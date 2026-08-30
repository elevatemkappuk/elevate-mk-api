from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import generics, serializers, status
from rest_framework.exceptions import APIException, NotFound
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from people.models import Person
from professional_profiles.models import Industry, ProfessionalProfile
from professional_profiles.serializers import (
    IndustryOptionSerializer,
    ProfessionalProfileSerializer,
    ProfessionalProfileWriteSerializer,
)
from staff_access.models import StaffRole
from staff_access.permissions import HasActiveStaffRoleCodes


class HasProfessionalProfileAccess(HasActiveStaffRoleCodes):
    required_role_codes = (
        StaffRole.CRM_ADMIN,
        StaffRole.CRM_MANAGER,
        StaffRole.CRM_VIEWER,
    )


class HasProfessionalProfileWriteAccess(HasActiveStaffRoleCodes):
    required_role_codes = (
        StaffRole.CRM_ADMIN,
        StaffRole.CRM_MANAGER,
    )


class ProfessionalProfileConflict(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "Professional profile cannot be written for this person."
    default_code = "professional_profile_conflict"


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
    lookup_url_kwarg = "person_id"
    lookup_field = "person_id"

    def get_permissions(self):
        permission_classes = [IsAuthenticated, HasProfessionalProfileAccess]
        if self.request.method in {"POST", "PATCH"}:
            permission_classes = [IsAuthenticated, HasProfessionalProfileWriteAccess]
        return [permission() for permission in permission_classes]

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

    @extend_schema(
        operation_id="people_professional_profile_create",
        summary="Create CRM Person professional profile",
        description=(
            "Creates the one editable ProfessionalProfile resource for an active CRM-visible BUSINESS Person. "
            "All professional fields are optional. Archived BUSINESS people are rejected with 409. "
            "TECHNICAL people and nonexistent people return 404. Existing profiles return 409 and must be updated with PATCH."
        ),
        request=ProfessionalProfileWriteSerializer,
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
            201: ProfessionalProfileSerializer,
            400: OpenApiResponse(description="Invalid request body, unsupported fields, or invalid taxonomy reference."),
            401: OpenApiResponse(description="Authentication credentials were not provided."),
            403: OpenApiResponse(description="You do not have a permitted active staff role."),
            404: OpenApiResponse(description="No CRM-visible BUSINESS Person matches the supplied ID."),
            409: OpenApiResponse(description="The person is archived or already has a professional profile."),
        },
        tags=["Professional Profiles"],
        examples=[
            OpenApiExample(
                "Professional profile create request",
                value={
                    "job_title": "Software Engineer",
                    "company": "Example Ltd",
                    "industry": 25,
                    "career_stage": "MID_CAREER",
                    "linkedin_url": "https://www.linkedin.com/in/example",
                },
                request_only=True,
            ),
            OpenApiExample(
                "Professional profile create response",
                value={
                    "id": 12,
                    "job_title": "Software Engineer",
                    "company": "Example Ltd",
                    "industry": {
                        "id": 25,
                        "name": "Technology",
                        "slug": "technology",
                    },
                    "career_stage": "MID_CAREER",
                    "linkedin_url": "https://www.linkedin.com/in/example",
                    "created_at": "2026-08-30T12:00:00Z",
                    "updated_at": "2026-08-30T12:00:00Z",
                },
                response_only=True,
            ),
        ],
    )
    def post(self, request, *args, **kwargs):
        input_serializer = ProfessionalProfileWriteSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)

        try:
            with transaction.atomic():
                person = self.get_business_person_or_404(select_for_update=True)

                if person.archived_at is not None:
                    raise ProfessionalProfileConflict("Archived people cannot receive professional profile changes.")

                if ProfessionalProfile.objects.filter(person=person).exists():
                    raise ProfessionalProfileConflict("This person already has a professional profile.")

                professional_profile = ProfessionalProfile(
                    person=person,
                    **input_serializer.validated_data,
                )
                try:
                    professional_profile.full_clean()
                except DjangoValidationError as error:
                    raise serializers.ValidationError(error.message_dict)
                professional_profile.save()
        except IntegrityError:
            raise ProfessionalProfileConflict("This person already has a professional profile.")

        serializer = self.get_serializer(professional_profile)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @extend_schema(
        operation_id="people_professional_profile_partial_update",
        summary="Partially update CRM Person professional profile",
        description=(
            "Partially updates an existing ProfessionalProfile for an active CRM-visible BUSINESS Person. "
            "Only supplied fields are changed. Archived BUSINESS people are rejected with 409. "
            "PATCH does not create a profile."
        ),
        request=ProfessionalProfileWriteSerializer,
        parameters=[
            OpenApiParameter(
                name="person_id",
                type=int,
                location=OpenApiParameter.PATH,
                description="Primary key of an active CRM-visible BUSINESS Person with an existing professional profile.",
                required=True,
            )
        ],
        responses={
            200: ProfessionalProfileSerializer,
            400: OpenApiResponse(description="Invalid request body, unsupported fields, or invalid taxonomy reference."),
            401: OpenApiResponse(description="Authentication credentials were not provided."),
            403: OpenApiResponse(description="You do not have a permitted active staff role."),
            404: OpenApiResponse(description="No ProfessionalProfile exists for the supplied CRM-visible BUSINESS Person."),
            409: OpenApiResponse(description="The person is archived and cannot be modified."),
        },
        tags=["Professional Profiles"],
        examples=[
            OpenApiExample(
                "Professional profile patch request",
                value={
                    "industry": None,
                    "career_stage": "LEADERSHIP",
                },
                request_only=True,
            ),
            OpenApiExample(
                "Professional profile patch response",
                value={
                    "id": 12,
                    "job_title": "Software Engineer",
                    "company": "Example Ltd",
                    "industry": None,
                    "career_stage": "LEADERSHIP",
                    "linkedin_url": "https://www.linkedin.com/in/example",
                    "created_at": "2026-08-30T12:00:00Z",
                    "updated_at": "2026-08-30T12:15:00Z",
                },
                response_only=True,
            ),
        ],
    )
    def patch(self, request, *args, **kwargs):
        input_serializer = ProfessionalProfileWriteSerializer(data=request.data, partial=True)
        input_serializer.is_valid(raise_exception=True)

        with transaction.atomic():
            person = self.get_business_person_or_404(select_for_update=True)

            if person.archived_at is not None:
                raise ProfessionalProfileConflict("Archived people cannot receive professional profile changes.")

            professional_profile = (
                ProfessionalProfile.objects.select_for_update()
                .filter(person=person)
                .first()
            )
            if professional_profile is None:
                raise NotFound("Not found.")

            for field, value in input_serializer.validated_data.items():
                setattr(professional_profile, field, value)

            try:
                professional_profile.full_clean()
            except DjangoValidationError as error:
                raise serializers.ValidationError(error.message_dict)
            professional_profile.save()

        serializer = self.get_serializer(professional_profile)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def get_queryset(self):
        return ProfessionalProfile.objects.select_related("industry", "person").filter(
            person__record_type=Person.RecordType.BUSINESS
        )

    def get_business_person_or_404(self, *, select_for_update=False):
        queryset = Person.objects.business()
        if select_for_update:
            queryset = queryset.select_for_update()
        person = queryset.filter(pk=self.kwargs["person_id"]).first()
        if person is None:
            raise NotFound("Not found.")
        return person
