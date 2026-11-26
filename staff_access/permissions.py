from django.db import transaction
import logging
import hashlib
from rest_framework.permissions import BasePermission

from audit.models import AuditEvent
from audit.services import record_audit_event
from staff_access.models import (
    CapabilityDefinition,
    RoleCapabilityDefault,
    StaffCapabilityAssignment,
    StaffCapabilityRestriction,
    StaffRole,
    StaffRoleAssignment,
)


PRIVACY_CAPABILITY_CODES = frozenset(CapabilityDefinition.canonical_codes())
CRM_EFFECTIVE_ACCESS_DISCLOSURE_ALLOWLIST = frozenset({
    CapabilityDefinition.STAFF_CAPABILITY_ADMIN,
    CapabilityDefinition.PRIVACY_AUDIT_READ,
    CapabilityDefinition.LEGAL_HOLD_ADMIN,
    CapabilityDefinition.PERSON_CONTACT_READ,
    CapabilityDefinition.PERSON_DEMOGRAPHIC_READ,
    CapabilityDefinition.PERSON_EXPORT,
})
CRM_OPERATIONAL_ROLE_CODES = tuple(code for code, _name in StaffRole.CANONICAL_ROLES)
GOVERNANCE_DENIAL_ACTION = AuditEvent.Action.CAPABILITY_PERMISSION_MUTATED
logger = logging.getLogger(__name__)


def _governance_target_reference(request):
    data = getattr(request, "data", {}) or {}
    for key in ("target_user_id", "role_id", "proposal_id"):
        value = data.get(key)
        if value not in (None, ""):
            return hashlib.sha256(f"governance:{key}:{value}".encode()).hexdigest()[:24]
    return "governance"


def record_governance_authorization_failure(*, request, denial_code, operation=None):
    allowed_operations = {
        "ROLE_DEFAULT_ENABLE", "ROLE_DEFAULT_DISABLE", "INDIVIDUAL_GRANT",
        "INDIVIDUAL_RESTORE", "INDIVIDUAL_SUSPEND", "INDIVIDUAL_REVOKE",
        "INDIVIDUAL_RESTRICT", "INDIVIDUAL_UNRESTRICT", "approve", "reject", "cancel",
    }
    actor = getattr(request, "user", None)
    if not getattr(actor, "is_authenticated", False):
        actor = None
    normalized_operation = operation if isinstance(operation, str) and operation in allowed_operations else "UNKNOWN"
    try:
        record_audit_event(
            action=GOVERNANCE_DENIAL_ACTION,
            actor_user=actor,
            entity_type="GovernanceAuthorization",
            entity_id=_governance_target_reference(request),
            metadata={
                "event_type": "GOVERNANCE_AUTHORIZATION_DENIED",
                "denial_code": str(denial_code),
                "operation": normalized_operation,
                "outcome": "DENIED",
            },
        )
    except Exception:
        # Preserve the original authorization response if audit persistence is unavailable;
        # the denied operation has not entered a governance mutation transaction.
        logger.exception("Failed to persist governance authorization denial audit event.")


def get_active_staff_role_codes_for_user(user):
    if not getattr(user, "is_authenticated", False):
        return []

    return list(
        StaffRoleAssignment.objects.active()
        .filter(user=user)
        .order_by("role__code")
        .values_list("role__code", flat=True)
    )


def user_has_any_active_staff_role(user, allowed_role_codes=None):
    role_codes = get_active_staff_role_codes_for_user(user)
    if not role_codes:
        return False
    if allowed_role_codes is None:
        return True

    allowed_role_codes = set(allowed_role_codes)
    return any(role_code in allowed_role_codes for role_code in role_codes)


def get_effective_capability_codes_for_user(user, *, requested_codes=None, request=None):
    """Return backend-authoritative effective capability codes.

    Role defaults and active individual grants are additive. An active explicit
    restriction has precedence over both. Suspended and revoked grants are not
    effective, and removing a restriction does not change their stored state.
    Django superusers receive the approved full privacy-capability bypass, which
    is transactionally audited before the result is returned.
    """
    if not getattr(user, "is_authenticated", False) or not getattr(user, "is_active", False):
        return set()

    requested = (
        set(CapabilityDefinition.canonical_codes())
        if requested_codes is None
        else set(requested_codes)
    )
    invalid = requested.difference(CapabilityDefinition.canonical_codes())
    if invalid:
        raise ValueError(f"Unknown capability code(s): {', '.join(sorted(invalid))}")

    if getattr(user, "is_superuser", False):
        request_id = None
        ip_address = None
        if request is not None:
            request_id = request.headers.get("X-Request-ID") or request.META.get("HTTP_X_REQUEST_ID")
            ip_address = request.META.get("REMOTE_ADDR")
        with transaction.atomic():
            record_audit_event(
                action=AuditEvent.Action.PRIVACY_SUPERUSER_BYPASS,
                actor_user=user,
                entity_type="PrivacyCapabilityEvaluation",
                entity_id=str(user.pk),
                metadata={
                    "capability_codes": sorted(requested),
                    "evaluator": "django_superuser_bypass",
                },
                request_id=request_id,
                ip_address=ip_address,
            )
        return requested

    active_role_codes = get_active_staff_role_codes_for_user(user)
    if not active_role_codes:
        return set()
    role_default_codes = set(
        RoleCapabilityDefault.objects.filter(
            role__code__in=active_role_codes,
            role__is_active=True,
            capability__is_active=True,
            is_enabled=True,
            capability__code__in=requested,
        ).values_list("capability__code", flat=True)
    )
    active_grant_codes = set(
        StaffCapabilityAssignment.objects.active()
        .filter(user=user, capability__is_active=True, capability__code__in=requested)
        .values_list("capability__code", flat=True)
    )
    restricted_codes = set(
        StaffCapabilityRestriction.objects.active()
        .filter(user=user, capability__code__in=requested)
        .values_list("capability__code", flat=True)
    )
    return (role_default_codes | active_grant_codes) - restricted_codes


def user_has_capability(user, capability_code, *, request=None):
    return capability_code in get_effective_capability_codes_for_user(
        user,
        requested_codes={capability_code},
        request=request,
    )


class HasAnyActiveStaffRole(BasePermission):
    message = "You do not have an active staff role."

    def has_permission(self, request, view):
        return user_has_any_active_staff_role(request.user)


class HasActiveStaffRoleCodes(BasePermission):
    message = "You do not have a permitted active staff role."
    required_role_codes = ()

    def has_permission(self, request, view):
        return user_has_any_active_staff_role(
            request.user,
            allowed_role_codes=self.required_role_codes,
        )


class HasCrmOperationalAccess(HasActiveStaffRoleCodes):
    required_role_codes = CRM_OPERATIONAL_ROLE_CODES


class HasCapability(BasePermission):
    message = "You do not have the required capability."
    required_capability = None

    def has_permission(self, request, view):
        capability = getattr(view, "required_capability", self.required_capability)
        return bool(capability) and user_has_capability(request.user, capability, request=request)
