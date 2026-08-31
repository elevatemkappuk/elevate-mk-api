from notifications.providers.brevo import BrevoTransactionalEmailProvider


def send_transactional_email(
    *,
    recipient_email,
    recipient_name=None,
    template_id=None,
    template_params=None,
    subject=None,
    html_content=None,
    provider=None,
):
    """Send transactional email without exposing provider-specific details to callers."""
    provider = provider or BrevoTransactionalEmailProvider.from_settings()
    return provider.send(
        recipient_email=recipient_email,
        recipient_name=recipient_name,
        template_id=template_id,
        template_params=template_params,
        subject=subject,
        html_content=html_content,
    )
