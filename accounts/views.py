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
from django.middleware.csrf import get_token
from django.utils import timezone
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.views import TokenRefreshView

from accounts.serializers import (
    AuthErrorSerializer,
    CurrentUserSerializer,
    CsrfBootstrapSerializer,
    DetailSerializer,
    InvalidCredentialsError,
    LoginSerializer,
    MobileTokenRefreshSerializer,
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer,
    InvalidRefreshTokenError,
    RefreshTokenLogoutSerializer,
)
from audit.models import AuditEvent
from audit.services import record_audit_event
from accounts.authentication import AuthenticatedSessionAuthentication, CrmSessionAuthentication, MobileJWTAuthentication, SessionAuthentication
from accounts.authentication import CRM_SESSION_CREATED_AT, CRM_SESSION_LAST_ACTIVITY
from accounts.revocation import issue_mobile_token_pair, revoke_mobile_session, revoke_user_credentials
from notifications.exceptions import TransactionalEmailError
from notifications.services import send_transactional_email


logger = logging.getLogger(__name__)
User = get_user_model()
PASSWORD_RESET_REQUEST_DETAIL = "If an account exists for that email address, password reset instructions have been sent."
INVALID_RESET_TOKEN_DETAIL = "This password reset link is invalid or has expired."


class AuditPersistenceError(Exception):
    pass


def build_password_reset_url(*, user, frontend_url):
    base_url = frontend_url.rstrip("/")
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    return f"{base_url}/reset-password/{uid}/{token}"


def record_auth_audit_or_raise(*, request=None, **kwargs):
    if request is not None:
        request_id = request.headers.get("X-Request-ID") or request.META.get("HTTP_X_REQUEST_ID")
        if request_id:
            kwargs.setdefault("request_id", request_id)
        kwargs.setdefault("ip_address", request.META.get("REMOTE_ADDR"))
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
                response=CsrfBootstrapSerializer,
                description="CSRF cookie issued in the csrftoken response cookie and token returned for cross-origin clients.",
            ),
        },
        tags=["Authentication"],
        auth=[],
    )
    def get(self, request):
        return Response(
            {"detail": "CSRF cookie set.", "csrf_token": get_token(request)},
            status=status.HTTP_200_OK,
        )


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
        if not serializer.is_valid():
            detail_error = serializer.errors.get("detail")
            if (
                isinstance(detail_error, list)
                and len(detail_error) == 1
                and getattr(detail_error[0], "code", None) == InvalidCredentialsError.default_code
            ):
                try:
                    record_auth_audit_or_raise(
                        request=request,
                        action=AuditEvent.Action.LOGIN_FAILED,
                        actor_user=None,
                        entity_type="Authentication",
                        entity_id=None,
                    )
                except AuditPersistenceError:
                    raise AuditPersistenceError
            serializer.is_valid(raise_exception=True)

        user = serializer.validated_data["user"]
        login(request, user)
        request.session["elevate_auth_context"] = "crm"
        request.session["elevate_auth_version"] = user.auth_version
        request.session["elevate_crm_session_version"] = user.crm_session_version
        now = timezone.now().timestamp()
        request.session[CRM_SESSION_CREATED_AT] = now
        request.session[CRM_SESSION_LAST_ACTIVITY] = now

        try:
            record_auth_audit_or_raise(
                request=request,
                action=AuditEvent.Action.LOGIN_SUCCEEDED,
                actor_user=user,
                entity_type="User",
                entity_id=user.id,
            )
        except AuditPersistenceError:
            logout(request)
            raise

        return Response(CurrentUserSerializer(user).data, status=status.HTTP_200_OK)


class TokenLoginView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    @extend_schema(
        operation_id="auth_token_login",
        summary="Issue mobile access and refresh tokens",
        description=(
            "Authenticates a user with the normalized email/password rules used by the "
            "session login endpoint and returns a short-lived access token plus a "
            "rotating refresh token. This endpoint does not create a Django session."
        ),
        request=LoginSerializer,
        responses={
            200: OpenApiResponse(description="Access and refresh tokens."),
            400: OpenApiResponse(response=AuthErrorSerializer, description="Invalid credentials."),
        },
        tags=["Authentication"],
        auth=[],
    )
    def post(self, request):
        serializer = LoginSerializer(
            data=request.data,
            context={"request": request, "require_active_mobile_membership": True},
        )
        if not serializer.is_valid():
            detail_error = serializer.errors.get("detail")
            if (
                isinstance(detail_error, list)
                and len(detail_error) == 1
                and getattr(detail_error[0], "code", None) == InvalidCredentialsError.default_code
            ):
                record_auth_audit_or_raise(
                    request=request,
                    action=AuditEvent.Action.LOGIN_FAILED,
                    actor_user=None,
                    entity_type="Authentication",
                    entity_id=None,
                )
            serializer.is_valid(raise_exception=True)

        user = serializer.validated_data["user"]
        with transaction.atomic():
            refresh = issue_mobile_token_pair(user=user)
            record_auth_audit_or_raise(
                request=request,
                action=AuditEvent.Action.LOGIN_SUCCEEDED,
                actor_user=user,
                entity_type="User",
                entity_id=user.id,
                metadata={"authentication": "jwt"},
            )
        return Response(
            {"access": str(refresh.access_token), "refresh": str(refresh)},
            status=status.HTTP_200_OK,
        )


class TokenRefreshEndpointView(TokenRefreshView):
    permission_classes = [AllowAny]
    authentication_classes = []
    serializer_class = MobileTokenRefreshSerializer

    @extend_schema(
        operation_id="auth_token_refresh",
        summary="Rotate a mobile refresh token",
        description="Returns a new short-lived access token and rotates the supplied refresh token.",
        responses={
            200: OpenApiResponse(description="Rotated access and refresh tokens."),
            401: OpenApiResponse(description="Invalid, expired, or already rotated refresh token."),
        },
        tags=["Authentication"],
        auth=[],
    )
    def post(self, request, *args, **kwargs):
        return super().post(request, *args, **kwargs)


class TokenLogoutView(APIView):
    permission_classes = [IsAuthenticated]
    authentication_classes = [JWTAuthentication]

    @extend_schema(
        operation_id="auth_token_logout",
        summary="Revoke a mobile refresh token",
        description=(
            "Requires a valid JWT access token and the matching refresh token in the request body. "
            "The refresh token is blacklisted and the current device session is revoked. "
            "The supplied access token is rejected immediately after logout. Other mobile sessions remain valid."
        ),
        request=RefreshTokenLogoutSerializer,
        responses={
            204: OpenApiResponse(description="Refresh token revoked. No response body."),
            400: OpenApiResponse(description="The refresh token field is missing or malformed."),
            401: OpenApiResponse(description="JWT access or refresh authentication failed."),
            403: OpenApiResponse(description="The refresh token belongs to another user."),
        },
        tags=["Authentication"],
    )
    def post(self, request):
        serializer = RefreshTokenLogoutSerializer(
            data=request.data,
            context={"request": request},
        )
        if not serializer.is_valid():
            detail_error = serializer.errors.get("detail")
            if (
                isinstance(detail_error, list)
                and len(detail_error) == 1
                and getattr(detail_error[0], "code", None) == InvalidRefreshTokenError.default_code
            ):
                return Response(
                    {
                        "code": InvalidRefreshTokenError.default_code,
                        "detail": "The refresh token is invalid, expired, or already revoked.",
                    },
                    status=status.HTTP_401_UNAUTHORIZED,
                )
            if (
                isinstance(detail_error, list)
                and len(detail_error) == 1
                and getattr(detail_error[0], "code", None) == "refresh_token_user_mismatch"
            ):
                return Response(
                    {"code": "refresh_token_user_mismatch", "detail": str(detail_error[0])},
                    status=status.HTTP_403_FORBIDDEN,
                )
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            revoke_mobile_session(
                session_id=serializer.validated_data["mobile_session_id"],
                reason="mobile_logout",
            )
            serializer.validated_data["token"].blacklist()
            record_auth_audit_or_raise(
                request=request,
                action=AuditEvent.Action.LOGOUT,
                actor_user=request.user,
                entity_type="User",
                entity_id=request.user.id,
                metadata={
                    "authentication": "jwt",
                    "mobile_session_revoked": True,
                    "mobile_session_id": str(serializer.validated_data["mobile_session_id"]),
                    "revocation_reason": "mobile_logout",
                    "refresh_token_revoked": True,
                },
            )

        return Response(status=status.HTTP_204_NO_CONTENT)


@method_decorator(csrf_protect, name="dispatch")
class LogoutView(APIView):
    permission_classes = [IsAuthenticated]
    authentication_classes = [AuthenticatedSessionAuthentication]

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
        logout(request)
        try:
            record_auth_audit_or_raise(
                request=request,
                action=AuditEvent.Action.LOGOUT,
                actor_user=user,
                entity_type="User",
                entity_id=user.id,
            )
        except AuditPersistenceError:
            raise

        return Response(status=status.HTTP_204_NO_CONTENT)


class MeView(APIView):
    permission_classes = [IsAuthenticated]
    authentication_classes = [CrmSessionAuthentication, MobileJWTAuthentication]

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
        return build_password_reset_url(user=user, frontend_url=settings.CRM_FRONTEND_URL)


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
            revoke_user_credentials(user=user, reason="password_reset", request=request)
            record_auth_audit_or_raise(
                request=request,
                action=AuditEvent.Action.PASSWORD_RESET,
                actor_user=None,
                entity_type="User",
                entity_id=user.id,
                metadata={"user_id": str(user.id), "person_id": str(user.person_id)},
            )
        return Response({"detail": "Your password has been reset successfully."})
