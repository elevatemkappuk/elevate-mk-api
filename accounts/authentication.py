import logging

from django.utils import timezone
from rest_framework.authentication import SessionAuthentication as DRFSessionAuthentication
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication

from accounts.revocation import validate_mobile_token_claims
from accounts.services import user_is_active_mobile_member

logger = logging.getLogger(__name__)

CRM_SESSION_CREATED_AT = "elevate_crm_session_created_at"
CRM_SESSION_LAST_ACTIVITY = "elevate_crm_session_last_activity"
CRM_SESSION_IDLE_TIMEOUT_SECONDS = 30 * 60
CRM_SESSION_ABSOLUTE_TIMEOUT_SECONDS = 12 * 60 * 60


def _session_timestamp_is_valid(value):
    return isinstance(value, (int, float)) and value > 0


def enforce_crm_session_lifetime(request, user):
    """Enforce CRM-only idle and absolute session limits server-side."""
    now = timezone.now().timestamp()
    created_at = request.session.get(CRM_SESSION_CREATED_AT)
    last_activity = request.session.get(CRM_SESSION_LAST_ACTIVITY)

    if (
        not _session_timestamp_is_valid(created_at)
        or not _session_timestamp_is_valid(last_activity)
        or now - created_at > CRM_SESSION_ABSOLUTE_TIMEOUT_SECONDS
        or now - last_activity > CRM_SESSION_IDLE_TIMEOUT_SECONDS
    ):
        from audit.models import AuditEvent
        from audit.services import record_audit_event

        try:
            request_id = request.headers.get("X-Request-ID") or request.META.get("HTTP_X_REQUEST_ID")
            record_audit_event(
                action=AuditEvent.Action.SESSION_EXPIRED,
                actor_user=user,
                entity_type="Authentication",
                entity_id=user.pk,
                metadata={
                    "authentication": "session",
                    "surface": "crm",
                    "reason": (
                        "absolute_timeout"
                        if _session_timestamp_is_valid(created_at)
                        and now - created_at > CRM_SESSION_ABSOLUTE_TIMEOUT_SECONDS
                        else "idle_timeout"
                    ),
                },
                request_id=request_id or None,
                ip_address=request.META.get("REMOTE_ADDR"),
            )
        except Exception:
            logger.exception("Unable to persist CRM session-expiry audit event.")
        request.session.flush()
        raise AuthenticationFailed("Authentication credentials were not provided.")

    request.session[CRM_SESSION_LAST_ACTIVITY] = now
    request.session.modified = True


def _validate_session_authentication(request, result, *, allow_mobile_fallback=False):
    if result is None:
        return None

    user, _session_auth = result
    expected_version = str(user.auth_version)
    session_version = request.session.get("elevate_auth_version")
    if session_version is None or str(session_version) != expected_version:
        request.session.flush()
        if allow_mobile_fallback:
            return None
        raise AuthenticationFailed("Authentication credentials were not provided.")

    return result


class SessionAuthentication(DRFSessionAuthentication):
    def authenticate_header(self, request):
        return "Session"

    def authenticate(self, request):
        return _validate_session_authentication(request, super().authenticate(request))


class CommunitySessionAuthentication(SessionAuthentication):
    """Fail closed for stale sessions without blocking a valid mobile JWT."""

    def authenticate(self, request):
        result = DRFSessionAuthentication.authenticate(self, request)
        if result is not None and request.session.get("elevate_auth_context") != "community":
            if request.session.get("elevate_auth_context") == "crm":
                return None
            request.session.flush()
            return None
        return _validate_session_authentication(request, result, allow_mobile_fallback=True)


class CrmSessionAuthentication(SessionAuthentication):
    """Django session authentication boundary for Staff CRM resources."""

    def authenticate(self, request):
        result = super().authenticate(request)
        if result is None:
            return None

        user, _session_auth = result
        if request.session.get("elevate_auth_context") != "crm":
            raise AuthenticationFailed("Authentication credentials were not provided.")

        enforce_crm_session_lifetime(request, user)
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


# Community member endpoints accept either a versioned Django session or a
# mobile JWT whose device session and account generation remain valid. Session
# authentication stays first so DRF continues to enforce CSRF for unsafe
# cookie-authenticated requests.
COMMUNITY_AUTHENTICATION_CLASSES = (CommunitySessionAuthentication, MobileJWTAuthentication)
