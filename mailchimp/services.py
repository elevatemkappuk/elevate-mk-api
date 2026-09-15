from dataclasses import dataclass

from mailchimp.client import MailchimpAudience, MailchimpMarketingClient


@dataclass(frozen=True)
class MailchimpVerificationResult:
    """Safe verification output; credentials and provider response bodies are excluded."""

    audience_id: str
    audience_name: str
    member_count: int | None
    unsubscribe_count: int | None
    cleaned_count: int | None

    @classmethod
    def from_audience(cls, audience: MailchimpAudience):
        return cls(
            audience_id=audience.audience_id,
            audience_name=audience.audience_name,
            member_count=audience.member_count,
            unsubscribe_count=audience.unsubscribe_count,
            cleaned_count=audience.cleaned_count,
        )


def verify_mailchimp_connection(*, client=None):
    """Verify access to the configured audience without mutating Mailchimp."""
    client = client or MailchimpMarketingClient.from_settings()
    return MailchimpVerificationResult.from_audience(client.get_configured_audience())
