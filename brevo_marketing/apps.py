from django.apps import AppConfig
from django.core.checks import Error, register

from brevo_marketing.routing import SUPPORTED_MARKETING_SYNC_PROVIDERS


class BrevoMarketingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "brevo_marketing"


@register()
def check_marketing_sync_provider(app_configs, **kwargs):
    from django.conf import settings

    provider = str(getattr(settings, "MARKETING_SYNC_PROVIDER", "")).strip().upper()
    if provider not in SUPPORTED_MARKETING_SYNC_PROVIDERS:
        return [Error(
            "MARKETING_SYNC_PROVIDER must be BREVO.",
            hint="Set MARKETING_SYNC_PROVIDER=BREVO or leave it at its default.",
            id="brevo_marketing.E001",
        )]
    return []
