from dataclasses import dataclass

from brevo_marketing.client import BrevoContactAttribute, BrevoContactList, BrevoMarketingClient


@dataclass(frozen=True)
class BrevoMarketingInspectionResult:
    attributes: tuple[BrevoContactAttribute, ...]
    lists: tuple[BrevoContactList, ...]


def inspect_brevo_marketing_configuration(*, client=None):
    """Verify read-only contacts API access and return safe account metadata."""
    client = client or BrevoMarketingClient.from_settings()
    return BrevoMarketingInspectionResult(
        attributes=client.get_contact_attributes(),
        lists=client.get_contact_lists(),
    )
