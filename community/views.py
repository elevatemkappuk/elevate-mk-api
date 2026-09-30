from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from django.contrib.auth import login
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from memberships.models import Membership
from people.models import Person
from professional_profiles.models import Industry

from community.activation import (
    ACCOUNT_SETUP_UNAVAILABLE_CODE,
    ACCOUNT_SETUP_UNAVAILABLE_DETAIL,
    INVALID_ACTIVATION_CODE,
    INVALID_ACTIVATION_DETAIL,
    PASSWORD_VALIDATION_CODE,
    CommunityAccountSetupUnavailable,
    CommunityPasswordValidationError,
    InvalidCommunityActivation,
    redeem_community_activation,
)
from community.serializers import (
    CommunityActivationSerializer,
    CommunityCurrentUserSerializer,
    CommunityIndustrySerializer,
    CommunityJoinSerializer,
)
from community.services import (
    CommunityJoinIdempotencyConflict,
    CommunityJoinReviewRequired,
    submit_community_join,
)


class CommunityIndustryListView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    @extend_schema(
        operation_id="community_industries_list",
        summary="List public Community Industries",
        responses={200: CommunityIndustrySerializer(many=True)},
        auth=[],
        tags=["Community"],
    )
    def get(self, request):
        industries = Industry.objects.filter(is_active=True)
        return Response(CommunityIndustrySerializer(industries, many=True).data)


@method_decorator(csrf_protect, name="dispatch")
class CommunityJoinView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "community_join"

    @extend_schema(
        operation_id="community_join_create",
        summary="Submit a native Community membership join",
        request=CommunityJoinSerializer,
        responses={
            202: OpenApiResponse(description="Submission accepted."),
            400: OpenApiResponse(description="Invalid submission."),
            409: OpenApiResponse(description="Submission requires review."),
            429: OpenApiResponse(description="Too many submissions."),
        },
        auth=[],
        tags=["Community"],
    )
    def post(self, request):
        serializer = CommunityJoinSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        idempotency_key = request.headers.get("Idempotency-Key", "").strip()
        if len(idempotency_key) > 255:
            return Response(
                {"code": "VALIDATION_ERROR", "detail": "Invalid submission."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            submit_community_join(
                data=serializer.validated_data,
                request=request,
                idempotency_key=idempotency_key or None,
            )
        except (CommunityJoinReviewRequired, CommunityJoinIdempotencyConflict):
            return Response(
                {
                    "code": "SUBMISSION_REQUIRES_REVIEW",
                    "detail": "Your membership submission requires review.",
                },
                status=status.HTTP_409_CONFLICT,
            )
        return Response(
            {
                "status": "accepted",
                "message": "Your membership submission has been received.",
            },
            status=status.HTTP_202_ACCEPTED,
        )


@method_decorator(csrf_protect, name="dispatch")
class CommunityActivationView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "community_activation"

    @extend_schema(
        operation_id="community_activate_account",
        summary="Redeem a Community account activation invitation",
        request=CommunityActivationSerializer,
        responses={200: CommunityCurrentUserSerializer, 400: OpenApiResponse(description="Activation failed."), 409: OpenApiResponse(description="Account setup unavailable."), 429: OpenApiResponse(description="Too many activation attempts.")},
        auth=[],
        tags=["Community"],
    )
    def post(self, request, invitation_id, token):
        serializer = CommunityActivationSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"code": PASSWORD_VALIDATION_CODE, "detail": "Please enter a valid password and confirmation.", "fields": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            result = redeem_community_activation(
                invitation_id=invitation_id,
                raw_token=token,
                password=serializer.validated_data["password"],
            )
        except InvalidCommunityActivation:
            return Response({"code": INVALID_ACTIVATION_CODE, "detail": INVALID_ACTIVATION_DETAIL}, status=status.HTTP_400_BAD_REQUEST)
        except CommunityPasswordValidationError as error:
            return Response({"code": PASSWORD_VALIDATION_CODE, "detail": "Password does not meet the configured requirements.", "fields": {"password": error.messages}}, status=status.HTTP_400_BAD_REQUEST)
        except CommunityAccountSetupUnavailable:
            return Response({"code": ACCOUNT_SETUP_UNAVAILABLE_CODE, "detail": ACCOUNT_SETUP_UNAVAILABLE_DETAIL}, status=status.HTTP_409_CONFLICT)

        login(request, result.user)
        return Response(
            CommunityCurrentUserSerializer({
                "id": result.user.id,
                "first_name": result.user.person.first_name,
                "last_name": result.user.person.last_name,
            }).data,
            status=status.HTTP_200_OK,
        )


class CommunityMeView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="community_current_user",
        summary="Get the current Community account",
        responses={200: CommunityCurrentUserSerializer, 401: OpenApiResponse(description="Authentication credentials were not provided."), 403: OpenApiResponse(description="Community access is unavailable.")},
        tags=["Community"],
    )
    def get(self, request):
        person = request.user.person
        has_membership = Membership.objects.filter(person=person, status=Membership.Status.ACTIVE).exists()
        if person.record_type != Person.RecordType.BUSINESS or person.archived_at is not None or not has_membership:
            return Response({"detail": "Community access is unavailable."}, status=status.HTTP_403_FORBIDDEN)
        return Response(CommunityCurrentUserSerializer({"id": request.user.id, "first_name": person.first_name, "last_name": person.last_name}).data)
