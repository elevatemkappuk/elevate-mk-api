from staff_access.models import StaffRole
from staff_access.permissions import HasActiveStaffRoleCodes


class HasCampaignAccess(HasActiveStaffRoleCodes):
    required_role_codes = (StaffRole.CRM_ADMIN, StaffRole.CRM_MANAGER, StaffRole.CRM_VIEWER)


class HasCampaignWriteAccess(HasActiveStaffRoleCodes):
    required_role_codes = (StaffRole.CRM_ADMIN, StaffRole.CRM_MANAGER)
