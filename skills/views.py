from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import generics
from rest_framework.exceptions import NotFound
from rest_framework.permissions import IsAuthenticated

from people.models import Person
from staff_access.models import StaffRole
from staff_access.permissions import HasActiveStaffRoleCodes
from skills.models import Skill
from skills.serializers import SkillSummarySerializer


class HasSkillAccess(HasActiveStaffRoleCodes):
    required_role_codes = (
        StaffRole.CRM_ADMIN,
        StaffRole.CRM_MANAGER,
        StaffRole.CRM_VIEWER,
    )


class SkillListView(generics.ListAPIView):
    serializer_class = SkillSummarySerializer
    permission_classes = [IsAuthenticated, HasSkillAccess]
    pagination_class = None

    @extend_schema(
        operation_id="skills_list",
        summary="List active Skills",
        description=(
            "Returns the active canonical Skill taxonomy for CRM forms and future filtering. "
            "Only active Skill definitions are returned. Results are ordered by display_order, name, and id. "
            "Session authentication and an active CRM staff role are required."
        ),
        responses={
            200: SkillSummarySerializer(many=True),
            401: OpenApiResponse(description="Authentication credentials were not provided."),
            403: OpenApiResponse(description="You do not have a permitted active staff role."),
        },
        tags=["Skills"],
        examples=[
            OpenApiExample(
                "Skill list",
                value=[
                    {"id": 1, "name": "Accounting", "slug": "accounting"},
                    {"id": 2, "name": "Business Development", "slug": "business-development"},
                ],
                response_only=True,
            )
        ],
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        return Skill.objects.filter(is_active=True)


class PersonSkillListView(generics.ListAPIView):
    serializer_class = SkillSummarySerializer
    permission_classes = [IsAuthenticated, HasSkillAccess]
    pagination_class = None

    @extend_schema(
        operation_id="people_skills_list",
        summary="List CRM Person Skills",
        description=(
            "Returns active Skill definitions assigned to a single CRM-visible BUSINESS Person. "
            "Archived BUSINESS people remain retrievable by direct ID. "
            "TECHNICAL people are outside the CRM People domain and return 404. "
            "Inactive Skill definitions remain stored in the database but are omitted from this read response."
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
            200: SkillSummarySerializer(many=True),
            401: OpenApiResponse(description="Authentication credentials were not provided."),
            403: OpenApiResponse(description="You do not have a permitted active staff role."),
            404: OpenApiResponse(
                description="No BUSINESS Person matches the supplied ID within the CRM People domain."
            ),
        },
        tags=["Skills"],
        examples=[
            OpenApiExample(
                "Person skill list",
                value=[
                    {"id": 16, "name": "Project Management", "slug": "project-management"},
                    {"id": 21, "name": "Software Development", "slug": "software-development"},
                ],
                response_only=True,
            )
        ],
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        person = self.get_business_person_or_404()
        return Skill.objects.filter(
            person_skills__person=person,
            is_active=True,
        ).distinct()

    def get_business_person_or_404(self):
        person = Person.objects.business().filter(pk=self.kwargs["person_id"]).first()
        if person is None:
            raise NotFound("Not found.")
        return person

