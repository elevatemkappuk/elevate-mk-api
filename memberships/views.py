from django.db import IntegrityError, transaction
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import generics, serializers, status
from rest_framework.exceptions import APIException, NotFound
from rest_framework.exceptions import NotFound
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from memberships.models import Membership
from memberships.serializers import MakeMembershipSerializer, MembershipSerializer
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
        person = self.get_business_person_or_404(select_for_update=True)

        if person.archived_at is not None:
            raise MembershipConflict("Archived people cannot receive a new membership.")

        input_serializer = MakeMembershipSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)

        if Membership.objects.filter(person=person).exists():
            raise MembershipConflict("This person already has a membership record.")

        try:
            with transaction.atomic():
                person = self.get_business_person_or_404(select_for_update=True)

                if person.archived_at is not None:
                    raise MembershipConflict("Archived people cannot receive a new membership.")

                if Membership.objects.filter(person=person).exists():
                    raise MembershipConflict("This person already has a membership record.")

                membership = Membership.objects.create(
                    person=person,
                    status=Membership.Status.ACTIVE,
                    joined_at=input_serializer.validated_data["joined_at"],
                    membership_source=input_serializer.validated_data["membership_source"],
                )
        except IntegrityError:
            raise MembershipConflict("This person already has a membership record.")

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
