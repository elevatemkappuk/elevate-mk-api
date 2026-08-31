from django.db import transaction

from staff_access.exceptions import FinalCrmAdminProtectionError
from staff_access.models import StaffRole, StaffRoleAssignment


def ensure_staff_role_can_be_deactivated(role):
    if role.code == StaffRole.CRM_ADMIN:
        raise FinalCrmAdminProtectionError()


def deactivate_staff_role(role):
    """Deactivate a non-canonical-admin role through the Staff Access domain."""
    with transaction.atomic():
        locked_role = StaffRole.objects.select_for_update().get(pk=role.pk)
        ensure_staff_role_can_be_deactivated(locked_role)
        locked_role.is_active = False
        locked_role.save(update_fields=["is_active", "updated_at"])
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

    return active_assignments
