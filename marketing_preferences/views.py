from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import generics, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from marketing_preferences.audience import (
    ELIGIBLE,
    annotate_email_marketing_classification,
    build_active_people_selection,
    get_audience_counts,
)
from marketing_preferences.models import MarketingPreference
from marketing_preferences.serializers import (
    AudiencePreviewPersonSerializer,
    AudiencePreviewRequestSerializer,
    MarketingPreferenceMutationSerializer,
    MarketingPreferenceSerializer,
    MarketingPreferenceWriteResponseSerializer,
)
from marketing_preferences.services import (
    get_effective_marketing_preference,
    record_opt_in,
    record_opt_out,
)
from people.models import Person
from staff_access.models import StaffRole
from staff_access.permissions import HasActiveStaffRoleCodes


class HasMarketingPreferenceAccess(HasActiveStaffRoleCodes):
    required_role_codes = (StaffRole.CRM_ADMIN, StaffRole.CRM_MANAGER, StaffRole.CRM_VIEWER)


class HasMarketingPreferenceWriteAccess(HasActiveStaffRoleCodes):
    required_role_codes = (StaffRole.CRM_ADMIN, StaffRole.CRM_MANAGER)


class AudiencePreviewView(generics.GenericAPIView):
    permission_classes = [IsAuthenticated, HasMarketingPreferenceAccess]
    serializer_class = AudiencePreviewRequestSerializer

    @extend_schema(
        operation_id="marketing_audience_preview",
        summary="Preview an active CRM email-marketing audience",
        request=AudiencePreviewRequestSerializer,
        responses={
            200: OpenApiResponse(description="Selection counts and a paginated read-only preview."),
            400: OpenApiResponse(description="Invalid audience criteria or pagination."),
            401: OpenApiResponse(description="Authentication credentials were not provided."),
            403: OpenApiResponse(description="You do not have a permitted active staff role."),
        },
        tags=["Marketing Audience"],
    )
    def post(self, request, *args, **kwargs):
        input_serializer = self.get_serializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        data = input_serializer.validated_data

        selected = build_active_people_selection(data["selection"], data["ordering"])
        classified = annotate_email_marketing_classification(selected)
        selected_count, eligible_count, excluded_count, exclusion_counts = get_audience_counts(classified)

        result_queryset = classified
        if data["result"] == "eligible":
            result_queryset = result_queryset.filter(audience_classification=ELIGIBLE)
        elif data["result"] == "excluded":
            result_queryset = result_queryset.exclude(audience_classification=ELIGIBLE)

        page = data["page"]
        page_size = data["page_size"]
        result_count = result_queryset.count()
        start = (page - 1) * page_size
        page_rows = list(result_queryset[start:start + page_size])
        has_next = start + page_size < result_count
        has_previous = page > 1 and start < result_count + page_size

        return Response({
            "selection": {**data["selection"], "record_state": "active"},
            "selected_count": selected_count,
            "eligible_count": eligible_count,
            "excluded_count": excluded_count,
            "exclusion_counts": exclusion_counts,
            "results": {
                "count": result_count,
                "page": page,
                "page_size": page_size,
                "next_page": page + 1 if has_next else None,
                "previous_page": page - 1 if has_previous else None,
                "next": page + 1 if has_next else None,
                "previous": page - 1 if has_previous else None,
                "results": AudiencePreviewPersonSerializer(page_rows, many=True).data,
            },
        })


class PersonMarketingPreferenceView(generics.GenericAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = MarketingPreferenceSerializer

    def get_permissions(self):
        permission_classes = [IsAuthenticated, HasMarketingPreferenceAccess]
        if self.request.method == "POST":
            permission_classes = [IsAuthenticated, HasMarketingPreferenceWriteAccess]
        return [permission() for permission in permission_classes]

    @extend_schema(
        operation_id="people_marketing_preference_retrieve",
        summary="Retrieve effective email marketing preference",
        parameters=[OpenApiParameter(name="person_id", type=int, location=OpenApiParameter.PATH, required=True)],
        responses={200: MarketingPreferenceSerializer, 403: OpenApiResponse(description="You do not have a permitted active staff role."), 404: OpenApiResponse(description="No BUSINESS Person matches the supplied ID.")},
        tags=["People", "Marketing Preference"],
    )
    def get(self, request, *args, **kwargs):
        person = self.get_person_or_404()
        return Response(self.get_serializer(get_effective_marketing_preference(person=person)).data)

    @extend_schema(
        operation_id="people_marketing_preference_record",
        summary="Record explicit email marketing preference",
        request=MarketingPreferenceMutationSerializer,
        responses={200: MarketingPreferenceWriteResponseSerializer, 400: OpenApiResponse(description="Invalid marketing preference state."), 403: OpenApiResponse(description="You do not have a permitted active staff role."), 404: OpenApiResponse(description="No BUSINESS Person matches the supplied ID.")},
        tags=["People", "Marketing Preference"],
    )
    def post(self, request, *args, **kwargs):
        person = self.get_person_or_404()
        input_serializer = MarketingPreferenceMutationSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        state = input_serializer.validated_data["state"]
        result = (
            record_opt_in(person=person, actor_user=request.user)
            if state == MarketingPreference.State.OPTED_IN
            else record_opt_out(person=person, actor_user=request.user)
        )
        return Response(
            MarketingPreferenceWriteResponseSerializer({
                "preference": result.preference,
                "changed": result.changed,
            }).data,
            status=status.HTTP_200_OK,
        )

    def get_person_or_404(self):
        person = Person.objects.business().filter(pk=self.kwargs["person_id"]).first()
        if person is None:
            from rest_framework.exceptions import NotFound

            raise NotFound("Not found.")
        return person
