class BrevoMarketingError(Exception):
    """Base class for safe Brevo marketing integration failures."""


class BrevoMarketingConfigurationError(BrevoMarketingError):
    """The backend-only Brevo marketing configuration is missing or invalid."""


class BrevoMarketingAuthenticationError(BrevoMarketingError):
    """Brevo rejected the configured API credentials."""


class BrevoMarketingAccessError(BrevoMarketingError):
    """The configured credentials cannot access the requested Brevo API resource."""


class BrevoMarketingTemporaryError(BrevoMarketingError):
    """Brevo or the network could not complete the request temporarily."""


class BrevoMarketingPropagationDelay(BrevoMarketingTemporaryError):
    """Brevo has not indexed newly-added list members yet."""


class BrevoMarketingAPIError(BrevoMarketingError):
    """Brevo returned an otherwise controlled API failure."""


class BrevoMarketingValidationError(BrevoMarketingAPIError):
    """Brevo rejected a request because its payload failed validation."""


def is_invalid_phone_error(error):
    """Identify only Brevo's explicit invalid-phone validation response."""
    if not isinstance(error, BrevoMarketingValidationError):
        return False
    message = " ".join(str(error).casefold().split())
    return any(phrase in message for phrase in ("invalid phone number", "invalid phone", "invalid mobile"))


class BrevoMarketingRateLimitError(BrevoMarketingTemporaryError):
    """Brevo rate-limited the request."""


class BrevoMarketingIdentityConflictError(BrevoMarketingError):
    """A Brevo contact identity conflicts with an existing CRM reference."""
