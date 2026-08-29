from rest_framework.permissions import BasePermission

from staff_access.models import StaffRoleAssignment


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
