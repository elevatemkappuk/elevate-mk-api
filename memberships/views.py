from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import generics, serializers, status
from rest_framework.exceptions import APIException, NotFound
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from audit.models import AuditEvent
from audit.services import record_audit_event
from memberships.models import Membership
from memberships.serializers import EndMembershipSerializer, MakeMembershipSerializer, MembershipSerializer
from people.models import Person
from staff_access.models import StaffRole
from staff_access.permissions import HasActiveStaffRoleCodes


class HasMembershipAccess(HasActiveStaffRoleCodes):
    required_role_codes = (
        StaffRole.CRM_ADMIN,
        StaffRole.CRM_MANAGER,
        StaffRole.CRM_VIEWER,
    )


class HasMembershipWriteAccess(HasActiveStaffRoleCodes):
    required_role_codes = (
        StaffRole.CRM_ADMIN,
        StaffRole.CRM_MANAGER,
    )


class MembershipConflict(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "Membership cannot be created for this person."
    default_code = "membership_conflict"


class PersonMembershipView(generics.GenericAPIView):
    serializer_class = MembershipSerializer
    permission_classes = [IsAuthenticated]

    def get_permissions(self):
        permission_classes = [IsAuthenticated, HasMembershipAccess]
        if self.request.method == "POST":
            permission_classes = [IsAuthenticated, HasMembershipWriteAccess]
        return [permission() for permission in permission_classes]

    @extend_schema(
        operation_id="people_membership_retrieve",
        summary="Retrieve CRM Person Membership",
        description=(
            "Returns the Membership subresource for a CRM-visible BUSINESS Person. "
            "Archived BUSINESS people remain readable by direct ID. "
            "A 404 response means either the Person is outside the CRM People domain, "
            "the Person does not exist, or the Person has no Membership."
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
            200: MembershipSerializer,
            401: OpenApiResponse(description="Authentication credentials were not provided."),
            403: OpenApiResponse(description="You do not have a permitted active staff role."),
            404: OpenApiResponse(
                description=(
                    "No Membership subresource exists for the supplied CRM-visible BUSINESS Person ID."
                )
            ),
        },
        tags=["People", "Membership"],
        examples=[
            OpenApiExample(
                "Membership detail",
                value={
                    "id": 5,
                    "status": "ACTIVE",
                    "joined_at": "2024-04-12",
                    "ended_at": None,
                    "membership_source": "WEBSITE_FORM",
                    "created_at": "2026-08-30T11:00:00Z",
                    "updated_at": "2026-08-30T11:00:00Z",
                },
                response_only=True,
            )
        ],
    )
    def get(self, request, *args, **kwargs):
        membership = self.get_membership_or_404()
        serializer = self.get_serializer(membership)
        return Response(serializer.data)

    @extend_schema(
        operation_id="people_membership_create",
        summary="Make Member for CRM Person",
        description=(
            "Creates the first Membership record for an existing CRM-visible BUSINESS Person. "
            "This is an explicit Make Member action, not a generic Membership create API. "
            "The backend forces status to ACTIVE, ended_at to null, and person from the route. "
            "Archived BUSINESS people are rejected for new membership creation. "
            "Existing ACTIVE or FORMER memberships return 409 because reactivation and rejoin history are not implemented."
        ),
        request=MakeMembershipSerializer,
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
            201: MembershipSerializer,
            400: OpenApiResponse(description="Invalid request body or unsupported field values."),
            401: OpenApiResponse(description="Authentication credentials were not provided."),
            403: OpenApiResponse(description="You do not have a permitted active staff role."),
            404: OpenApiResponse(
                description="No CRM-visible BUSINESS Person matches the supplied ID."
            ),
            409: OpenApiResponse(
                description="The person already has a membership, or the archived person cannot receive a new membership."
            ),
        },
        tags=["People", "Membership"],
        examples=[
            OpenApiExample(
                "Make member request",
                value={
                    "joined_at": "2024-04-12",
                    "membership_source": "STAFF",
                },
                request_only=True,
            ),
            OpenApiExample(
                "Make member response",
                value={
                    "id": 9,
                    "status": "ACTIVE",
                    "joined_at": "2024-04-12",
                    "ended_at": None,
                    "membership_source": "STAFF",
                    "created_at": "2026-08-30T12:00:00Z",
                    "updated_at": "2026-08-30T12:00:00Z",
                },
                response_only=True,
            ),
        ],
    )
    def post(self, request, *args, **kwargs):
        input_serializer = MakeMembershipSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)

        with transaction.atomic():
            person = self.get_business_person_or_404(select_for_update=True)

            if person.archived_at is not None:
                raise MembershipConflict("Archived people cannot receive a new membership.")

            if Membership.objects.filter(person=person).exists():
                raise MembershipConflict("This person already has a membership record.")

            joined_at = input_serializer.validated_data["joined_at"]
            membership_source = input_serializer.validated_data["membership_source"]

            try:
                membership = Membership.objects.create(
                    person=person,
                    status=Membership.Status.ACTIVE,
                    joined_at=joined_at,
                    membership_source=membership_source,
                )
            except IntegrityError:
                raise MembershipConflict("This person already has a membership record.")

            record_audit_event(
                action=AuditEvent.Action.MEMBERSHIP_CREATED,
                actor_user=request.user,
                entity_type="Membership",
                entity_id=membership.id,
                changes={
                    "status": {"from": None, "to": Membership.Status.ACTIVE},
                    "joined_at": {"from": None, "to": joined_at.isoformat()},
                    "membership_source": {"from": None, "to": membership_source},
                },
                metadata={"person_id": str(person.id)},
            )

        serializer = self.get_serializer(membership)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    def get_membership_or_404(self):
        person = self.get_business_person_or_404()
        membership = Membership.objects.select_related("person").filter(person=person).first()
        if membership is None:
            raise NotFound("Not found.")
        return membership

    def get_business_person_or_404(self, *, select_for_update=False):
        queryset = Person.objects.business()
        if select_for_update:
            queryset = queryset.select_for_update()
        person = queryset.filter(pk=self.kwargs["person_id"]).first()
        if person is None:
            raise NotFound("Not found.")
        return person


class EndMembershipConflict(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "Membership cannot be ended for this person."
    default_code = "membership_end_conflict"


class PersonMembershipEndView(generics.GenericAPIView):
    serializer_class = MembershipSerializer
    permission_classes = [IsAuthenticated, HasMembershipWriteAccess]

    @extend_schema(
        operation_id="people_membership_end",
        summary="End Membership for CRM Person",
        description=(
            "Transitions an existing ACTIVE Membership for a CRM-visible BUSINESS Person to FORMER. "
            "The existing Membership row is reused. Joined date and membership source are preserved. "
            "Archived BUSINESS people are rejected for lifecycle changes. "
            "People without Membership and already-FORMER memberships return 409."
        ),
        request=EndMembershipSerializer,
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
            200: MembershipSerializer,
            400: OpenApiResponse(description="Invalid request body or end date."),
            401: OpenApiResponse(description="Authentication credentials were not provided."),
            403: OpenApiResponse(description="You do not have a permitted active staff role."),
            404: OpenApiResponse(description="No CRM-visible BUSINESS Person matches the supplied ID."),
            409: OpenApiResponse(
                description="There is no eligible active membership to end, or the person is archived."
            ),
        },
        tags=["People", "Membership"],
        examples=[
            OpenApiExample(
                "End membership request",
                value={"ended_at": "2026-08-30"},
                request_only=True,
            ),
            OpenApiExample(
                "End membership response",
                value={
                    "id": 9,
                    "status": "FORMER",
                    "joined_at": "2024-04-12",
                    "ended_at": "2026-08-30",
                    "membership_source": "STAFF",
                    "created_at": "2026-08-30T12:00:00Z",
                    "updated_at": "2026-08-30T13:00:00Z",
                },
                response_only=True,
            ),
        ],
    )
    def post(self, request, *args, **kwargs):
        input_serializer = EndMembershipSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)

        with transaction.atomic():
            person = self.get_business_person_or_404(select_for_update=True)

            if person.archived_at is not None:
                raise EndMembershipConflict("Archived people cannot receive membership lifecycle changes.")

            membership = Membership.objects.select_for_update().filter(person=person).first()
            if membership is None:
                raise EndMembershipConflict("There is no active membership to end for this person.")

            if membership.status == Membership.Status.FORMER:
                raise EndMembershipConflict("This membership is already former.")

            ended_at = input_serializer.validated_data["ended_at"]
            membership.status = Membership.Status.FORMER
            membership.ended_at = ended_at
            try:
                membership.full_clean()
            except DjangoValidationError as error:
                raise serializers.ValidationError(error.message_dict)
            membership.save(update_fields=["status", "ended_at", "updated_at"])

            record_audit_event(
                action=AuditEvent.Action.MEMBERSHIP_ENDED,
                actor_user=request.user,
                entity_type="Membership",
                entity_id=membership.id,
                changes={
                    "status": {"from": Membership.Status.ACTIVE, "to": Membership.Status.FORMER},
                    "ended_at": {"from": None, "to": ended_at.isoformat()},
                },
                metadata={"person_id": str(person.id)},
            )

        serializer = self.get_serializer(membership)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def get_business_person_or_404(self, *, select_for_update=False):
        queryset = Person.objects.business()
        if select_for_update:
            queryset = queryset.select_for_update()
        person = queryset.filter(pk=self.kwargs["person_id"]).first()
        if person is None:
            raise NotFound("Not found.")
        return person
