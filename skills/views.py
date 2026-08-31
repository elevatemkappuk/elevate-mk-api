from django.db import IntegrityError, transaction
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import generics, status
from rest_framework.exceptions import APIException, NotFound
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from audit.models import AuditEvent
from audit.services import record_audit_event
from people.models import Person
from staff_access.models import StaffRole
from staff_access.permissions import HasActiveStaffRoleCodes
from skills.models import PersonSkill, Skill
from skills.serializers import AssignSkillInputSerializer, SkillSummarySerializer


class HasSkillAccess(HasActiveStaffRoleCodes):
    required_role_codes = (
        StaffRole.CRM_ADMIN,
        StaffRole.CRM_MANAGER,
        StaffRole.CRM_VIEWER,
    )


class HasSkillWriteAccess(HasActiveStaffRoleCodes):
    required_role_codes = (
        StaffRole.CRM_ADMIN,
        StaffRole.CRM_MANAGER,
    )


class SkillAssignmentConflict(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "Skill assignment cannot be written for this person."
    default_code = "skill_assignment_conflict"


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


class PersonSkillListView(generics.GenericAPIView):
    serializer_class = SkillSummarySerializer
    pagination_class = None

    def get_permissions(self):
        permission_classes = [IsAuthenticated, HasSkillAccess]
        if self.request.method == "POST":
            permission_classes = [IsAuthenticated, HasSkillWriteAccess]
        return [permission() for permission in permission_classes]

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
        serializer = SkillSummarySerializer(self.get_queryset(), many=True)
        return Response(serializer.data)

    @extend_schema(
        operation_id="people_skills_create",
        summary="Assign CRM Person Skill",
        description=(
            "Creates a PersonSkill relationship for an active CRM-visible BUSINESS Person. "
            "Only active Skill definitions may be assigned. "
            "Archived BUSINESS people return 409. "
            "TECHNICAL people and nonexistent people return 404. "
            "Duplicate assignments return 409."
        ),
        request=AssignSkillInputSerializer,
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
            201: SkillSummarySerializer,
            400: OpenApiResponse(description="Invalid request body, unsupported fields, nonexistent skill, or inactive skill."),
            401: OpenApiResponse(description="Authentication credentials were not provided."),
            403: OpenApiResponse(description="You do not have a permitted active staff role."),
            404: OpenApiResponse(description="No CRM-visible BUSINESS Person matches the supplied ID."),
            409: OpenApiResponse(description="The person is archived or the skill is already assigned."),
        },
        tags=["Skills"],
        examples=[
            OpenApiExample(
                "Assign skill request",
                value={"skill": 16},
                request_only=True,
            ),
            OpenApiExample(
                "Assign skill response",
                value={"id": 16, "name": "Project Management", "slug": "project-management"},
                response_only=True,
            ),
        ],
    )
    def post(self, request, *args, **kwargs):
        input_serializer = AssignSkillInputSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)

        skill = input_serializer.validated_data["skill"]
        with transaction.atomic():
            person = self.get_business_person_or_404(select_for_update=True)

            if person.archived_at is not None:
                raise SkillAssignmentConflict("Archived people cannot receive skill changes.")

            if PersonSkill.objects.filter(person=person, skill=skill).exists():
                raise SkillAssignmentConflict("This skill is already assigned to the person.")

            try:
                person_skill = PersonSkill.objects.create(person=person, skill=skill)
            except IntegrityError:
                raise SkillAssignmentConflict("This skill is already assigned to the person.")

            record_audit_event(
                action=AuditEvent.Action.SKILL_ASSIGNED,
                actor_user=request.user,
                entity_type="PersonSkill",
                entity_id=person_skill.id,
                changes={"assigned": {"from": False, "to": True}},
                metadata={"person_id": str(person.id), "skill_id": str(skill.id)},
            )

        serializer = SkillSummarySerializer(skill)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    def get_queryset(self):
        person = self.get_business_person_or_404()
        return Skill.objects.filter(
            person_skills__person=person,
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


class PersonSkillDetailView(generics.GenericAPIView):
    permission_classes = [IsAuthenticated, HasSkillWriteAccess]

    @extend_schema(
        operation_id="people_skills_delete",
        summary="Remove CRM Person Skill",
        description=(
            "Deletes only the PersonSkill assignment for an active or inactive Skill from a CRM-visible BUSINESS Person. "
            "Archived BUSINESS people return 409. "
            "TECHNICAL people and nonexistent people return 404. "
            "The canonical Skill definition is not deleted or modified."
        ),
        parameters=[
            OpenApiParameter(
                name="person_id",
                type=int,
                location=OpenApiParameter.PATH,
                description="Primary key of a CRM-visible BUSINESS Person.",
                required=True,
            ),
            OpenApiParameter(
                name="skill_id",
                type=int,
                location=OpenApiParameter.PATH,
                description="Primary key of the assigned Skill to remove.",
                required=True,
            ),
        ],
        responses={
            204: OpenApiResponse(description="The skill assignment was removed."),
            401: OpenApiResponse(description="Authentication credentials were not provided."),
            403: OpenApiResponse(description="You do not have a permitted active staff role."),
            404: OpenApiResponse(description="No matching CRM-visible BUSINESS Person or skill assignment exists."),
            409: OpenApiResponse(description="The person is archived and cannot be modified."),
        },
        tags=["Skills"],
    )
    def delete(self, request, *args, **kwargs):
        with transaction.atomic():
            person = self.get_business_person_or_404(select_for_update=True)

            if person.archived_at is not None:
                raise SkillAssignmentConflict("Archived people cannot receive skill changes.")

            person_skill = (
                PersonSkill.objects.select_for_update()
                .filter(person=person, skill_id=self.kwargs["skill_id"])
                .first()
            )
            if person_skill is None:
                raise NotFound("Not found.")

            person_skill_id = person_skill.id
            skill_id = person_skill.skill_id
            person_skill.delete()

            record_audit_event(
                action=AuditEvent.Action.SKILL_REMOVED,
                actor_user=request.user,
                entity_type="PersonSkill",
                entity_id=person_skill_id,
                changes={"assigned": {"from": True, "to": False}},
                metadata={"person_id": str(person.id), "skill_id": str(skill_id)},
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
