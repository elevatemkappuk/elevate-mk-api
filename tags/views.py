from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import generics
from rest_framework.exceptions import NotFound
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from people.models import Person
from staff_access.models import StaffRole
from staff_access.permissions import HasActiveStaffRoleCodes
from tags.models import Tag
from tags.serializers import TagSummarySerializer


class HasTagAccess(HasActiveStaffRoleCodes):
    required_role_codes = (
        StaffRole.CRM_ADMIN,
        StaffRole.CRM_MANAGER,
        StaffRole.CRM_VIEWER,
    )


class TagListView(generics.ListAPIView):
    serializer_class = TagSummarySerializer
    permission_classes = [IsAuthenticated, HasTagAccess]
    pagination_class = None

    @extend_schema(
        operation_id="tags_list",
        summary="List active Tags",
        description=(
            "Returns the active canonical Tag taxonomy for internal CRM usage. "
            "Only active Tag definitions are returned. Results are ordered by display_order, name, and id. "
            "Tags are internal staff classification data, not member-facing profile fields. "
            "Session authentication and an active CRM staff role are required."
        ),
        responses={
            200: TagSummarySerializer(many=True),
            401: OpenApiResponse(description="Authentication credentials were not provided."),
            403: OpenApiResponse(description="You do not have a permitted active staff role."),
        },
        tags=["Tags"],
        examples=[
            OpenApiExample(
                "Tag list",
                value=[
                    {"id": 1, "name": "Potential Speaker", "slug": "potential-speaker"},
                    {"id": 8, "name": "VIP", "slug": "vip"},
                ],
                response_only=True,
            )
        ],
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        return Tag.objects.filter(is_active=True)


class PersonTagListView(generics.GenericAPIView):
    serializer_class = TagSummarySerializer
    permission_classes = [IsAuthenticated, HasTagAccess]
    pagination_class = None

    @extend_schema(
        operation_id="people_tags_list",
        summary="List CRM Person Tags",
        description=(
            "Returns active Tag definitions assigned to a single CRM-visible BUSINESS Person. "
            "Only active PersonTag assignments whose Tag definition is also active are returned. "
            "Archived BUSINESS people remain retrievable by direct ID. "
            "TECHNICAL people are outside the CRM People domain and return 404. "
            "Tags are internal CRM classification data, so lifecycle audit metadata is intentionally omitted from this compact read."
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
            200: TagSummarySerializer(many=True),
            401: OpenApiResponse(description="Authentication credentials were not provided."),
            403: OpenApiResponse(description="You do not have a permitted active staff role."),
            404: OpenApiResponse(
                description="No BUSINESS Person matches the supplied ID within the CRM People domain."
            ),
        },
        tags=["Tags"],
        examples=[
            OpenApiExample(
                "Person tag list",
                value=[
                    {"id": 2, "name": "Potential Mentor", "slug": "potential-mentor"},
                    {"id": 8, "name": "VIP", "slug": "vip"},
                ],
                response_only=True,
            )
        ],
    )
    def get(self, request, *args, **kwargs):
        serializer = TagSummarySerializer(self.get_queryset(), many=True)
        return Response(serializer.data)

    def get_queryset(self):
        person = self.get_business_person_or_404()
        return Tag.objects.filter(
            person_tags__person=person,
            person_tags__is_active=True,
            is_active=True,
        ).order_by("display_order", "name", "id")

    def get_business_person_or_404(self):
        person = Person.objects.business().filter(pk=self.kwargs["person_id"]).first()
        if person is None:
            raise NotFound("Not found.")
        return person

