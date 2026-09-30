import logging

from django.conf import settings
from rest_framework import status
from django.contrib.auth import get_user_model, login
from drf_spectacular.utils import OpenApiResponse, extend_schema
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from memberships.models import Membership
from people.models import Person
from professional_profiles.models import Industry
from accounts.serializers import LoginSerializer, PasswordResetRequestSerializer
from accounts.views import build_password_reset_url, record_auth_audit_or_raise
from audit.models import AuditEvent

from community.activation import (
    ACCOUNT_SETUP_UNAVAILABLE_CODE,
    ACCOUNT_SETUP_UNAVAILABLE_DETAIL,
    INVALID_ACTIVATION_CODE,
    INVALID_ACTIVATION_DETAIL,
    PASSWORD_VALIDATION_CODE,
    CommunityAccountSetupUnavailable,
    CommunityPasswordValidationError,
    InvalidCommunityActivation,
    check_community_activation,
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
    is_community_eligible_user,
)
from notifications.exceptions import TransactionalEmailError
from notifications.services import send_transactional_email


logger = logging.getLogger(__name__)
User = get_user_model()

COMMUNITY_LOGIN_INVALID_CODE = "INVALID_CREDENTIALS"
COMMUNITY_LOGIN_INVALID_DETAIL = "Email or password is incorrect."
COMMUNITY_ACCESS_UNAVAILABLE_CODE = "COMMUNITY_ACCESS_UNAVAILABLE"
COMMUNITY_ACCESS_UNAVAILABLE_DETAIL = "Community access is not available for this account."
COMMUNITY_PASSWORD_RESET_DETAIL = "If an eligible Elevate MK account exists for that email address, we've sent password reset instructions."


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
class CommunityPasswordResetRequestView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "community_password_reset"

    @extend_schema(
        operation_id="community_password_reset_request",
        summary="Request Community password-reset instructions",
        request=PasswordResetRequestSerializer,
        responses={200: OpenApiResponse(description="Generic reset-request response.")},
        auth=[],
        tags=["Community"],
    )
    def post(self, request):
        serializer = PasswordResetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = User.objects.select_related("person").filter(email=serializer.validated_data["email"]).first()
        if user and user.is_active and user.has_usable_password() and is_community_eligible_user(user):
            try:
                send_transactional_email(
                    recipient_email=user.email,
                    recipient_name=user.get_full_name() or None,
                    template_id=settings.BREVO_COMMUNITY_PASSWORD_RESET_TEMPLATE_ID,
                    template_params={"reset_url": build_password_reset_url(user=user, frontend_url=settings.COMMUNITY_FRONTEND_URL)},
                )
            except TransactionalEmailError:
                logger.warning("Community password reset email delivery failed.")
        return Response({"detail": COMMUNITY_PASSWORD_RESET_DETAIL}, status=status.HTTP_200_OK)


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
class CommunityLoginView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "community_login"

    @extend_schema(
        operation_id="community_login",
        summary="Sign in to the Community",
        request=LoginSerializer,
        responses={
            200: CommunityCurrentUserSerializer,
            400: OpenApiResponse(description="Invalid credentials."),
            403: OpenApiResponse(description="Community access unavailable."),
            429: OpenApiResponse(description="Too many login attempts."),
        },
        auth=[],
        tags=["Community"],
    )
    def post(self, request):
        serializer = LoginSerializer(data=request.data, context={"request": request})
        if not serializer.is_valid():
            record_auth_audit_or_raise(
                action=AuditEvent.Action.LOGIN_FAILED,
                actor_user=None,
                entity_type="Authentication",
                entity_id=None,
            )
            return Response(
                {"code": COMMUNITY_LOGIN_INVALID_CODE, "detail": COMMUNITY_LOGIN_INVALID_DETAIL},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user = serializer.validated_data["user"]
        if not is_community_eligible_user(user):
            record_auth_audit_or_raise(
                action=AuditEvent.Action.LOGIN_FAILED,
                actor_user=user,
                entity_type="Authentication",
                entity_id=user.id,
                metadata={"authentication": "community", "outcome": "access_unavailable"},
            )
            return Response(
                {"code": COMMUNITY_ACCESS_UNAVAILABLE_CODE, "detail": COMMUNITY_ACCESS_UNAVAILABLE_DETAIL},
                status=status.HTTP_403_FORBIDDEN,
            )

        login(request, user)
        record_auth_audit_or_raise(
            action=AuditEvent.Action.LOGIN_SUCCEEDED,
            actor_user=user,
            entity_type="Authentication",
            entity_id=user.id,
            metadata={"authentication": "community"},
        )
        person = user.person
        return Response(
            CommunityCurrentUserSerializer({
                "id": user.id,
                "first_name": person.first_name,
                "last_name": person.last_name,
            }).data,
            status=status.HTTP_200_OK,
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
    def get(self, request, invitation_id, token):
        try:
            check_community_activation(invitation_id=invitation_id, raw_token=token)
        except InvalidCommunityActivation:
            return Response(
                {"code": INVALID_ACTIVATION_CODE, "detail": INVALID_ACTIVATION_DETAIL},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response({"usable": True}, status=status.HTTP_200_OK)

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
        if not is_community_eligible_user(request.user):
            return Response({"detail": "Community access is unavailable."}, status=status.HTTP_403_FORBIDDEN)
        person = request.user.person
        return Response(CommunityCurrentUserSerializer({"id": request.user.id, "first_name": person.first_name, "last_name": person.last_name}).data)
