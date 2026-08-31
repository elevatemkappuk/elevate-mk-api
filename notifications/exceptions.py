class TransactionalEmailError(Exception):
    """A provider-independent transactional email delivery failure."""


class TransactionalEmailConfigurationError(TransactionalEmailError):
    """Raised only when email delivery is attempted without required settings."""
