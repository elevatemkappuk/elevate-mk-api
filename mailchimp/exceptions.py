class MailchimpVerificationError(Exception):
    """Base class for safe, provider-independent verification failures."""


class MailchimpConfigurationError(MailchimpVerificationError):
    """Required backend-only Mailchimp settings are missing or invalid."""


class MailchimpAuthenticationError(MailchimpVerificationError):
    """Mailchimp rejected the configured credentials."""


class MailchimpAudienceAccessError(MailchimpVerificationError):
    """The configured audience does not exist or is not accessible."""


class MailchimpTemporaryError(MailchimpVerificationError):
    """Mailchimp or the network could not complete a verification request temporarily."""


class MailchimpAPIError(MailchimpVerificationError):
    """Mailchimp returned an otherwise controlled API failure."""


class MailchimpPersonSyncError(MailchimpVerificationError):
    """Base class for controlled one-Person synchronization failures."""


class MailchimpPersonSyncConflictError(MailchimpPersonSyncError):
    """The resolved provider identity conflicts with an existing CRM reference."""
