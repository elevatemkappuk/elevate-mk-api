"""Backend-authoritative authorization predicates for Staff Management."""

from enum import StrEnum

from django.core.exceptions import PermissionDenied

from audit.models import AuditEvent
from audit.services import record_audit_event
from staff_access.models import StaffRole
from staff_access.models import StaffRoleAssignment
from staff_access.permissions import user_has_any_active_staff_role

STAFF_AUTHORIZATION_DENIED_ACTION = AuditEvent.Action.CAPABILITY_PERMISSION_MUTATED


class StaffOperation(StrEnum):
    ACCESS = "STAFF_MANAGEMENT_ACCESS"
    ROLE_ASSIGNMENT = "STAFF_ROLE_ASSIGNMENT"
    ROLE_CHANGE = "STAFF_ROLE_CHANGE"
    ACCESS_DEACTIVATION = "STAFF_ACCESS_DEACTIVATION"
    ACCESS_REACTIVATION = "STAFF_ACCESS_REACTIVATION"
    ACCOUNT_DISABLE = "STAFF_ACCOUNT_DISABLE"
    ACCOUNT_ENABLE = "STAFF_ACCOUNT_ENABLE"
    ADMIN_EMAIL_CHANGE = "STAFF_ADMIN_EMAIL_CHANGE"
    ADMIN_PASSWORD_RESET = "STAFF_ADMIN_PASSWORD_RESET"
    EXCEPTIONAL_RECOVERY = "STAFF_EXCEPTIONAL_RECOVERY"
    ADMIN_APPROVAL = "STAFF_ADMIN_APPROVAL"


def _is_authenticated_active(actor):
    return bool(getattr(actor, "is_authenticated", False) and getattr(actor, "is_active", False))


def is_eligible_staff_superuser(actor):
    if not _is_authenticated_active(actor) or not getattr(actor, "is_superuser", False):
        return False
    person = getattr(actor, "person", None)
    return bool(person is not None and person.archived_at is None)


def is_active_crm_administrator(actor):
    return bool(
        _is_authenticated_active(actor)
        and user_has_any_active_staff_role(actor, (StaffRole.CRM_ADMIN,))
    )


def usable_crm_admin_assignments():
    """Return assignments that satisfy operational Administrator continuity."""
    return StaffRoleAssignment.objects.active().filter(
        role__code=StaffRole.CRM_ADMIN,
        user__is_active=True,
        user__person__archived_at__isnull=True,
    )


def has_usable_crm_administrator():
    return usable_crm_admin_assignments().exists()


def can_access_staff_management(actor):
    return is_active_crm_administrator(actor) or is_eligible_staff_superuser(actor)


def is_protected_staff_target(target):
    return bool(getattr(target, "is_staff", False) or getattr(target, "is_superuser", False))


def is_self_target(actor, target):
    return bool(actor is not None and target is not None and actor.pk == target.pk)


SELF_TARGETED_OPERATIONS = frozenset({
    StaffOperation.ROLE_ASSIGNMENT,
    StaffOperation.ROLE_CHANGE,
    StaffOperation.ACCESS_DEACTIVATION,
    StaffOperation.ACCESS_REACTIVATION,
    StaffOperation.ACCOUNT_DISABLE,
    StaffOperation.ACCOUNT_ENABLE,
    StaffOperation.ADMIN_EMAIL_CHANGE,
    StaffOperation.ADMIN_PASSWORD_RESET,
    StaffOperation.EXCEPTIONAL_RECOVERY,
    StaffOperation.ADMIN_APPROVAL,
})

PROTECTED_TARGET_OPERATIONS = frozenset({
    StaffOperation.ROLE_ASSIGNMENT,
    StaffOperation.ROLE_CHANGE,
    StaffOperation.ACCESS_DEACTIVATION,
    StaffOperation.ACCESS_REACTIVATION,
    StaffOperation.ACCOUNT_DISABLE,
    StaffOperation.ACCOUNT_ENABLE,
    StaffOperation.ADMIN_EMAIL_CHANGE,
    StaffOperation.ADMIN_PASSWORD_RESET,
    StaffOperation.EXCEPTIONAL_RECOVERY,
})


def _record_denial(*, actor, target, operation, denial_code, request=None):
    metadata = {
        "event_type": "STAFF_AUTHORIZATION_DENIED",
        "operation": str(operation),
        "outcome": "DENIED",
        "denial_code": denial_code,
    }
    if target is not None:
        metadata["target_user_id"] = str(target.pk)
    request_id = None
    ip_address = None
    if request is not None:
        request_id = request.headers.get("X-Request-ID") or request.META.get("HTTP_X_REQUEST_ID")
        ip_address = request.META.get("REMOTE_ADDR")
    record_audit_event(
        action=STAFF_AUTHORIZATION_DENIED_ACTION,
        actor_user=actor if getattr(actor, "is_authenticated", False) else None,
        entity_type="StaffAuthorization",
        entity_id=str(getattr(target, "pk", "staff-management")),
        metadata=metadata,
        request_id=request_id,
        ip_address=ip_address,
    )


def authorize_staff_operation(*, actor, operation, target=None, request=None):
    """Authorize one operation; denial audit failure remains fail-closed."""
    operation = StaffOperation(operation)
    denial_code = None
    if not can_access_staff_management(actor):
        denial_code = "STAFF_MANAGEMENT_AUTHORITY_REQUIRED"
    elif target is not None and is_self_target(actor, target) and operation in SELF_TARGETED_OPERATIONS:
        denial_code = "SELF_MANAGEMENT_PROHIBITED"
    elif (
        target is not None
        and is_protected_staff_target(target)
        and not is_eligible_staff_superuser(actor)
        and operation in PROTECTED_TARGET_OPERATIONS
    ):
        denial_code = "PROTECTED_TECHNICAL_ACCOUNT"

    if denial_code is not None:
        _record_denial(
            actor=actor,
            target=target,
            operation=operation,
            denial_code=denial_code,
            request=request,
        )
        raise PermissionDenied(denial_code)
    return True
