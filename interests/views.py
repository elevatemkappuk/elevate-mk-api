from django.db import IntegrityError, transaction
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import generics, status
from rest_framework.exceptions import APIException, NotFound
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.serializers import ValidationError

from interests.models import Interest, PersonInterest
from interests.serializers import AssignInterestInputSerializer, InterestSummarySerializer
from people.models import Person
from staff_access.models import StaffRole
from staff_access.permissions import HasActiveStaffRoleCodes


class HasInterestAccess(HasActiveStaffRoleCodes):
    required_role_codes = (
        StaffRole.CRM_ADMIN,
        StaffRole.CRM_MANAGER,
        StaffRole.CRM_VIEWER,
    )


class HasInterestWriteAccess(HasActiveStaffRoleCodes):
    required_role_codes = (
        StaffRole.CRM_ADMIN,
        StaffRole.CRM_MANAGER,
    )


class InterestAssignmentConflict(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "Interest assignment cannot be written for this person."
    default_code = "interest_assignment_conflict"


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
    pagination_class = None

    def get_permissions(self):
        permission_classes = [IsAuthenticated, HasInterestAccess]
        if self.request.method == "POST":
            permission_classes = [IsAuthenticated, HasInterestWriteAccess]
        return [permission() for permission in permission_classes]

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

    @extend_schema(
        operation_id="people_interests_create",
        summary="Assign CRM Person Interest",
        description=(
            "Creates a PersonInterest relationship for an active CRM-visible BUSINESS Person. "
            "Only active Interest definitions may be assigned. "
            "Archived BUSINESS people return 409. "
            "TECHNICAL people and nonexistent people return 404. "
            "Duplicate assignments return 409, including assignments to Interests that later became inactive."
        ),
        request=AssignInterestInputSerializer,
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
            201: InterestSummarySerializer,
            400: OpenApiResponse(description="Invalid request body, unsupported fields, nonexistent interest, or inactive interest."),
            401: OpenApiResponse(description="Authentication credentials were not provided."),
            403: OpenApiResponse(description="You do not have a permitted active staff role."),
            404: OpenApiResponse(description="No CRM-visible BUSINESS Person matches the supplied ID."),
            409: OpenApiResponse(description="The person is archived or the interest is already assigned."),
        },
        tags=["Interests"],
        examples=[
            OpenApiExample(
                "Assign interest request",
                value={"interest": 5},
                request_only=True,
            ),
            OpenApiExample(
                "Assign interest response",
                value={"id": 5, "name": "Technology", "slug": "technology"},
                response_only=True,
            ),
        ],
    )
    def post(self, request, *args, **kwargs):
        input_serializer = AssignInterestInputSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)

        interest = input_serializer.validated_data["interest"]
        try:
            with transaction.atomic():
                person = self.get_business_person_or_404(select_for_update=True)

                if person.archived_at is not None:
                    raise InterestAssignmentConflict("Archived people cannot receive interest changes.")

                if PersonInterest.objects.filter(person=person, interest=interest).exists():
                    raise InterestAssignmentConflict("This interest is already assigned to the person.")

                if not interest.is_active:
                    raise ValidationError({"interest": ["Only active interests may be assigned."]})

                PersonInterest.objects.create(person=person, interest=interest)
        except IntegrityError:
            raise InterestAssignmentConflict("This interest is already assigned to the person.")

        serializer = InterestSummarySerializer(interest)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    def get_queryset(self):
        person = self.get_business_person_or_404()
        return Interest.objects.filter(
            person_interests__person=person,
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


class PersonInterestDetailView(generics.GenericAPIView):
    permission_classes = [IsAuthenticated, HasInterestWriteAccess]

    @extend_schema(
        operation_id="people_interests_delete",
        summary="Remove CRM Person Interest",
        description=(
            "Deletes only the PersonInterest assignment for an active or inactive Interest from a CRM-visible BUSINESS Person. "
            "Archived BUSINESS people return 409. "
            "TECHNICAL people and nonexistent people return 404. "
            "The canonical Interest definition is not deleted or modified."
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
                name="interest_id",
                type=int,
                location=OpenApiParameter.PATH,
                description="Primary key of the assigned Interest to remove.",
                required=True,
            ),
        ],
        responses={
            204: OpenApiResponse(description="The interest assignment was removed."),
            401: OpenApiResponse(description="Authentication credentials were not provided."),
            403: OpenApiResponse(description="You do not have a permitted active staff role."),
            404: OpenApiResponse(description="No matching CRM-visible BUSINESS Person or interest assignment exists."),
            409: OpenApiResponse(description="The person is archived and cannot be modified."),
        },
        tags=["Interests"],
    )
    def delete(self, request, *args, **kwargs):
        with transaction.atomic():
            person = self.get_business_person_or_404(select_for_update=True)

            if person.archived_at is not None:
                raise InterestAssignmentConflict("Archived people cannot receive interest changes.")

            person_interest = (
                PersonInterest.objects.select_for_update()
                .filter(person=person, interest_id=self.kwargs["interest_id"])
                .first()
            )
            if person_interest is None:
                raise NotFound("Not found.")

            person_interest.delete()

        return Response(status=status.HTTP_204_NO_CONTENT)

    def get_business_person_or_404(self, *, select_for_update=False):
        queryset = Person.objects.business()
        if select_for_update:
            queryset = queryset.select_for_update()
        person = queryset.filter(pk=self.kwargs["person_id"]).first()
        if person is None:
            raise NotFound("Not found.")
        return person
