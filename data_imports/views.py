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
    ImportBatchSerializer,
    ImportRecordResolutionSerializer,
    ImportReviewDetailSerializer,
    ImportReviewRecordSerializer,
    ImportRecordPreviewSerializer,
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
from data_imports.adapters.membership_form import MembershipFormStructureError
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
