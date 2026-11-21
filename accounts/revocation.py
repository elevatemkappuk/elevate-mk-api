from django.db.models import F
from django.utils import timezone
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import MobileAuthSession, User


AUTH_VERSION_CLAIM = "auth_version"
MOBILE_SESSION_CLAIM = "mobile_session_id"


def issue_mobile_token_pair(*, user):
    mobile_session = MobileAuthSession.objects.create(user=user)
    refresh = RefreshToken.for_user(user)
    refresh[AUTH_VERSION_CLAIM] = user.auth_version
    refresh[MOBILE_SESSION_CLAIM] = str(mobile_session.pk)
    return refresh


def validate_mobile_token_claims(*, user, token):
    try:
        auth_version = int(token[AUTH_VERSION_CLAIM])
        mobile_session_id = str(token[MOBILE_SESSION_CLAIM])
    except (KeyError, TypeError, ValueError) as error:
        raise AuthenticationFailed("Authentication credentials were not provided.") from error

    if auth_version != user.auth_version:
        raise AuthenticationFailed("Authentication credentials were not provided.")

    if not MobileAuthSession.objects.filter(
        pk=mobile_session_id,
        user_id=user.pk,
        revoked_at__isnull=True,
    ).exists():
        raise AuthenticationFailed("Authentication credentials were not provided.")

    return mobile_session_id


def revoke_mobile_session(*, session_id, reason):
    return MobileAuthSession.objects.filter(
        pk=session_id,
        revoked_at__isnull=True,
    ).update(
        revoked_at=timezone.now(),
        revoked_reason=reason,
    )


def revoke_user_credentials(*, user, reason):
    """Invalidate all credentials issued for a user without exposing secrets."""
    now = timezone.now()
    User.objects.filter(pk=user.pk).update(auth_version=F("auth_version") + 1)
    MobileAuthSession.objects.filter(
        user_id=user.pk,
        revoked_at__isnull=True,
    ).update(
        revoked_at=now,
        revoked_reason=reason,
    )
    user.refresh_from_db(fields=["auth_version", "crm_session_version", "is_active"])


def invalidate_crm_sessions(*, user_ids):
    """Invalidate CRM sessions while preserving unrelated mobile credentials."""
    user_ids = {int(user_id) for user_id in user_ids if user_id}
    if not user_ids:
        return 0
    return User.objects.filter(pk__in=user_ids).update(
        crm_session_version=F("crm_session_version") + 1,
    )


def handle_user_active_state_change(*, user, was_active, actor_user=None):
    from audit.models import AuditEvent
    from audit.services import record_audit_event

    if was_active and not user.is_active:
        revoke_user_credentials(user=user, reason="account_deactivated")
        record_audit_event(
            action=AuditEvent.Action.ACCOUNT_DISABLED,
            actor_user=actor_user,
            entity_type="User",
            entity_id=user.pk,
            metadata={"reason": "account_deactivated", "credentials_revoked": True},
        )
    elif was_active is False and user.is_active:
        record_audit_event(
            action=AuditEvent.Action.ACCOUNT_REENABLED,
            actor_user=actor_user,
            entity_type="User",
            entity_id=user.pk,
            metadata={"reason": "account_reactivated", "fresh_authentication_required": True},
        )
