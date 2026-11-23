from dataclasses import dataclass

from brevo_marketing.client import BrevoMarketingClient
from brevo_marketing.exceptions import BrevoMarketingError
from brevo_marketing.sync import BREVO_PROVIDER, MARKETING_CONTACT_REFERENCE_TYPE, _provider_state
from external_references.models import ExternalPersonReference
from marketing_preferences.models import MarketingPreference
from marketing_preferences.services import get_effective_marketing_preference
from people.services import normalize_email


BREVO_CONTACT_PROFILE_BASE_URL = "https://app.brevo.com/contact/index/"


def build_brevo_contact_profile_url(contact_id):
    """Build Brevo's best-effort web-app contact link from a numeric ID.

    This is a Brevo UI route, not a formally guaranteed public API URL. Keep the
    route centralized so a future Brevo UI change has one maintenance point.
    """
    if isinstance(contact_id, bool):
        return None
    value = str(contact_id).strip()
    if not value.isdigit() or int(value) <= 0:
        return None
    return f"{BREVO_CONTACT_PROFILE_BASE_URL}{int(value)}"


@dataclass(frozen=True)
class BrevoIntegrationInspection:
    status: str
    reason_code: str | None
    title: str
    explanation: str
    can_reconcile: bool
    provider_profile_url: str | None = None


@dataclass(frozen=True)
class PersonBrevoInspection:
    marketing_preference: object
    integration: BrevoIntegrationInspection


def inspect_person_brevo_integration(*, person, can_reconcile=False, client=None):
    """Return a safe, read-only projection of one Person's Brevo state."""
    preference = get_effective_marketing_preference(person=person, channel=MarketingPreference.Channel.EMAIL)
    reference = ExternalPersonReference.objects.filter(
        person=person,
        provider=BREVO_PROVIDER,
        reference_type=MARKETING_CONTACT_REFERENCE_TYPE,
        status=ExternalPersonReference.Status.ACTIVE,
    ).first()
    if reference is None:
        return PersonBrevoInspection(
            marketing_preference=preference,
            integration=BrevoIntegrationInspection(
                "NOT_CONNECTED", "BREVO_NO_ACTIVE_REFERENCE", "Not connected to Brevo",
                "This Person does not have an active Brevo marketing connection.", False,
            ),
        )

    try:
        client = client or BrevoMarketingClient.from_settings()
        contact = client.get_contact_by_id(reference.external_id)
        if contact is None:
            return PersonBrevoInspection(
                preference,
                BrevoIntegrationInspection(
                    "CONTACT_MISSING", "BREVO_CONTACT_NOT_FOUND_FOR_EXISTING_REFERENCE",
                    "Brevo contact no longer exists",
                    "Elevate's saved Brevo connection points to a contact that can no longer be found.",
                    bool(can_reconcile),
                ),
            )

        provider_profile_url = build_brevo_contact_profile_url(contact.contact_id)
        current_email = normalize_email(person.primary_email)
        if not current_email:
            return _identity_conflict(
                preference,
                reason_code="BREVO_CRM_EMAIL_MISSING",
                title="CRM email required",
                explanation="A current CRM email is required to verify this Brevo connection.",
                provider_profile_url=provider_profile_url,
            )
        if normalize_email(contact.email) != current_email:
            return _identity_conflict(
                preference,
                reason_code="BREVO_EMAIL_IDENTITY_MISMATCH",
                title="CRM and Brevo email identities differ",
                explanation="The Brevo contact linked to this person uses a different email identity from the person's current CRM email.",
                provider_profile_url=provider_profile_url,
            )
        if ExternalPersonReference.objects.filter(
            provider=BREVO_PROVIDER,
            reference_type=MARKETING_CONTACT_REFERENCE_TYPE,
            status=ExternalPersonReference.Status.ACTIVE,
            external_id=str(contact.contact_id),
        ).exclude(person=person).exists():
            return _identity_conflict(
                preference,
                reason_code="BREVO_CONTACT_LINKED_TO_OTHER_PERSON",
                title="Brevo contact is linked elsewhere",
                explanation="This Brevo contact is already associated with another CRM Person.",
                provider_profile_url=provider_profile_url,
            )

        provider_state = _provider_state(contact, client.get_marketing_list_id())
        if provider_state is not None:
            return PersonBrevoInspection(
                preference,
                BrevoIntegrationInspection(
                    "RESTRICTED", "BREVO_CONTACT_RESTRICTED", "Marketing restricted in Brevo",
                    "Brevo currently prevents marketing email for this contact. Elevate will not automatically unblock or resubscribe them.",
                    False, provider_profile_url,
                ),
            )
        return PersonBrevoInspection(
            preference,
            BrevoIntegrationInspection(
                "CONNECTED", None, "Connected to Brevo",
                "This Person has a verified active Brevo marketing connection.", False, provider_profile_url,
            ),
        )
    except BrevoMarketingError:
        return PersonBrevoInspection(
            preference,
            BrevoIntegrationInspection(
                "UNKNOWN", "BREVO_INSPECTION_UNAVAILABLE", "Brevo status unavailable",
                "Brevo status could not be verified right now. No CRM or Brevo changes were made.", False,
            ),
        )


def _identity_conflict(
    preference,
    *,
    reason_code="BREVO_CONTACT_IDENTITY_CONFLICT",
    title="Brevo contact identity needs review",
    explanation="The CRM and Brevo identities could not be matched safely.",
    provider_profile_url=None,
):
    return PersonBrevoInspection(
        preference,
        BrevoIntegrationInspection(
            "IDENTITY_CONFLICT", reason_code, title, explanation, False, provider_profile_url,
        ),
    )
