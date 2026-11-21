from rest_framework.authentication import SessionAuthentication as DRFSessionAuthentication
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication

from accounts.revocation import validate_mobile_token_claims
from accounts.services import user_is_active_mobile_member


class SessionAuthentication(DRFSessionAuthentication):
    def authenticate_header(self, request):
        return "Session"

    def authenticate(self, request):
        result = super().authenticate(request)
        if result is None:
            return None

        user, _session_auth = result
        expected_version = str(user.auth_version)
        session_version = request.session.get("elevate_auth_version")
        if session_version is None:
            request.session["elevate_auth_version"] = user.auth_version
        elif str(session_version) != expected_version:
            raise AuthenticationFailed("Authentication credentials were not provided.")

        return result


class CrmSessionAuthentication(SessionAuthentication):
    """Django session authentication boundary for Staff CRM resources."""

    def authenticate(self, request):
        result = super().authenticate(request)
        if result is None:
            return None

        user, _session_auth = result
        if request.session.get("elevate_auth_context") == "crm":
            session_version = request.session.get("elevate_crm_session_version")
            if str(session_version) != str(user.crm_session_version):
                raise AuthenticationFailed("Authentication credentials were not provided.")

        return result


# CRM resources intentionally accept Django sessions only. Keep the global
# DRF authentication defaults unchanged for mobile and community endpoints.
CRM_SESSION_AUTHENTICATION_CLASSES = (CrmSessionAuthentication,)


class MobileJWTAuthentication(JWTAuthentication):
    """JWT authentication for mobile/member endpoints only."""

    def authenticate(self, request):
        result = super().authenticate(request)
        if result is None:
            return None

        user, token = result
        if not user_is_active_mobile_member(user):
            raise AuthenticationFailed("Authentication credentials were not provided.")

        validate_mobile_token_claims(user=user, token=token)

        return user, token
