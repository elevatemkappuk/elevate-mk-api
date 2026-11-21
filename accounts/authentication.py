from rest_framework.authentication import SessionAuthentication as DRFSessionAuthentication
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication

from accounts.services import user_is_active_mobile_member


class SessionAuthentication(DRFSessionAuthentication):
    def authenticate_header(self, request):
        return "Session"


# CRM resources intentionally accept Django sessions only. Keep the global
# DRF authentication defaults unchanged for mobile and community endpoints.
CRM_SESSION_AUTHENTICATION_CLASSES = (SessionAuthentication,)


class MobileJWTAuthentication(JWTAuthentication):
    """JWT authentication for mobile/member endpoints only."""

    def authenticate(self, request):
        result = super().authenticate(request)
        if result is None:
            return None

        user, token = result
        if not user_is_active_mobile_member(user):
            raise AuthenticationFailed("Authentication credentials were not provided.")

        return user, token
