from staff_access.models import StaffRole
from staff_access.permissions import HasActiveStaffRoleCodes


class HasCommunityModerationRole(HasActiveStaffRoleCodes):
    required_role_codes = (StaffRole.CRM_ADMIN, StaffRole.CRM_MANAGER)
