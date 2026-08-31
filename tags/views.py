from django.db import IntegrityError, transaction
from django.utils import timezone
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import generics, status
from rest_framework.exceptions import APIException, NotFound
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.serializers import ValidationError

from audit.models import AuditEvent
from audit.services import record_audit_event
from people.models import Person
from staff_access.models import StaffRole
from staff_access.permissions import HasActiveStaffRoleCodes
from tags.models import PersonTag, Tag
from tags.serializers import AssignTagSerializer, EmptyRequestSerializer, TagSummarySerializer


class HasTagAccess(HasActiveStaffRoleCodes):
    required_role_codes = (
        StaffRole.CRM_ADMIN,
        StaffRole.CRM_MANAGER,
        StaffRole.CRM_VIEWER,
    )


class HasTagWriteAccess(HasActiveStaffRoleCodes):
    required_role_codes = (
        StaffRole.CRM_ADMIN,
        StaffRole.CRM_MANAGER,
    )


class TagAssignmentConflict(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "Tag assignment cannot be written for this person."
    default_code = "tag_assignment_conflict"


class TagListView(generics.ListAPIView):
    serializer_class = TagSummarySerializer
    permission_classes = [IsAuthenticated, HasTagAccess]
    pagination_class = None

    @extend_schema(
        operation_id="tags_list",
        summary="List active Tags",
        description=(
            "Returns the active canonical Tag taxonomy for internal CRM usage. "
            "Only active Tag definitions are returned. Results are ordered by display_order, name, and id. "
            "Tags are internal staff classification data, not member-facing profile fields. "
            "Session authentication and an active CRM staff role are required."
        ),
        responses={
            200: TagSummarySerializer(many=True),
            401: OpenApiResponse(description="Authentication credentials were not provided."),
            403: OpenApiResponse(description="You do not have a permitted active staff role."),
        },
        tags=["Tags"],
        examples=[
            OpenApiExample(
                "Tag list",
                value=[
                    {"id": 1, "name": "Potential Speaker", "slug": "potential-speaker"},
                    {"id": 8, "name": "VIP", "slug": "vip"},
                ],
                response_only=True,
            )
        ],
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        return Tag.objects.filter(is_active=True)


class PersonTagListView(generics.GenericAPIView):
    serializer_class = TagSummarySerializer
    pagination_class = None

    def get_permissions(self):
        permission_classes = [IsAuthenticated, HasTagAccess]
        if self.request.method == "POST":
            permission_classes = [IsAuthenticated, HasTagWriteAccess]
        return [permission() for permission in permission_classes]

    @extend_schema(
        operation_id="people_tags_list",
        summary="List CRM Person Tags",
        description=(
            "Returns active Tag definitions assigned to a single CRM-visible BUSINESS Person. "
            "Only active PersonTag assignments whose Tag definition is also active are returned. "
            "Archived BUSINESS people remain retrievable by direct ID. "
            "TECHNICAL people are outside the CRM People domain and return 404. "
            "Tags are internal CRM classification data, so lifecycle audit metadata is intentionally omitted from this compact read."
        ),
        parameters=[
            OpenApiParameter(
                name="person_id",
                type=int,
                location=OpenApiParameter.PATH,
                description="Primary key of a CRM-visible BUSINESS Person.",
                required=True,
            )
        ],
        responses={
            200: TagSummarySerializer(many=True),
            401: OpenApiResponse(description="Authentication credentials were not provided."),
            403: OpenApiResponse(description="You do not have a permitted active staff role."),
            404: OpenApiResponse(
                description="No BUSINESS Person matches the supplied ID within the CRM People domain."
            ),
        },
        tags=["Tags"],
        examples=[
            OpenApiExample(
                "Person tag list",
                value=[
                    {"id": 2, "name": "Potential Mentor", "slug": "potential-mentor"},
                    {"id": 8, "name": "VIP", "slug": "vip"},
                ],
                response_only=True,
            )
        ],
    )
    def get(self, request, *args, **kwargs):
        serializer = TagSummarySerializer(self.get_queryset(), many=True)
        return Response(serializer.data)

    @extend_schema(
        operation_id="people_tags_create",
        summary="Assign CRM Person Tag",
        description=(
            "Creates a lifecycle-aware PersonTag relationship for an active CRM-visible BUSINESS Person. "
            "If the relationship does not exist, a new PersonTag row is created and returns 201. "
            "If the same PersonTag exists but is inactive, the same row is reactivated and returns 200. "
            "If the PersonTag is already active, the request returns 409. "
            "Only active canonical Tag definitions may be assigned or reactivated. "
            "Archived BUSINESS people return 409. "
            "TECHNICAL people and nonexistent people return 404. "
            "Lifecycle attribution is controlled entirely by the backend."
        ),
        request=AssignTagSerializer,
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
            200: TagSummarySerializer,
            201: TagSummarySerializer,
            400: OpenApiResponse(
                description="Invalid request body, unsupported fields, nonexistent tag, or inactive tag."
            ),
            401: OpenApiResponse(description="Authentication credentials were not provided."),
            403: OpenApiResponse(description="You do not have a permitted active staff role."),
            404: OpenApiResponse(description="No CRM-visible BUSINESS Person matches the supplied ID."),
            409: OpenApiResponse(
                description="The person is archived or the tag assignment is already active."
            ),
        },
        tags=["Tags"],
        examples=[
            OpenApiExample(
                "Assign tag request",
                value={"tag": 8},
                request_only=True,
            ),
            OpenApiExample(
                "Assign tag response",
                value={"id": 8, "name": "VIP", "slug": "vip"},
                response_only=True,
            ),
        ],
    )
    def post(self, request, *args, **kwargs):
        input_serializer = AssignTagSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)

        tag_id = input_serializer.validated_data["tag"]
        actor = request.user

        with transaction.atomic():
            person = self.get_business_person_or_404(select_for_update=True)

            if person.archived_at is not None:
                raise TagAssignmentConflict("Archived people cannot receive tag changes.")

            tag = Tag.objects.filter(pk=tag_id).first()
            if tag is None:
                raise ValidationError({"tag": ["Tag does not exist."]})
            if not tag.is_active:
                raise ValidationError({"tag": ["Only active tags may be assigned."]})

            person_tag = (
                PersonTag.objects.select_for_update()
                .filter(person=person, tag=tag)
                .first()
            )

            if person_tag is None:
                try:
                    person_tag = PersonTag.objects.create(
                        person=person,
                        tag=tag,
                        assigned_by=actor,
                    )
                except IntegrityError:
                    raise TagAssignmentConflict("This tag is already assigned to the person.")

                record_audit_event(
                    action=AuditEvent.Action.TAG_ASSIGNED,
                    actor_user=actor,
                    entity_type="PersonTag",
                    entity_id=person_tag.id,
                    changes={"is_active": {"from": None, "to": True}},
                    metadata={"person_id": str(person.id), "tag_id": str(tag.id)},
                )
                response_status = status.HTTP_201_CREATED
            elif person_tag.is_active:
                raise TagAssignmentConflict("This tag is already assigned to the person.")
            else:
                person_tag.is_active = True
                person_tag.assigned_by = actor
                person_tag.assigned_at = timezone.now()
                person_tag.removed_by = None
                person_tag.removed_at = None
                person_tag.save()

                record_audit_event(
                    action=AuditEvent.Action.TAG_REACTIVATED,
                    actor_user=actor,
                    entity_type="PersonTag",
                    entity_id=person_tag.id,
                    changes={"is_active": {"from": False, "to": True}},
                    metadata={"person_id": str(person.id), "tag_id": str(tag.id)},
                )
                response_status = status.HTTP_200_OK

        serializer = TagSummarySerializer(person_tag.tag)
        return Response(serializer.data, status=response_status)

    def get_queryset(self):
        person = self.get_business_person_or_404()
        return Tag.objects.filter(
            person_tags__person=person,
            person_tags__is_active=True,
            is_active=True,
        ).order_by("display_order", "name", "id")

    def get_business_person_or_404(self, *, select_for_update=False):
        queryset = Person.objects.business()
        if select_for_update:
            queryset = queryset.select_for_update()
        person = queryset.filter(pk=self.kwargs["person_id"]).first()
        if person is None:
            raise NotFound("Not found.")
        return person


class PersonTagRemoveView(generics.GenericAPIView):
    serializer_class = EmptyRequestSerializer
    permission_classes = [IsAuthenticated, HasTagWriteAccess]

    @extend_schema(
        operation_id="people_tags_remove",
        summary="Remove CRM Person Tag",
        description=(
            "Marks an existing active PersonTag inactive for an active CRM-visible BUSINESS Person. "
            "Removal preserves the PersonTag row and records removed_by and removed_at. "
            "If the same PersonTag is assigned again later, the existing row is reactivated rather than duplicated. "
            "Archived BUSINESS people return 409. "
            "TECHNICAL people and nonexistent people return 404. "
            "An already inactive PersonTag returns 409. "
            "Removal does not require the canonical Tag definition itself to still be active."
        ),
        parameters=[
            OpenApiParameter(
                name="person_id",
                type=int,
                location=OpenApiParameter.PATH,
                description="Primary key of an active CRM-visible BUSINESS Person.",
                required=True,
            ),
            OpenApiParameter(
                name="tag_id",
                type=int,
                location=OpenApiParameter.PATH,
                description="Primary key of the assigned Tag to remove.",
                required=True,
            ),
        ],
        responses={
            204: OpenApiResponse(description="The tag assignment was marked inactive."),
            401: OpenApiResponse(description="Authentication credentials were not provided."),
            403: OpenApiResponse(description="You do not have a permitted active staff role."),
            404: OpenApiResponse(description="No matching CRM-visible BUSINESS Person or tag assignment exists."),
            409: OpenApiResponse(
                description="The person is archived or the tag assignment is already inactive."
            ),
        },
        tags=["Tags"],
    )
    def post(self, request, *args, **kwargs):
        if len(request.data) != 0:
            raise ValidationError({"non_field_errors": ["This endpoint does not accept a request body."]})

        with transaction.atomic():
            person = self.get_business_person_or_404(select_for_update=True)

            if person.archived_at is not None:
                raise TagAssignmentConflict("Archived people cannot receive tag changes.")

            person_tag = (
                PersonTag.objects.select_for_update()
                .filter(person=person, tag_id=self.kwargs["tag_id"])
                .first()
            )
            if person_tag is None:
                raise NotFound("Not found.")
            if not person_tag.is_active:
                raise TagAssignmentConflict("This tag assignment is already inactive.")

            person_tag.is_active = False
            person_tag.removed_by = request.user
            person_tag.removed_at = timezone.now()
            person_tag.save()

            record_audit_event(
                action=AuditEvent.Action.TAG_REMOVED,
                actor_user=request.user,
                entity_type="PersonTag",
                entity_id=person_tag.id,
                changes={"is_active": {"from": True, "to": False}},
                metadata={"person_id": str(person.id), "tag_id": str(person_tag.tag_id)},
            )

        return Response(status=status.HTTP_204_NO_CONTENT)

    def get_business_person_or_404(self, *, select_for_update=False):
        queryset = Person.objects.business()
        if select_for_update:
            queryset = queryset.select_for_update()
        person = queryset.filter(pk=self.kwargs["person_id"]).first()
        if person is None:
            raise NotFound("Not found.")
        return person
