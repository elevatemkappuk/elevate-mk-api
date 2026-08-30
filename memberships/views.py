from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import generics
from rest_framework.exceptions import NotFound
from rest_framework.permissions import IsAuthenticated

from memberships.models import Membership
from memberships.serializers import MembershipSerializer
from people.models import Person
from staff_access.models import StaffRole
from staff_access.permissions import HasActiveStaffRoleCodes


class HasMembershipAccess(HasActiveStaffRoleCodes):
    required_role_codes = (
        StaffRole.CRM_ADMIN,
        StaffRole.CRM_MANAGER,
        StaffRole.CRM_VIEWER,
    )


class PersonMembershipDetailView(generics.RetrieveAPIView):
    serializer_class = MembershipSerializer
    permission_classes = [IsAuthenticated, HasMembershipAccess]

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
        return super().get(request, *args, **kwargs)

    def get_object(self):
        person = Person.objects.business().filter(pk=self.kwargs["person_id"]).first()
        if person is None:
            raise NotFound("Not found.")

        membership = (
            Membership.objects.select_related("person")
            .filter(person=person)
            .first()
        )
        if membership is None:
            raise NotFound("Not found.")

        return membership
