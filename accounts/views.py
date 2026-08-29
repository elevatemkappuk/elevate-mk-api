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
    AuthenticatedUserSerializer,
    AuthErrorSerializer,
    DetailSerializer,
    LoginSerializer,
)


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
            200: AuthenticatedUserSerializer,
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
        serializer.is_valid(raise_exception=True)

        user = serializer.validated_data["user"]
        login(request, user)

        return Response(AuthenticatedUserSerializer(user).data, status=status.HTTP_200_OK)


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
        logout(request)
        return Response(status=status.HTTP_204_NO_CONTENT)


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="auth_me",
        summary="Get the current authenticated user",
        description=(
            "Returns the concise authenticated account summary for the current Django "
            "session, including the linked Person record."
        ),
        responses={
            200: AuthenticatedUserSerializer,
            401: OpenApiResponse(description="Authentication credentials were not provided."),
        },
        tags=["Authentication"],
    )
    def get(self, request):
        return Response(AuthenticatedUserSerializer(request.user).data, status=status.HTTP_200_OK)
