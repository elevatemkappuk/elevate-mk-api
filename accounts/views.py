from django.contrib.auth import login, logout
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.csrf import csrf_protect
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.serializers import (
    AuthErrorSerializer,
    CurrentUserSerializer,
    DetailSerializer,
    InvalidCredentialsError,
    LoginSerializer,
)
from audit.models import AuditEvent
from audit.services import record_audit_event


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
            "a normal Django server-side session. This endpoint is CSRF-protected."
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
                return Response(
                    {"detail": ["Authentication could not be completed right now."]},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )
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
            return Response(
                {"detail": ["Authentication could not be completed right now."]},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(CurrentUserSerializer(user).data, status=status.HTTP_200_OK)


@method_decorator(csrf_protect, name="dispatch")
class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="auth_logout",
        summary="Log out the current session",
        description=(
            "Logs out the currently authenticated Django session and invalidates its "
            "server-side authenticated state. This endpoint is CSRF-protected."
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
            return Response(
                {"detail": "Logout could not be completed right now."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

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
