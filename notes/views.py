from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import generics, serializers, status
from rest_framework.exceptions import APIException, NotFound
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from audit.models import AuditEvent
from audit.services import record_audit_event
from notes.models import InternalNote
from notes.serializers import (
    EmptyRequestSerializer,
    InternalNoteArchiveSerializer,
    InternalNoteCreateSerializer,
    InternalNoteSerializer,
    InternalNoteUpdateSerializer,
    NotesRecordStateQuerySerializer,
    PaginatedInternalNoteListSerializer,
)
from people.models import Person
from staff_access.models import StaffRole
from staff_access.permissions import HasActiveStaffRoleCodes


class HasNotesAccess(HasActiveStaffRoleCodes):
    required_role_codes = (
        StaffRole.CRM_ADMIN,
        StaffRole.CRM_MANAGER,
    )


class NotesPagination(PageNumberPagination):
    page_size = 25
    page_size_query_param = "page_size"
    max_page_size = 100

    def get_page_size(self, request):
        params = getattr(request, "_validated_notes_query_params", {})
        return params.get("page_size", self.page_size)


class NotesConflict(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "Note lifecycle change cannot be applied."
    default_code = "note_conflict"


def get_note_list_queryset_for_person(person):
    return InternalNote.objects.select_related("created_by", "archived_by").filter(person=person)


def record_note_event(*, action, actor_user, note, person, metadata=None, changes=None):
    event_metadata = {"person_id": str(person.id)}
    if metadata:
        event_metadata.update(metadata)
    record_audit_event(
        action=action,
        actor_user=actor_user,
        entity_type="InternalNote",
        entity_id=note.id,
        metadata=event_metadata,
        changes=changes or {},
    )


class BusinessPersonNotesMixin:
    def get_business_person_or_404(self, *, select_for_update=False):
        queryset = Person.objects.business()
        if select_for_update:
            queryset = queryset.select_for_update()
        person = queryset.filter(pk=self.kwargs["person_id"]).first()
        if person is None:
            raise NotFound("Not found.")
        return person

    def get_note_or_404(self, *, person, select_for_update=False):
        queryset = get_note_list_queryset_for_person(person)
        if select_for_update:
            queryset = queryset.select_for_update()
        note = queryset.filter(pk=self.kwargs["note_id"]).first()
        if note is None:
            raise NotFound("Not found.")
        return note


class PersonNoteListView(BusinessPersonNotesMixin, generics.GenericAPIView):
    serializer_class = InternalNoteSerializer
    pagination_class = NotesPagination

    def get_permissions(self):
        return [IsAuthenticated(), HasNotesAccess()]

    @extend_schema(
        operation_id="people_notes_list",
        summary="List CRM Person internal notes",
        description=(
            "Returns a paginated collection of sensitive internal notes for a single CRM-visible BUSINESS Person. "
            "Only CRM_ADMIN and CRM_MANAGER may access notes. CRM_VIEWER is forbidden. "
            "Archived BUSINESS people remain readable by direct ID. "
            "TECHNICAL people are outside the CRM Notes domain and return 404."
        ),
        parameters=[
            OpenApiParameter(
                name="person_id",
                type=int,
                location=OpenApiParameter.PATH,
                description="Primary key of a CRM-visible BUSINESS Person.",
                required=True,
            ),
            NotesRecordStateQuerySerializer,
        ],
        responses={
            200: PaginatedInternalNoteListSerializer,
            400: OpenApiResponse(description="Invalid query parameter value."),
            401: OpenApiResponse(description="Authentication credentials were not provided."),
            403: OpenApiResponse(description="You do not have a permitted active staff role."),
            404: OpenApiResponse(description="No BUSINESS Person matches the supplied ID within the CRM Notes domain."),
        },
        tags=["Notes"],
        examples=[
            OpenApiExample(
                "Active notes page",
                value={
                    "count": 1,
                    "next": None,
                    "previous": None,
                    "results": [
                        {
                            "id": 7,
                            "body": "Met during partner outreach and requested follow-up next quarter.",
                            "created_by": {"id": 3, "email": "manager@example.com"},
                            "created_at": "2026-08-31T15:00:00Z",
                            "updated_at": "2026-08-31T15:00:00Z",
                            "archived_at": None,
                            "archived_by": None,
                            "archive_reason": "",
                        }
                    ],
                },
                response_only=True,
            )
        ],
    )
    def get(self, request, *args, **kwargs):
        self.validated_query_params = self.get_validated_query_params()
        request._validated_notes_query_params = self.validated_query_params
        person = self.get_business_person_or_404()
        queryset = self.apply_record_state(
            get_note_list_queryset_for_person(person),
            self.validated_query_params["record_state"],
        )
        page = self.paginate_queryset(queryset.order_by("-created_at", "-id"))
        serializer = self.get_serializer(page, many=True)
        return self.get_paginated_response(serializer.data)

    @extend_schema(
        operation_id="people_notes_create",
        summary="Create CRM Person internal note",
        description=(
            "Creates a new sensitive internal note for an active CRM-visible BUSINESS Person. "
            "Only CRM_ADMIN and CRM_MANAGER may create notes. "
            "Archived BUSINESS people are readable but not writable and return 409."
        ),
        request=InternalNoteCreateSerializer,
        parameters=[
            OpenApiParameter(
                name="person_id",
                type=int,
                location=OpenApiParameter.PATH,
                description="Primary key of an active CRM-visible BUSINESS Person.",
                required=True,
            )
        ],
        responses={
            201: InternalNoteSerializer,
            400: OpenApiResponse(description="Invalid request body or unsupported fields."),
            401: OpenApiResponse(description="Authentication credentials were not provided."),
            403: OpenApiResponse(description="You do not have a permitted active staff role."),
            404: OpenApiResponse(description="No CRM-visible BUSINESS Person matches the supplied ID."),
            409: OpenApiResponse(description="Archived BUSINESS people cannot receive note mutations."),
        },
        tags=["Notes"],
        examples=[
            OpenApiExample(
                "Create note request",
                value={"body": "Spoke after the chamber event. Interested in advisory opportunities."},
                request_only=True,
            )
        ],
    )
    def post(self, request, *args, **kwargs):
        input_serializer = InternalNoteCreateSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)

        with transaction.atomic():
            person = self.get_business_person_or_404(select_for_update=True)
            if person.archived_at is not None:
                raise NotesConflict("Archived people cannot receive note changes.")

            note = InternalNote(
                person=person,
                body=input_serializer.validated_data["body"],
                created_by=request.user,
            )
            try:
                note.full_clean()
            except DjangoValidationError as error:
                raise serializers.ValidationError(error.message_dict)
            note.save()

            record_note_event(
                action=AuditEvent.Action.NOTE_CREATED,
                actor_user=request.user,
                note=note,
                person=person,
                changes={"created": {"from": False, "to": True}},
            )

        serializer = self.get_serializer(note)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    def paginate_queryset(self, queryset):
        paginator = self.paginator
        if paginator is None:
            return None
        return paginator.paginate_queryset(queryset, self.request, view=self)

    def get_paginated_response(self, data):
        return self.paginator.get_paginated_response(data)

    @property
    def paginator(self):
        if not hasattr(self, "_paginator"):
            if self.pagination_class is None:
                self._paginator = None
            else:
                self._paginator = self.pagination_class()
        return self._paginator

    def get_validated_query_params(self):
        serializer = NotesRecordStateQuerySerializer(data=self.request.query_params)
        serializer.is_valid(raise_exception=True)
        return serializer.validated_data

    def apply_record_state(self, queryset, record_state):
        if record_state == NotesRecordStateQuerySerializer.RECORD_STATE_ARCHIVED:
            return queryset.archived()
        if record_state == NotesRecordStateQuerySerializer.RECORD_STATE_ALL:
            return queryset
        return queryset.active()


class PersonNoteUpdateView(BusinessPersonNotesMixin, generics.GenericAPIView):
    serializer_class = InternalNoteSerializer
    permission_classes = [IsAuthenticated, HasNotesAccess]

    @extend_schema(
        operation_id="people_notes_partial_update",
        summary="Edit CRM Person internal note",
        description=(
            "Updates the plain-text body of an active internal note for an active CRM-visible BUSINESS Person. "
            "Archived notes use dedicated archive/restore lifecycle endpoints and cannot be edited directly."
        ),
        request=InternalNoteUpdateSerializer,
        parameters=[
            OpenApiParameter(
                name="person_id",
                type=int,
                location=OpenApiParameter.PATH,
                description="Primary key of a CRM-visible BUSINESS Person.",
                required=True,
            ),
            OpenApiParameter(
                name="note_id",
                type=int,
                location=OpenApiParameter.PATH,
                description="Primary key of the InternalNote belonging to that Person.",
                required=True,
            ),
        ],
        responses={
            200: InternalNoteSerializer,
            400: OpenApiResponse(description="Invalid request body or unsupported fields."),
            401: OpenApiResponse(description="Authentication credentials were not provided."),
            403: OpenApiResponse(description="You do not have a permitted active staff role."),
            404: OpenApiResponse(description="No matching CRM-visible BUSINESS Person or note exists."),
            409: OpenApiResponse(description="Archived people or archived notes cannot be edited."),
        },
        tags=["Notes"],
    )
    def patch(self, request, *args, **kwargs):
        input_serializer = InternalNoteUpdateSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)

        with transaction.atomic():
            person = self.get_business_person_or_404(select_for_update=True)
            if person.archived_at is not None:
                raise NotesConflict("Archived people cannot receive note changes.")

            note = self.get_note_or_404(person=person, select_for_update=True)
            if note.archived_at is not None:
                raise NotesConflict("Archived notes cannot be edited.")

            original_body = note.body
            note.body = input_serializer.validated_data["body"]
            try:
                note.full_clean()
            except DjangoValidationError as error:
                raise serializers.ValidationError(error.message_dict)
            note.save(update_fields=["body", "updated_at"])

            if note.body != original_body:
                record_note_event(
                    action=AuditEvent.Action.NOTE_UPDATED,
                    actor_user=request.user,
                    note=note,
                    person=person,
                    metadata={"body_changed": True},
                    changes={"body": {"changed": True}},
                )

        serializer = self.get_serializer(note)
        return Response(serializer.data, status=status.HTTP_200_OK)


class PersonNoteArchiveView(BusinessPersonNotesMixin, generics.GenericAPIView):
    serializer_class = InternalNoteSerializer
    permission_classes = [IsAuthenticated, HasNotesAccess]

    @extend_schema(
        operation_id="people_notes_archive",
        summary="Archive CRM Person internal note",
        description=(
            "Archives an active internal note for an active CRM-visible BUSINESS Person. "
            "The note body remains on the InternalNote row and is not copied into AuditEvent."
        ),
        request=InternalNoteArchiveSerializer,
        parameters=[
            OpenApiParameter(
                name="person_id",
                type=int,
                location=OpenApiParameter.PATH,
                description="Primary key of a CRM-visible BUSINESS Person.",
                required=True,
            ),
            OpenApiParameter(
                name="note_id",
                type=int,
                location=OpenApiParameter.PATH,
                description="Primary key of the InternalNote belonging to that Person.",
                required=True,
            ),
        ],
        responses={
            200: InternalNoteSerializer,
            400: OpenApiResponse(description="Invalid request body or unsupported fields."),
            401: OpenApiResponse(description="Authentication credentials were not provided."),
            403: OpenApiResponse(description="You do not have a permitted active staff role."),
            404: OpenApiResponse(description="No matching CRM-visible BUSINESS Person or note exists."),
            409: OpenApiResponse(description="Archived people cannot be mutated and archived notes cannot be archived twice."),
        },
        tags=["Notes"],
    )
    def post(self, request, *args, **kwargs):
        input_serializer = InternalNoteArchiveSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)

        with transaction.atomic():
            person = self.get_business_person_or_404(select_for_update=True)
            if person.archived_at is not None:
                raise NotesConflict("Archived people cannot receive note changes.")

            note = self.get_note_or_404(person=person, select_for_update=True)
            if note.archived_at is not None:
                raise NotesConflict("This note is already archived.")

            archive_reason = input_serializer.validated_data.get("archive_reason", "")
            note.archive(archived_by=request.user, archive_reason=archive_reason)

            record_note_event(
                action=AuditEvent.Action.NOTE_ARCHIVED,
                actor_user=request.user,
                note=note,
                person=person,
                changes={"archived": {"from": False, "to": True}},
            )

        serializer = self.get_serializer(note)
        return Response(serializer.data, status=status.HTTP_200_OK)


class PersonNoteRestoreView(BusinessPersonNotesMixin, generics.GenericAPIView):
    serializer_class = InternalNoteSerializer
    permission_classes = [IsAuthenticated, HasNotesAccess]

    @extend_schema(
        operation_id="people_notes_restore",
        summary="Restore CRM Person internal note",
        description=(
            "Restores an archived internal note for an active CRM-visible BUSINESS Person. "
            "Restore clears archived_at, archived_by, and archive_reason on InternalNote."
        ),
        request=EmptyRequestSerializer,
        parameters=[
            OpenApiParameter(
                name="person_id",
                type=int,
                location=OpenApiParameter.PATH,
                description="Primary key of a CRM-visible BUSINESS Person.",
                required=True,
            ),
            OpenApiParameter(
                name="note_id",
                type=int,
                location=OpenApiParameter.PATH,
                description="Primary key of the InternalNote belonging to that Person.",
                required=True,
            ),
        ],
        responses={
            200: InternalNoteSerializer,
            400: OpenApiResponse(description="Unexpected request body fields were supplied."),
            401: OpenApiResponse(description="Authentication credentials were not provided."),
            403: OpenApiResponse(description="You do not have a permitted active staff role."),
            404: OpenApiResponse(description="No matching CRM-visible BUSINESS Person or note exists."),
            409: OpenApiResponse(description="Archived people cannot be mutated and active notes cannot be restored."),
        },
        tags=["Notes"],
    )
    def post(self, request, *args, **kwargs):
        input_serializer = EmptyRequestSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)

        with transaction.atomic():
            person = self.get_business_person_or_404(select_for_update=True)
            if person.archived_at is not None:
                raise NotesConflict("Archived people cannot receive note changes.")

            note = self.get_note_or_404(person=person, select_for_update=True)
            if note.archived_at is None:
                raise NotesConflict("This note is already active.")

            note.restore()

            record_note_event(
                action=AuditEvent.Action.NOTE_RESTORED,
                actor_user=request.user,
                note=note,
                person=person,
                changes={"archived": {"from": True, "to": False}},
            )

        serializer = self.get_serializer(note)
        return Response(serializer.data, status=status.HTTP_200_OK)
