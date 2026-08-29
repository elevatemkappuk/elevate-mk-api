from django.db.models import Q, Value
from django.db.models.functions import Concat
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema
from rest_framework import generics
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated

from people.models import Person
from people.serializers import (
    PaginatedPersonListSerializer,
    PersonListQuerySerializer,
    PersonListSerializer,
)
from staff_access.models import StaffRole
from staff_access.permissions import HasActiveStaffRoleCodes


class PeoplePagination(PageNumberPagination):
    page_size = 25
    page_size_query_param = "page_size"
    max_page_size = 100

    def get_page_size(self, request):
        params = getattr(request, "_validated_people_query_params", {})
        return params.get("page_size", self.page_size)


class HasPeopleAccess(HasActiveStaffRoleCodes):
    required_role_codes = (
        StaffRole.CRM_ADMIN,
        StaffRole.CRM_MANAGER,
        StaffRole.CRM_VIEWER,
    )


class PeopleListView(generics.ListAPIView):
    serializer_class = PersonListSerializer
    permission_classes = [IsAuthenticated, HasPeopleAccess]
    pagination_class = PeoplePagination

    ORDERING_MAP = {
        "first_name": ("first_name", "last_name", "id"),
        "-first_name": ("-first_name", "last_name", "id"),
        "last_name": ("last_name", "first_name", "id"),
        "-last_name": ("-last_name", "first_name", "id"),
        "created_at": ("created_at", "id"),
        "-created_at": ("-created_at", "id"),
        "updated_at": ("updated_at", "id"),
        "-updated_at": ("-updated_at", "id"),
    }

    @extend_schema(
        operation_id="people_list",
        summary="List CRM People",
        description=(
            "Returns BUSINESS Person records for the Staff CRM People directory. "
            "TECHNICAL persons are excluded for all record_state values. "
            "Active CRM staff roles CRM_ADMIN, CRM_MANAGER, and CRM_VIEWER are allowed. "
            "Session authentication is required."
        ),
        parameters=[PersonListQuerySerializer],
        responses={
            200: PaginatedPersonListSerializer,
            400: OpenApiResponse(description="Invalid query parameter value."),
            401: OpenApiResponse(description="Authentication credentials were not provided."),
            403: OpenApiResponse(description="You do not have a permitted active staff role."),
        },
        tags=["People"],
        examples=[
            OpenApiExample(
                "Active people list",
                value={
                    "count": 1,
                    "next": None,
                    "previous": None,
                    "results": [
                        {
                            "id": 1,
                            "first_name": "Amina",
                            "last_name": "Johnson",
                            "primary_email": "amina@example.com",
                            "mobile": "+265991234567",
                            "location": "Lilongwe",
                            "age_range": "",
                            "gender": "",
                            "archived_at": None,
                            "created_at": "2026-08-29T12:00:00Z",
                            "updated_at": "2026-08-29T12:00:00Z",
                        }
                    ],
                },
                response_only=True,
            )
        ],
    )
    def get(self, request, *args, **kwargs):
        self.validated_query_params = self.get_validated_query_params()
        request._validated_people_query_params = self.validated_query_params
        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        params = getattr(self, "validated_query_params", self.get_validated_query_params())

        queryset = Person.objects.all()
        queryset = self.apply_record_state(queryset, params["record_state"])
        queryset = self.apply_search(queryset, params.get("q", ""))
        return queryset.order_by(*self.ORDERING_MAP[params["ordering"]])

    def get_validated_query_params(self):
        serializer = PersonListQuerySerializer(data=self.request.query_params)
        serializer.is_valid(raise_exception=True)
        return serializer.validated_data

    def apply_record_state(self, queryset, record_state):
        if record_state == PersonListQuerySerializer.RECORD_STATE_ARCHIVED:
            return queryset.archived_business()
        if record_state == PersonListQuerySerializer.RECORD_STATE_ALL:
            return queryset.business()
        return queryset.active_business()

    def apply_search(self, queryset, query):
        query = query.strip()
        if not query:
            return queryset

        return queryset.annotate(
            full_name=Concat("first_name", Value(" "), "last_name")
        ).filter(
            Q(first_name__icontains=query)
            | Q(last_name__icontains=query)
            | Q(primary_email__icontains=query)
            | Q(mobile__icontains=query)
            | Q(full_name__icontains=query)
        )
