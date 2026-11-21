from django.db import transaction
from django.db.models import F

from accounts.models import User
from audit.models import AuditEvent
from audit.services import record_audit_event
from staff_access.exceptions import FinalCrmAdminProtectionError
from staff_access.models import StaffRole, StaffRoleAssignment


def ensure_staff_role_can_be_deactivated(role):
    if role.code == StaffRole.CRM_ADMIN:
        raise FinalCrmAdminProtectionError()


def deactivate_staff_role(role, *, actor_user=None, request=None):
    """Deactivate a non-canonical-admin role through the Staff Access domain."""
    with transaction.atomic():
        locked_role = StaffRole.objects.select_for_update().get(pk=role.pk)
        ensure_staff_role_can_be_deactivated(locked_role)
        affected_user_ids = list(
            StaffRoleAssignment.objects.active()
            .filter(role=locked_role)
            .values_list("user_id", flat=True)
        )
        locked_role.is_active = False
        locked_role.save(update_fields=["is_active", "updated_at"])
        User.objects.filter(pk__in=affected_user_ids).update(
            crm_session_version=F("crm_session_version") + 1,
        )
        request_id = None
        ip_address = None
        if request is not None:
            request_id = request.headers.get("X-Request-ID") or request.META.get("HTTP_X_REQUEST_ID")
            ip_address = request.META.get("REMOTE_ADDR")
        record_audit_event(
            action=AuditEvent.Action.STAFF_ROLE_REVOKED,
            actor_user=actor_user,
            entity_type="StaffRole",
            entity_id=locked_role.pk,
            metadata={
                "reason": "staff_role_deactivated",
                "staff_role_code": locked_role.code,
                "affected_user_count": len(affected_user_ids),
                "crm_sessions_invalidated": True,
            },
            request_id=request_id,
            ip_address=ip_address,
        )
    return locked_role


def revoke_staff_role_assignments(*, assignments, revoked_by=None, revoked_at=None):
    """Revoke active assignments without permitting loss of the final CRM admin.

    Locking the canonical CRM_ADMIN role first serializes all supported revoke
    operations. The selected assignment rows are then locked in primary-key
    order before the effective-admin count is re-evaluated.
    """
    if hasattr(assignments, "values_list"):
        assignment_ids = assignments.values_list("pk", flat=True)
    else:
        assignment_ids = [assignment.pk for assignment in assignments if assignment.pk]

    with transaction.atomic():
        crm_admin_role = StaffRole.objects.select_for_update().get(code=StaffRole.CRM_ADMIN)
        locked_assignments = list(
            StaffRoleAssignment.objects.select_for_update()
            .select_related("role")
            .filter(pk__in=assignment_ids)
            .order_by("pk")
        )
        active_assignments = [
            assignment
            for assignment in locked_assignments
            if assignment.is_active and assignment.revoked_at is None
        ]
        effective_admin_revocations = [
            assignment
            for assignment in active_assignments
            if assignment.role_id == crm_admin_role.id and assignment.role.is_active
        ]

        if effective_admin_revocations:
            active_admin_count = StaffRoleAssignment.objects.active().filter(role=crm_admin_role).count()
            if active_admin_count <= len(effective_admin_revocations):
                raise FinalCrmAdminProtectionError()

        for assignment in active_assignments:
            assignment._revoke_unchecked(revoked_by=revoked_by, revoked_at=revoked_at)

        User.objects.filter(pk__in={assignment.user_id for assignment in active_assignments}).update(
            crm_session_version=F("crm_session_version") + 1,
        )

    return active_assignments
