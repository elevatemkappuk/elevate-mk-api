from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import generics
from rest_framework.permissions import IsAuthenticated

from people.models import Person
from professional_profiles.models import Industry, ProfessionalProfile
from professional_profiles.serializers import IndustryOptionSerializer, ProfessionalProfileSerializer
from staff_access.models import StaffRole
from staff_access.permissions import HasActiveStaffRoleCodes


class HasProfessionalProfileAccess(HasActiveStaffRoleCodes):
    required_role_codes = (
        StaffRole.CRM_ADMIN,
        StaffRole.CRM_MANAGER,
        StaffRole.CRM_VIEWER,
    )


class IndustryListView(generics.ListAPIView):
    serializer_class = IndustryOptionSerializer
    permission_classes = [IsAuthenticated, HasProfessionalProfileAccess]
    pagination_class = None

    @extend_schema(
        operation_id="industries_list",
        summary="List active Industries",
        description=(
            "Returns the active canonical Industry taxonomy for CRM forms and future filters. "
            "Only active records are returned. Results are ordered by display_order, name, and id. "
            "Session authentication and an active CRM staff role are required."
        ),
        responses={
            200: IndustryOptionSerializer(many=True),
            401: OpenApiResponse(description="Authentication credentials were not provided."),
            403: OpenApiResponse(description="You do not have a permitted active staff role."),
        },
        tags=["Professional Profiles"],
        examples=[
            OpenApiExample(
                "Industry list",
                value=[
                    {"id": 1, "name": "Technology", "slug": "technology"},
                    {"id": 2, "name": "Finance", "slug": "finance"},
                ],
                response_only=True,
            )
        ],
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        return Industry.objects.filter(is_active=True)


class ProfessionalProfileDetailView(generics.RetrieveAPIView):
    serializer_class = ProfessionalProfileSerializer
    permission_classes = [IsAuthenticated, HasProfessionalProfileAccess]
    lookup_url_kwarg = "person_id"
    lookup_field = "person_id"

    @extend_schema(
        operation_id="people_professional_profile_retrieve",
        summary="Retrieve CRM Person professional profile",
        description=(
            "Returns the ProfessionalProfile subresource for a CRM-visible BUSINESS Person. "
            "Archived BUSINESS people remain retrievable by direct ID. "
            "BUSINESS people without a professional profile, TECHNICAL people, and nonexistent IDs return 404."
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
            200: ProfessionalProfileSerializer,
            401: OpenApiResponse(description="Authentication credentials were not provided."),
            403: OpenApiResponse(description="You do not have a permitted active staff role."),
            404: OpenApiResponse(
                description="No ProfessionalProfile exists for the supplied CRM-visible BUSINESS Person."
            ),
        },
        tags=["Professional Profiles"],
        examples=[
            OpenApiExample(
                "Professional profile",
                value={
                    "id": 12,
                    "job_title": "Software Engineer",
                    "company": "Example Ltd",
                    "industry": {
                        "id": 3,
                        "name": "Technology",
                        "slug": "technology",
                    },
                    "career_stage": None,
                    "linkedin_url": "https://www.linkedin.com/in/example",
                    "created_at": "2026-08-30T12:00:00Z",
                    "updated_at": "2026-08-30T12:00:00Z",
                },
                response_only=True,
            )
        ],
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        return ProfessionalProfile.objects.select_related("industry", "person").filter(
            person__record_type=Person.RecordType.BUSINESS
        )
