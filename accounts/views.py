import logging

from django.conf import settings
from django.contrib.auth import get_user_model, login, logout
from django.contrib.auth.tokens import default_token_generator
from django.db import transaction
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.csrf import csrf_protect
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from accounts.serializers import (
    AuthErrorSerializer,
    CurrentUserSerializer,
    DetailSerializer,
    InvalidCredentialsError,
    LoginSerializer,
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer,
)
from audit.models import AuditEvent
from audit.services import record_audit_event
from notifications.exceptions import TransactionalEmailError
from notifications.services import send_transactional_email


logger = logging.getLogger(__name__)
User = get_user_model()
PASSWORD_RESET_REQUEST_DETAIL = "If an account exists for that email address, password reset instructions have been sent."
INVALID_RESET_TOKEN_DETAIL = "This password reset link is invalid or has expired."


class AuditPersistenceError(Exception):
    pass


def record_auth_audit_or_raise(**kwargs):
    try:
        record_audit_event(**kwargs)
    except Exception as exc:
        raise AuditPersistenceError from exc


@method_decorator(ensure_csrf_cookie, name="dispatch")
class CsrfView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    @extend_schema(
        operation_id="auth_csrf",
        summary="Bootstrap a CSRF cookie",
        description=(
            "Bootstraps Django CSRF protection for cookie and session-based clients by "
            "forcing Django to issue a csrftoken cookie. This endpoint does not create "
            "a login session and does not authenticate the caller. Clients should send "
            "the csrftoken value back in the X-CSRFToken header on subsequent unsafe requests."
        ),
        request=None,
        responses={
            200: OpenApiResponse(
                response=DetailSerializer,
                description="CSRF cookie issued in the csrftoken response cookie.",
            ),
        },
        tags=["Authentication"],
        auth=[],
    )
    def get(self, request):
        return Response({"detail": "CSRF cookie set."}, status=status.HTTP_200_OK)


@method_decorator(csrf_protect, name="dispatch")
class LoginView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    @extend_schema(
        operation_id="auth_login",
        summary="Log in with email and password",
        description=(
            "Authenticates a user with the custom email-based account model and establishes "
            "a normal Django server-side session. This endpoint is CSRF-protected. "
            "Successful and rejected credential-authentication attempts are recorded in the internal audit store."
        ),
        request=LoginSerializer,
        responses={
            200: CurrentUserSerializer,
            400: OpenApiResponse(
                response=AuthErrorSerializer,
                description="Generic authentication failure or request validation error.",
            ),
        },
        tags=["Authentication"],
        auth=[],
    )
    def post(self, request):
        serializer = LoginSerializer(data=request.data, context={"request": request})
        try:
            serializer.is_valid(raise_exception=True)
        except InvalidCredentialsError:
            try:
                record_auth_audit_or_raise(
                    action=AuditEvent.Action.LOGIN_FAILED,
                    actor_user=None,
                    entity_type="Authentication",
                    entity_id=None,
                )
            except AuditPersistenceError:
                raise AuditPersistenceError
            raise

        user = serializer.validated_data["user"]
        login(request, user)

        try:
            record_auth_audit_or_raise(
                action=AuditEvent.Action.LOGIN_SUCCEEDED,
                actor_user=user,
                entity_type="User",
                entity_id=user.id,
            )
        except AuditPersistenceError:
            logout(request)
            raise

        return Response(CurrentUserSerializer(user).data, status=status.HTTP_200_OK)


@method_decorator(csrf_protect, name="dispatch")
class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="auth_logout",
        summary="Log out the current session",
        description=(
            "Logs out the currently authenticated Django session and invalidates its "
            "server-side authenticated state. This endpoint is CSRF-protected. "
            "Successful authenticated logout is recorded in the internal audit store."
        ),
        request=None,
        responses={
            204: OpenApiResponse(description="Logout succeeded. No response body."),
            401: OpenApiResponse(description="Authentication credentials were not provided."),
        },
        tags=["Authentication"],
    )
    def post(self, request):
        user = request.user
        try:
            record_auth_audit_or_raise(
                action=AuditEvent.Action.LOGOUT,
                actor_user=user,
                entity_type="User",
                entity_id=user.id,
            )
        except AuditPersistenceError:
            raise

        logout(request)
        return Response(status=status.HTTP_204_NO_CONTENT)


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="auth_me",
        summary="Get the current authenticated user",
        description=(
            "Returns the concise authenticated account summary for the current Django "
            "session, including the linked Person record and active operational staff role codes."
        ),
        responses={
            200: CurrentUserSerializer,
            401: OpenApiResponse(description="Authentication credentials were not provided."),
        },
        tags=["Authentication"],
    )
    def get(self, request):
        return Response(CurrentUserSerializer(request.user).data, status=status.HTTP_200_OK)


@method_decorator(csrf_protect, name="dispatch")
class PasswordResetRequestView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "password_reset"

    @extend_schema(
        operation_id="auth_password_reset_request",
        summary="Request password-reset instructions",
        description="Public, CSRF-protected, enumeration-resistant endpoint. It always returns the same success response for well-formed email input and never logs the user in.",
        request=PasswordResetRequestSerializer,
        responses={200: DetailSerializer, 400: OpenApiResponse(response=AuthErrorSerializer)},
        tags=["Authentication"], auth=[],
    )
    def post(self, request):
        serializer = PasswordResetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = User.objects.filter(email=serializer.validated_data["email"]).first()
        if user and user.is_active and user.has_usable_password():
            reset_url = self._reset_url(user)
            try:
                send_transactional_email(
                    recipient_email=user.email,
                    recipient_name=user.get_full_name() or None,
                    template_id=settings.BREVO_PASSWORD_RESET_TEMPLATE_ID,
                    template_params={"reset_url": reset_url},
                )
            except TransactionalEmailError:
                logger.warning("Password reset email delivery failed.")
        return Response({"detail": PASSWORD_RESET_REQUEST_DETAIL})

    def _reset_url(self, user):
        base_url = settings.CRM_FRONTEND_URL.rstrip("/")
        uid = urlsafe_base64_encode(force_bytes(user.pk))
        token = default_token_generator.make_token(user)
        return f"{base_url}/reset-password/{uid}/{token}"


@method_decorator(csrf_protect, name="dispatch")
class PasswordResetConfirmView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "password_reset_confirm"

    @extend_schema(
        operation_id="auth_password_reset_confirm",
        summary="Reset a password using a valid reset link",
        description="Public, CSRF-protected endpoint. Invalid, expired, consumed, malformed, and inactive-account links return the same controlled 400 response. Successful reset does not log the user in.",
        request=PasswordResetConfirmSerializer,
        responses={200: DetailSerializer, 400: OpenApiResponse(response=AuthErrorSerializer)},
        tags=["Authentication"], auth=[],
    )
    def post(self, request):
        serializer = PasswordResetConfirmSerializer(data=request.data)
        if not serializer.is_valid():
            data = serializer.errors
            if str(data.get("code", [""])[0]) == "invalid_password_reset_token":
                return Response({"code": "invalid_password_reset_token", "detail": INVALID_RESET_TOKEN_DETAIL}, status=status.HTTP_400_BAD_REQUEST)
            return Response(data, status=status.HTTP_400_BAD_REQUEST)
        user = serializer.validated_data["user"]
        with transaction.atomic():
            user.set_password(serializer.validated_data["new_password"])
            user.save(update_fields=["password"])
            record_auth_audit_or_raise(
                action=AuditEvent.Action.PASSWORD_RESET,
                actor_user=None,
                entity_type="User",
                entity_id=user.id,
                metadata={"user_id": str(user.id), "person_id": str(user.person_id)},
            )
        return Response({"detail": "Your password has been reset successfully."})
