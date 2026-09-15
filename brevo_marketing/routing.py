from django.conf import settings

from brevo_marketing.exceptions import BrevoMarketingConfigurationError


BREVO_PROVIDER = "BREVO"
SUPPORTED_MARKETING_SYNC_PROVIDERS = frozenset({BREVO_PROVIDER})


def get_active_marketing_sync_provider():
    provider = str(getattr(settings, "MARKETING_SYNC_PROVIDER", "")).strip().upper()
    if provider not in SUPPORTED_MARKETING_SYNC_PROVIDERS:
        raise BrevoMarketingConfigurationError(
            "MARKETING_SYNC_PROVIDER is unsupported; configure BREVO."
        )
    return provider
