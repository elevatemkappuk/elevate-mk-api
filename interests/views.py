from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import generics
from rest_framework.exceptions import NotFound
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from interests.models import Interest
from interests.serializers import InterestSummarySerializer
from people.models import Person
from staff_access.models import StaffRole
from staff_access.permissions import HasActiveStaffRoleCodes


class HasInterestAccess(HasActiveStaffRoleCodes):
    required_role_codes = (
        StaffRole.CRM_ADMIN,
        StaffRole.CRM_MANAGER,
        StaffRole.CRM_VIEWER,
    )


class InterestListView(generics.ListAPIView):
    serializer_class = InterestSummarySerializer
    permission_classes = [IsAuthenticated, HasInterestAccess]
    pagination_class = None

    @extend_schema(
        operation_id="interests_list",
        summary="List active Interests",
        description=(
            "Returns the active canonical Interest taxonomy for CRM forms and overview displays. "
            "Only active Interest definitions are returned. Results are ordered by display_order, name, and id. "
            "Session authentication and an active CRM staff role are required."
        ),
        responses={
            200: InterestSummarySerializer(many=True),
            401: OpenApiResponse(description="Authentication credentials were not provided."),
            403: OpenApiResponse(description="You do not have a permitted active staff role."),
        },
        tags=["Interests"],
        examples=[
            OpenApiExample(
                "Interest list",
                value=[
                    {"id": 1, "name": "Networking", "slug": "networking"},
                    {"id": 2, "name": "Mentoring", "slug": "mentoring"},
                ],
                response_only=True,
            )
        ],
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        return Interest.objects.filter(is_active=True)


class PersonInterestListView(generics.GenericAPIView):
    serializer_class = InterestSummarySerializer
    permission_classes = [IsAuthenticated, HasInterestAccess]
    pagination_class = None

    @extend_schema(
        operation_id="people_interests_list",
        summary="List CRM Person Interests",
        description=(
            "Returns active Interest definitions assigned to a single CRM-visible BUSINESS Person. "
            "Archived BUSINESS people remain retrievable by direct ID. "
            "TECHNICAL people are outside the CRM People domain and return 404. "
            "Inactive Interest definitions remain stored in the database but are omitted from this read response."
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
            200: InterestSummarySerializer(many=True),
            401: OpenApiResponse(description="Authentication credentials were not provided."),
            403: OpenApiResponse(description="You do not have a permitted active staff role."),
            404: OpenApiResponse(
                description="No BUSINESS Person matches the supplied ID within the CRM People domain."
            ),
        },
        tags=["Interests"],
        examples=[
            OpenApiExample(
                "Person interest list",
                value=[
                    {"id": 5, "name": "Technology", "slug": "technology"},
                    {"id": 13, "name": "Startups", "slug": "startups"},
                ],
                response_only=True,
            )
        ],
    )
    def get(self, request, *args, **kwargs):
        serializer = InterestSummarySerializer(self.get_queryset(), many=True)
        return Response(serializer.data)

    def get_queryset(self):
        person = self.get_business_person_or_404()
        return Interest.objects.filter(
            person_interests__person=person,
            is_active=True,
        ).order_by("display_order", "name", "id")

    def get_business_person_or_404(self):
        person = Person.objects.business().filter(pk=self.kwargs["person_id"]).first()
        if person is None:
            raise NotFound("Not found.")
        return person

