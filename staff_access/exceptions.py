from django.core.exceptions import ValidationError


class FinalCrmAdminProtectionError(ValidationError):
    """Raised when a mutation would remove the final operational CRM admin."""

    def __init__(self):
        super().__init__(
            "At least one active CRM_ADMIN assignment must remain.",
            code="final_crm_admin",
        )
