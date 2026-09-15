from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db import transaction
from django.core.validators import validate_email

from external_references.models import ExternalPersonReference
from external_references.services import attach_person_reference
from marketing_preferences.models import MarketingPreference
from marketing_preferences.services import get_effective_marketing_preference
from mailchimp.client import MailchimpMarketingClient, MailchimpMember
from mailchimp.exceptions import MailchimpPersonSyncConflictError
from people.models import Person


MAILCHIMP_PROVIDER = "MAILCHIMP"
MARKETING_CONTACT_REFERENCE_TYPE = ExternalPersonReference.ReferenceType.MARKETING_CONTACT


class MailchimpPersonSyncOutcome:
    SKIPPED_NOT_BUSINESS = "SKIPPED_NOT_BUSINESS"
    SKIPPED_ARCHIVED = "SKIPPED_ARCHIVED"
    SKIPPED_MISSING_PRIMARY_EMAIL = "SKIPPED_MISSING_PRIMARY_EMAIL"
    SKIPPED_INVALID_PRIMARY_EMAIL = "SKIPPED_INVALID_PRIMARY_EMAIL"
    SKIPPED_CONSENT_UNKNOWN = "SKIPPED_CONSENT_UNKNOWN"
    SKIPPED_CONSENT_OPTED_OUT = "SKIPPED_CONSENT_OPTED_OUT"
    CREATED_SUBSCRIBED = "CREATED_SUBSCRIBED"
    UPDATED = "UPDATED"
    ALREADY_SYNCHRONIZED = "ALREADY_SYNCHRONIZED"
    EXISTING_PROVIDER_CONTACT_LINKED = "EXISTING_PROVIDER_CONTACT_LINKED"
    SKIPPED_PROTECTED_SUBSCRIPTION_STATE = "SKIPPED_PROTECTED_SUBSCRIPTION_STATE"


@dataclass(frozen=True)
class MailchimpPersonSyncResult:
    person_id: int
    outcome: str
    member_id: str | None = None
    reference_id: int | None = None
    provider_status: str | None = None
    reason: str | None = None


def _safe_email(person):
    return (person.primary_email or "").strip().casefold()


def _same_owned_fields(member: MailchimpMember, person):
    fields = member.merge_fields
    return (
        member.email_address.strip().casefold() == _safe_email(person)
        and (fields.get("FNAME") or "") == person.first_name
        and (fields.get("LNAME") or "") == person.last_name
    )


@transaction.atomic
def synchronize_person_to_mailchimp(*, person_id, client=None, actor_user=None):
    """Synchronize one explicitly opted-in BUSINESS Person to Mailchimp."""
    person = Person.objects.select_for_update().get(pk=person_id)
    if person.record_type != Person.RecordType.BUSINESS:
        return MailchimpPersonSyncResult(person_id=person.id, outcome=MailchimpPersonSyncOutcome.SKIPPED_NOT_BUSINESS, reason="NOT_BUSINESS")
    if person.archived_at is not None:
        return MailchimpPersonSyncResult(person_id=person.id, outcome=MailchimpPersonSyncOutcome.SKIPPED_ARCHIVED, reason="ARCHIVED")
    email_address = _safe_email(person)
    if not email_address:
        return MailchimpPersonSyncResult(person_id=person.id, outcome=MailchimpPersonSyncOutcome.SKIPPED_MISSING_PRIMARY_EMAIL, reason="MISSING_PRIMARY_EMAIL")
    try:
        validate_email(email_address)
    except ValidationError:
        return MailchimpPersonSyncResult(person_id=person.id, outcome=MailchimpPersonSyncOutcome.SKIPPED_INVALID_PRIMARY_EMAIL, reason="INVALID_PRIMARY_EMAIL")

    preference = get_effective_marketing_preference(person=person)
    if preference.state == MarketingPreference.State.UNKNOWN:
        return MailchimpPersonSyncResult(person_id=person.id, outcome=MailchimpPersonSyncOutcome.SKIPPED_CONSENT_UNKNOWN, reason="MARKETING_CONSENT_UNKNOWN")
    if preference.state == MarketingPreference.State.OPTED_OUT:
        return MailchimpPersonSyncResult(person_id=person.id, outcome=MailchimpPersonSyncOutcome.SKIPPED_CONSENT_OPTED_OUT, reason="MARKETING_CONSENT_OPTED_OUT")

    client = client or MailchimpMarketingClient.from_settings()
    existing_reference = ExternalPersonReference.objects.select_for_update().filter(
        person=person,
        provider=MAILCHIMP_PROVIDER,
        reference_type=MARKETING_CONTACT_REFERENCE_TYPE,
    ).first()
    member = client.get_member(email_address)

    if existing_reference is not None and (member is None or existing_reference.external_id != member.member_id):
        raise MailchimpPersonSyncConflictError(
            "The Person already has a different Mailchimp marketing contact reference."
        )
    if member is not None and ExternalPersonReference.objects.filter(
        provider=MAILCHIMP_PROVIDER,
        reference_type=MARKETING_CONTACT_REFERENCE_TYPE,
        external_id=member.member_id,
    ).exclude(person=person).exists():
        raise MailchimpPersonSyncConflictError(
            "The Mailchimp marketing contact is already linked to a different Person."
        )

    operation = ""
    if member is None:
        member = client.create_member(
            email_address=email_address,
            first_name=person.first_name,
            last_name=person.last_name,
            status="subscribed",
        )
        operation = MailchimpPersonSyncOutcome.CREATED_SUBSCRIBED
    elif member.status == "subscribed":
        if _same_owned_fields(member, person):
            operation = MailchimpPersonSyncOutcome.ALREADY_SYNCHRONIZED
        else:
            member = client.update_subscribed_member(
                email_address=email_address,
                first_name=person.first_name,
                last_name=person.last_name,
            )
            operation = MailchimpPersonSyncOutcome.UPDATED
    else:
        operation = MailchimpPersonSyncOutcome.SKIPPED_PROTECTED_SUBSCRIPTION_STATE

    reference = attach_person_reference(
        person=person,
        provider=MAILCHIMP_PROVIDER,
        reference_type=MARKETING_CONTACT_REFERENCE_TYPE,
        external_id=member.member_id,
        actor_user=actor_user,
    )
    if operation == MailchimpPersonSyncOutcome.ALREADY_SYNCHRONIZED and existing_reference is None:
        operation = MailchimpPersonSyncOutcome.EXISTING_PROVIDER_CONTACT_LINKED

    return MailchimpPersonSyncResult(
        person_id=person.id,
        outcome=operation,
        member_id=member.member_id,
        reference_id=reference.id,
        provider_status=member.status,
    )
