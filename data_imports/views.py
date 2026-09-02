import logging
from zipfile import BadZipFile

from django.db.models import QuerySet
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from openpyxl.utils.exceptions import InvalidFileException
from rest_framework import generics, serializers, status
from rest_framework.exceptions import APIException, NotFound
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from data_imports.models import ImportBatch, ImportRecord
from data_imports.serializers import (
    IMPORT_BATCH_COUNT_ANNOTATIONS,
    ImportBatchImportResponseSerializer,
    ImportBatchSerializer,
    ImportRecordResolutionSerializer,
    ImportReviewDetailSerializer,
    ImportReviewRecordSerializer,
    ImportRecordPreviewSerializer,
    EventbriteUploadSerializer,
    PaginatedImportRecordPreviewSerializer,
    MembershipFormUploadSerializer,
    PaginatedImportReviewQueueSerializer,
    candidate_people_for_records,
)
from data_imports.services.reconciliation import (
    ReconciliationConflict,
    ReconciliationValidationError,
    resolve_import_record,
)
from data_imports.services.membership_form_upload import (
    MembershipFormAnalysisError,
    ingest_and_analyze_membership_form,
)
from data_imports.services.membership_form_import import (
    ImportBatchNotImportable,
    ImportBatchPreflightError,
    StaleCreateNewIdentityReview,
    import_membership_form_batch,
)
from data_imports.adapters.membership_form import MembershipFormStructureError
from data_imports.adapters.eventbrite import EventbriteStructureError
from data_imports.services.eventbrite_ingestion import ingest_eventbrite_workbook
from data_imports.services.identity_analysis import analyze_import_batch
from staff_access.models import StaffRole
from staff_access.permissions import HasActiveStaffRoleCodes


logger = logging.getLogger(__name__)


class HasImportReconciliationAccess(HasActiveStaffRoleCodes):
    required_role_codes = (StaffRole.CRM_ADMIN,)


class ImportReviewPagination(PageNumberPagination):
    page_size = 25
    page_size_query_param = "page_size"
    max_page_size = 100


class ImportReconciliationConflict(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "Import record reconciliation cannot be completed."
    default_code = "import_reconciliation_conflict"


class ImportBatchImportConflict(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "Import batch cannot be imported in its current state."
    default_code = "import_batch_import_conflict"


class ImportBatchImportStaleIdentityConflict(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "The possible CRM matches have changed since this identity decision was made. Review the record again before adding it to the CRM."
    default_code = "import_batch_import_stale_identity_conflict"


class ImportBatchAnalysisConflict(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "Import batch is not eligible for identity analysis."
    default_code = "import_batch_analysis_conflict"


class ImportBatchQuerysetMixin:
    @staticmethod
    def get_batch_queryset() -> QuerySet:
        return ImportBatch.objects.annotate(**IMPORT_BATCH_COUNT_ANNOTATIONS)

    def get_batch_or_404(self):
        batch = self.get_batch_queryset().filter(pk=self.kwargs["batch_id"]).first()
        if batch is None:
            raise NotFound("Not found.")
        return batch


class ImportBatchListView(ImportBatchQuerysetMixin, generics.ListAPIView):
    serializer_class = ImportBatchSerializer
    permission_classes = [IsAuthenticated, HasImportReconciliationAccess]

    @extend_schema(
        operation_id="imports_list",
        summary="List historical import batches",
        description="Lists staged historical import batches and reconciliation summary counts. Only CRM_ADMIN may access import reconciliation.",
        responses={
            200: ImportBatchSerializer(many=True),
            401: OpenApiResponse(description="Authentication credentials were not provided."),
            403: OpenApiResponse(description="You do not have the CRM_ADMIN staff role."),
        },
        tags=["Historical Imports"],
    )
    def get_queryset(self):
        return self.get_batch_queryset()


class MembershipFormUploadView(ImportBatchQuerysetMixin, generics.GenericAPIView):
    permission_classes = [IsAuthenticated, HasImportReconciliationAccess]
    parser_classes = [MultiPartParser, FormParser]

    @extend_schema(
        operation_id="imports_membership_form_upload",
        summary="Upload and analyze a Membership Form workbook",
        description=(
            "Accepts one .xlsx Membership Form workbook up to 10 MiB, stages it through the existing import service, "
            "and runs existing deterministic identity analysis. Only CRM_ADMIN may upload. "
            "This never creates or updates authoritative CRM records."
        ),
        request=MembershipFormUploadSerializer,
        responses={
            201: ImportBatchSerializer,
            400: OpenApiResponse(description="Missing, invalid, empty, oversized, corrupt, or structurally invalid workbook."),
            401: OpenApiResponse(description="Authentication credentials were not provided."),
            403: OpenApiResponse(description="You do not have the CRM_ADMIN staff role."),
            500: OpenApiResponse(description="The workbook could not be staged or analyzed."),
        },
        tags=["Historical Imports"],
    )
    def post(self, request, *args, **kwargs):
        input_serializer = MembershipFormUploadSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        uploaded_file = input_serializer.validated_data["file"]

        try:
            batch = ingest_and_analyze_membership_form(uploaded_file=uploaded_file, created_by=request.user)
        except MembershipFormStructureError:
            raise serializers.ValidationError({"file": ["The workbook does not have the required Membership Form structure."]})
        except (BadZipFile, InvalidFileException, OSError, ValueError):
            raise serializers.ValidationError({"file": ["The uploaded file is not a readable .xlsx workbook."]})
        except MembershipFormAnalysisError:
            logger.error("Membership Form analysis failed after staging.")
            raise APIException("The workbook could not be analyzed safely.")
        except Exception:
            logger.error("Membership Form staging failed.")
            raise APIException("The workbook could not be staged safely.")

        batch = self.get_batch_queryset().get(pk=batch.pk)
        logger.info(
            "Membership Form import staged and analyzed: batch_id=%s size=%s status=%s total_count=%s",
            batch.id,
            uploaded_file.size,
            batch.status,
            batch.total_count,
        )
        return Response(ImportBatchSerializer(batch).data, status=status.HTTP_201_CREATED)


class EventbriteUploadView(ImportBatchQuerysetMixin, generics.GenericAPIView):
    permission_classes = [IsAuthenticated, HasImportReconciliationAccess]
    parser_classes = [MultiPartParser, FormParser]

    @extend_schema(
        operation_id="imports_eventbrite_upload",
        summary="Upload and stage an Eventbrite workbook",
        description=(
            "Accepts one .xlsx Eventbrite workbook up to 10 MiB and stages normalized source rows for later identity analysis. "
            "Only CRM_ADMIN may upload. This does not create or update Person, Membership, Event, EventParticipation, "
            "or ExternalEventReference records."
        ),
        request=EventbriteUploadSerializer,
        responses={
            201: ImportBatchSerializer,
            400: OpenApiResponse(description="Missing, invalid, empty, oversized, corrupt, or structurally invalid workbook."),
            401: OpenApiResponse(description="Authentication credentials were not provided."),
            403: OpenApiResponse(description="You do not have the CRM_ADMIN staff role."),
            500: OpenApiResponse(description="The workbook could not be staged."),
        },
        tags=["Historical Imports"],
    )
    def post(self, request, *args, **kwargs):
        input_serializer = EventbriteUploadSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        uploaded_file = input_serializer.validated_data["file"]

        try:
            batch = ingest_eventbrite_workbook(
                workbook_bytes=uploaded_file.read(),
                source_filename=uploaded_file.name,
                created_by=request.user,
            )
        except EventbriteStructureError:
            raise serializers.ValidationError({"file": ["The workbook does not have the required Eventbrite structure."]})
        except (BadZipFile, InvalidFileException, OSError, ValueError):
            raise serializers.ValidationError({"file": ["The uploaded file is not a readable .xlsx workbook."]})
        except Exception:
            logger.error("Eventbrite workbook staging failed.")
            raise APIException("The workbook could not be staged safely.")

        batch = self.get_batch_queryset().get(pk=batch.pk)
        logger.info(
            "Eventbrite import staged: batch_id=%s size=%s status=%s total_count=%s",
            batch.id,
            uploaded_file.size,
            batch.status,
            batch.total_count,
        )
        return Response(ImportBatchSerializer(batch).data, status=status.HTTP_201_CREATED)


class ImportBatchAnalyzeView(ImportBatchQuerysetMixin, generics.GenericAPIView):
    serializer_class = ImportBatchSerializer
    permission_classes = [IsAuthenticated, HasImportReconciliationAccess]

    @extend_schema(
        operation_id="imports_eventbrite_analyze",
        summary="Analyze a staged Eventbrite batch",
        description=(
            "Runs backend-authoritative buyer-to-Person identity analysis for an EVENTBRITE batch in STAGED status. "
            "It uses only normalized Person identity fields and never creates or updates CRM or Events-domain records."
        ),
        parameters=[OpenApiParameter(name="batch_id", type=int, location=OpenApiParameter.PATH, required=True)],
        responses={
            200: ImportBatchSerializer,
            401: OpenApiResponse(description="Authentication credentials were not provided."),
            403: OpenApiResponse(description="You do not have the CRM_ADMIN staff role."),
            404: OpenApiResponse(description="Import batch was not found."),
            409: OpenApiResponse(description="Import batch is not an Eventbrite batch in STAGED status."),
        },
        tags=["Historical Imports"],
    )
    def post(self, request, *args, **kwargs):
        batch = self.get_batch_or_404()
        try:
            batch = analyze_import_batch(batch)
        except ValueError:
            raise ImportBatchAnalysisConflict
        batch = self.get_batch_queryset().get(pk=batch.pk)
        return Response(ImportBatchSerializer(batch).data)


class ImportBatchDetailView(ImportBatchQuerysetMixin, generics.GenericAPIView):
    serializer_class = ImportBatchSerializer
    permission_classes = [IsAuthenticated, HasImportReconciliationAccess]

    @extend_schema(
        operation_id="imports_retrieve",
        summary="Retrieve a historical import batch",
        description="Returns import batch metadata and reconciliation summary counts without inlining staged records.",
        parameters=[OpenApiParameter(name="batch_id", type=int, location=OpenApiParameter.PATH, required=True)],
        responses={
            200: ImportBatchSerializer,
            401: OpenApiResponse(description="Authentication credentials were not provided."),
            403: OpenApiResponse(description="You do not have the CRM_ADMIN staff role."),
            404: OpenApiResponse(description="Import batch was not found."),
        },
        tags=["Historical Imports"],
    )
    def get(self, request, *args, **kwargs):
        return Response(self.get_serializer(self.get_batch_or_404()).data)


class ImportBatchImportView(ImportBatchQuerysetMixin, generics.GenericAPIView):
    serializer_class = ImportBatchImportResponseSerializer
    permission_classes = [IsAuthenticated, HasImportReconciliationAccess]

    @extend_schema(
        operation_id="imports_membership_form_import",
        summary="Import a ready Membership Form batch",
        description=(
            "Synchronously performs the authoritative Membership Form import for a READY_FOR_IMPORT batch. "
            "Only CRM_ADMIN may invoke it. The response contains the canonical imported batch and a mutation summary; "
            "it never exposes staged source rows."
        ),
        parameters=[OpenApiParameter(name="batch_id", type=int, location=OpenApiParameter.PATH, required=True)],
        responses={
            200: ImportBatchImportResponseSerializer,
            401: OpenApiResponse(description="Authentication credentials were not provided."),
            403: OpenApiResponse(description="You do not have the CRM_ADMIN staff role."),
            404: OpenApiResponse(description="Import batch was not found."),
            409: OpenApiResponse(description="Import batch is not importable or reviewed identity evidence has become stale."),
        },
        tags=["Historical Imports"],
    )
    def post(self, request, *args, **kwargs):
        try:
            result = import_membership_form_batch(
                batch_id=self.kwargs["batch_id"],
                imported_by=request.user,
            )
        except ImportBatch.DoesNotExist:
            raise NotFound("Not found.")
        except StaleCreateNewIdentityReview:
            raise ImportBatchImportStaleIdentityConflict
        except (ImportBatchNotImportable, ImportBatchPreflightError):
            raise ImportBatchImportConflict

        batch = self.get_batch_queryset().get(pk=result.batch_id)
        return Response(
            self.get_serializer({"batch": batch, "result": result}).data,
            status=status.HTTP_200_OK,
        )


class ImportReviewQueueView(ImportBatchQuerysetMixin, generics.GenericAPIView):
    serializer_class = ImportReviewRecordSerializer
    permission_classes = [IsAuthenticated, HasImportReconciliationAccess]
    pagination_class = ImportReviewPagination

    @extend_schema(
        operation_id="imports_review_queue",
        summary="List records requiring identity review",
        description="Returns a paginated queue of REVIEW_REQUIRED records with normalized source fields and current analyzer candidate People. Only CRM_ADMIN may access it.",
        parameters=[OpenApiParameter(name="batch_id", type=int, location=OpenApiParameter.PATH, required=True)],
        responses={
            200: PaginatedImportReviewQueueSerializer,
            401: OpenApiResponse(description="Authentication credentials were not provided."),
            403: OpenApiResponse(description="You do not have the CRM_ADMIN staff role."),
            404: OpenApiResponse(description="Import batch was not found."),
        },
        tags=["Historical Imports"],
    )
    def get(self, request, *args, **kwargs):
        batch = self.get_batch_or_404()
        queryset = batch.records.filter(status=ImportRecord.Status.REVIEW_REQUIRED).order_by("source_row_identifier", "id")
        page = self.paginate_queryset(queryset)
        records = list(page) if page is not None else list(queryset)
        serializer = self.get_serializer(records, many=True, context={"candidate_people": candidate_people_for_records(records)})
        if page is not None:
            return self.get_paginated_response(serializer.data)
        return Response(serializer.data)

    @property
    def paginator(self):
        if not hasattr(self, "_paginator"):
            self._paginator = self.pagination_class()
        return self._paginator

    def paginate_queryset(self, queryset):
        return self.paginator.paginate_queryset(queryset, self.request, view=self)

    def get_paginated_response(self, data):
        return self.paginator.get_paginated_response(data)


class ImportRecordListView(ImportBatchQuerysetMixin, generics.GenericAPIView):
    serializer_class = ImportRecordPreviewSerializer
    permission_classes = [IsAuthenticated, HasImportReconciliationAccess]
    pagination_class = ImportReviewPagination

    @extend_schema(
        operation_id="imports_records_list",
        summary="Preview staged import record resolutions",
        description=(
            "Returns a paginated, read-only preview of every staged record in an import batch. "
            "Resolution method and status remain authoritative; this endpoint never commits or mutates CRM data. "
            "Only CRM_ADMIN may access it."
        ),
        parameters=[OpenApiParameter(name="batch_id", type=int, location=OpenApiParameter.PATH, required=True)],
        responses={
            200: PaginatedImportRecordPreviewSerializer,
            401: OpenApiResponse(description="Authentication credentials were not provided."),
            403: OpenApiResponse(description="You do not have the CRM_ADMIN staff role."),
            404: OpenApiResponse(description="Import batch was not found."),
        },
        tags=["Historical Imports"],
    )
    def get(self, request, *args, **kwargs):
        batch = self.get_batch_or_404()
        queryset = batch.records.select_related("resolved_person").order_by("source_row_identifier", "id")
        page = self.paginate_queryset(queryset)
        records = page if page is not None else queryset
        serializer = self.get_serializer(records, many=True)
        if page is not None:
            return self.get_paginated_response(serializer.data)
        return Response(serializer.data)

    @property
    def paginator(self):
        if not hasattr(self, "_paginator"):
            self._paginator = self.pagination_class()
        return self._paginator

    def paginate_queryset(self, queryset):
        return self.paginator.paginate_queryset(queryset, self.request, view=self)

    def get_paginated_response(self, data):
        return self.paginator.get_paginated_response(data)


class ImportReviewDetailView(ImportBatchQuerysetMixin, generics.GenericAPIView):
    serializer_class = ImportReviewDetailSerializer
    permission_classes = [IsAuthenticated, HasImportReconciliationAccess]

    @extend_schema(
        operation_id="imports_review_retrieve",
        summary="Retrieve one identity review record",
        description="Returns source context, normalized reconciliation fields, evidence, validation information, and current analyzer candidate People.",
        parameters=[
            OpenApiParameter(name="batch_id", type=int, location=OpenApiParameter.PATH, required=True),
            OpenApiParameter(name="record_id", type=int, location=OpenApiParameter.PATH, required=True),
        ],
        responses={
            200: ImportReviewDetailSerializer,
            401: OpenApiResponse(description="Authentication credentials were not provided."),
            403: OpenApiResponse(description="You do not have the CRM_ADMIN staff role."),
            404: OpenApiResponse(description="Import batch or review record was not found."),
        },
        tags=["Historical Imports"],
    )
    def get(self, request, *args, **kwargs):
        batch = self.get_batch_or_404()
        record = batch.records.select_related("batch").filter(pk=self.kwargs["record_id"]).first()
        if record is None:
            raise NotFound("Not found.")
        return Response(self.get_serializer(record, context={"candidate_people": candidate_people_for_records([record])}).data)


class ImportReviewResolveView(generics.GenericAPIView):
    serializer_class = ImportReviewDetailSerializer
    permission_classes = [IsAuthenticated, HasImportReconciliationAccess]

    @extend_schema(
        operation_id="imports_review_resolve",
        summary="Resolve one identity review record",
        description=(
            "Records a CRM_ADMIN decision only. SAME_PERSON requires an analyzer candidate BUSINESS Person. "
            "DIFFERENT_PERSON records a future create-new decision and does not create a Person. "
            "Email-involved identity evidence requires confirm_identity_override=true. "
            "This endpoint is concurrency-safe and never commits authoritative CRM mutations."
        ),
        request=ImportRecordResolutionSerializer,
        parameters=[
            OpenApiParameter(name="batch_id", type=int, location=OpenApiParameter.PATH, required=True),
            OpenApiParameter(name="record_id", type=int, location=OpenApiParameter.PATH, required=True),
        ],
        responses={
            200: ImportReviewDetailSerializer,
            400: OpenApiResponse(description="Invalid reconciliation action or payload."),
            401: OpenApiResponse(description="Authentication credentials were not provided."),
            403: OpenApiResponse(description="You do not have the CRM_ADMIN staff role."),
            404: OpenApiResponse(description="Import batch or review record was not found."),
            409: OpenApiResponse(description="Review record is no longer awaiting reconciliation."),
        },
        tags=["Historical Imports"],
    )
    def post(self, request, *args, **kwargs):
        input_serializer = ImportRecordResolutionSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        try:
            record = resolve_import_record(
                batch_id=self.kwargs["batch_id"],
                record_id=self.kwargs["record_id"],
                action=input_serializer.validated_data["resolution"],
                person_id=input_serializer.validated_data.get("person_id"),
                confirm_identity_override=input_serializer.validated_data.get("confirm_identity_override", False),
                reviewed_by=request.user,
            )
        except ImportBatch.DoesNotExist:
            raise NotFound("Not found.")
        except ImportRecord.DoesNotExist:
            raise NotFound("Not found.")
        except ReconciliationConflict as error:
            raise ImportReconciliationConflict(str(error))
        except ReconciliationValidationError as error:
            raise serializers.ValidationError({"detail": str(error)})

        return Response(
            self.get_serializer(record, context={"candidate_people": candidate_people_for_records([record])}).data,
            status=status.HTTP_200_OK,
        )
