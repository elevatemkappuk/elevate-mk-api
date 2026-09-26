from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction

from audit.models import AuditEvent
from brevo_marketing.client import BrevoContact, BrevoMarketingClient
from brevo_marketing.exceptions import BrevoMarketingIdentityConflictError, BrevoMarketingValidationError, is_invalid_phone_error
from external_references.models import ExternalPersonReference
from external_references.services import attach_person_reference
from marketing_preferences.models import MarketingPreference
from marketing_preferences.services import get_effective_marketing_preference
from people.models import Person
from people.services import normalize_mobile


BREVO_PROVIDER = "BREVO"
MARKETING_CONTACT_REFERENCE_TYPE = ExternalPersonReference.ReferenceType.MARKETING_CONTACT


class BrevoPersonSyncOutcome:
    SKIPPED_NOT_BUSINESS = "SKIPPED_NOT_BUSINESS"
    SKIPPED_ARCHIVED = "SKIPPED_ARCHIVED"
    SKIPPED_MISSING_PRIMARY_EMAIL = "SKIPPED_MISSING_PRIMARY_EMAIL"
    SKIPPED_INVALID_PRIMARY_EMAIL = "SKIPPED_INVALID_PRIMARY_EMAIL"
    SKIPPED_CONSENT_UNKNOWN = "SKIPPED_CONSENT_UNKNOWN"
    SKIPPED_CONSENT_OPTED_OUT_NO_CONTACT = "SKIPPED_CONSENT_OPTED_OUT_NO_CONTACT"
    CREATED_MARKETING_CONTACT = "CREATED_MARKETING_CONTACT"
    UPDATED_MARKETING_CONTACT = "UPDATED_MARKETING_CONTACT"
    ALREADY_SYNCHRONIZED = "ALREADY_SYNCHRONIZED"
    EXISTING_PROVIDER_CONTACT_LINKED = "EXISTING_PROVIDER_CONTACT_LINKED"
    SKIPPED_PROTECTED_PROVIDER_STATE = "SKIPPED_PROTECTED_PROVIDER_STATE"
    MARKETING_OPTED_OUT = "MARKETING_OPTED_OUT"
    ALREADY_MARKETING_OPTED_OUT = "ALREADY_MARKETING_OPTED_OUT"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
    SKIPPED_NO_MARKETING_CONTACT = "SKIPPED_NO_MARKETING_CONTACT"
    UPDATED_PERSON_PROFILE = "UPDATED_PERSON_PROFILE"
    PROFILE_ALREADY_SYNCHRONIZED = "PROFILE_ALREADY_SYNCHRONIZED"
    EMAIL_MIGRATION_UPDATED = "EMAIL_MIGRATION_UPDATED"
    EMAIL_MIGRATION_ALREADY_SYNCHRONIZED = "EMAIL_MIGRATION_ALREADY_SYNCHRONIZED"
    EMAIL_MIGRATION_SUPERSEDED = "EMAIL_MIGRATION_SUPERSEDED"


@dataclass(frozen=True)
class BrevoPersonSyncResult:
    person_id: int
    outcome: str
    contact_id: int | None = None
    reference_id: int | None = None
    provider_state: str | None = None
    reason: str | None = None


def _safe_email(person):
    return (person.primary_email or "").strip().casefold()


def _approved_attributes(person):
    attributes = {
        "FIRSTNAME": person.first_name,
        "LASTNAME": person.last_name,
    }
    sms = _safe_brevo_sms(person.mobile)
    if sms:
        attributes["SMS"] = sms
    return attributes


def _safe_brevo_sms(value):
    """Return only an already-international mobile value; never infer a country."""
    normalized = normalize_mobile(value)
    if not normalized:
        return None
    if normalized.startswith("+"):
        digits = normalized[1:]
        return normalized if digits.isdigit() and 7 <= len(digits) <= 15 else None
    if normalized.startswith("00"):
        digits = normalized[2:]
        return normalized if digits.isdigit() and 7 <= len(digits) <= 15 else None
    return None


def _same_approved_attributes(contact: BrevoContact, person):
    return all(contact.attributes.get(key, "") == value for key, value in _approved_attributes(person).items())


def _attributes_without_sms(attributes):
    return {key: value for key, value in attributes.items() if key != "SMS"}


def _create_contact_with_optional_sms_fallback(*, client, email, attributes, list_id):
    try:
        return client.create_contact(email=email, attributes=attributes, list_id=list_id)
    except BrevoMarketingValidationError as error:
        if "SMS" not in attributes or not attributes["SMS"] or not is_invalid_phone_error(error):
            raise
        return client.create_contact(
            email=email,
            attributes=_attributes_without_sms(attributes),
            list_id=list_id,
        )


def _update_contact_with_optional_sms_fallback(*, client, contact_id, attributes=None, list_id=None, email_blacklisted=None):
    try:
        client.update_contact(
            contact_id=contact_id,
            attributes=attributes,
            list_id=list_id,
            email_blacklisted=email_blacklisted,
        )
    except BrevoMarketingValidationError as error:
        if not attributes or not attributes.get("SMS") or not is_invalid_phone_error(error):
            raise
        client.update_contact(
            contact_id=contact_id,
            attributes=_attributes_without_sms(attributes),
            list_id=list_id,
            email_blacklisted=email_blacklisted,
        )


def _provider_state(contact, list_id):
    if contact.email_blacklisted:
        return "EMAIL_CAMPAIGN_BLOCKLISTED"
    if list_id in contact.list_unsubscribed:
        return "MARKETING_LIST_UNSUBSCRIBED"
    return None


@transaction.atomic
def synchronize_person_to_brevo(*, person_id, client=None, actor_user=None):
    """Synchronize exactly one explicitly eligible BUSINESS Person to Brevo Marketing."""
    person = Person.objects.select_for_update().get(pk=person_id)
    if person.record_type != Person.RecordType.BUSINESS:
        return BrevoPersonSyncResult(person_id=person.id, outcome=BrevoPersonSyncOutcome.SKIPPED_NOT_BUSINESS, reason="NOT_BUSINESS")
    if person.archived_at is not None:
        return BrevoPersonSyncResult(person_id=person.id, outcome=BrevoPersonSyncOutcome.SKIPPED_ARCHIVED, reason="ARCHIVED")
    email = _safe_email(person)
    if not email:
        return BrevoPersonSyncResult(person_id=person.id, outcome=BrevoPersonSyncOutcome.SKIPPED_MISSING_PRIMARY_EMAIL, reason="MISSING_PRIMARY_EMAIL")
    try:
        validate_email(email)
    except ValidationError:
        return BrevoPersonSyncResult(person_id=person.id, outcome=BrevoPersonSyncOutcome.SKIPPED_INVALID_PRIMARY_EMAIL, reason="INVALID_PRIMARY_EMAIL")

    preference = get_effective_marketing_preference(person=person)
    if preference.state == MarketingPreference.State.UNKNOWN:
        return BrevoPersonSyncResult(person_id=person.id, outcome=BrevoPersonSyncOutcome.SKIPPED_CONSENT_UNKNOWN, reason="MARKETING_CONSENT_UNKNOWN")

    client = client or BrevoMarketingClient.from_settings(require_marketing_list=True)
    mobile_reason = "MOBILE_OMITTED_UNSAFE_FORMAT" if person.mobile and not _safe_brevo_sms(person.mobile) else None
    list_id = client.get_marketing_list_id()
    existing_reference = ExternalPersonReference.objects.select_for_update().filter(
        person=person,
        provider=BREVO_PROVIDER,
        reference_type=MARKETING_CONTACT_REFERENCE_TYPE,
    ).first()
    contact = client.get_contact(email)

    if contact is None:
        if existing_reference is not None:
            return BrevoPersonSyncResult(
                person_id=person.id,
                outcome=BrevoPersonSyncOutcome.RECONCILIATION_REQUIRED,
                reference_id=existing_reference.id,
                reason="BREVO_CONTACT_NOT_FOUND_FOR_EXISTING_REFERENCE",
            )
        if preference.state == MarketingPreference.State.OPTED_OUT:
            return BrevoPersonSyncResult(
                person_id=person.id,
                outcome=BrevoPersonSyncOutcome.SKIPPED_CONSENT_OPTED_OUT_NO_CONTACT,
                reason="NO_BREVO_CONTACT",
            )
        contact = _create_contact_with_optional_sms_fallback(
            client=client,
            email=email,
            attributes=_approved_attributes(person),
            list_id=list_id,
        )
        operation = BrevoPersonSyncOutcome.CREATED_MARKETING_CONTACT
    else:
        if contact.email and contact.email != email:
            raise BrevoMarketingIdentityConflictError("Brevo returned a contact with a different email identity.")
        if existing_reference is not None and str(existing_reference.external_id) != str(contact.contact_id):
            raise BrevoMarketingIdentityConflictError("The Person already has a different Brevo marketing contact reference.")
        if ExternalPersonReference.objects.filter(
            provider=BREVO_PROVIDER,
            reference_type=MARKETING_CONTACT_REFERENCE_TYPE,
            external_id=str(contact.contact_id),
        ).exclude(person=person).exists():
            raise BrevoMarketingIdentityConflictError("The Brevo marketing contact is already linked to a different Person.")

        protected_state = _provider_state(contact, list_id)
        if protected_state is not None and preference.state == MarketingPreference.State.OPTED_IN:
            operation = BrevoPersonSyncOutcome.SKIPPED_PROTECTED_PROVIDER_STATE
        elif preference.state == MarketingPreference.State.OPTED_OUT:
            if contact.email_blacklisted:
                operation = BrevoPersonSyncOutcome.ALREADY_MARKETING_OPTED_OUT
            elif list_id in contact.list_unsubscribed:
                operation = BrevoPersonSyncOutcome.SKIPPED_PROTECTED_PROVIDER_STATE
            else:
                client.update_contact(contact_id=contact.contact_id, email_blacklisted=True)
                contact = BrevoContact(
                    contact_id=contact.contact_id,
                    email=contact.email,
                    attributes=contact.attributes,
                    list_ids=contact.list_ids,
                    list_unsubscribed=contact.list_unsubscribed,
                    email_blacklisted=True,
                    sms_blacklisted=contact.sms_blacklisted,
                )
                operation = BrevoPersonSyncOutcome.MARKETING_OPTED_OUT
        else:
            needs_attributes = not _same_approved_attributes(contact, person)
            needs_list = list_id not in contact.list_ids
            if needs_attributes or needs_list:
                _update_contact_with_optional_sms_fallback(
                    client=client,
                    contact_id=contact.contact_id,
                    attributes=_approved_attributes(person) if needs_attributes else None,
                    list_id=list_id if needs_list else None,
                )
                operation = BrevoPersonSyncOutcome.UPDATED_MARKETING_CONTACT
            else:
                operation = BrevoPersonSyncOutcome.ALREADY_SYNCHRONIZED

    reference = attach_person_reference(
        person=person,
        provider=BREVO_PROVIDER,
        reference_type=MARKETING_CONTACT_REFERENCE_TYPE,
        external_id=str(contact.contact_id),
        actor_user=actor_user,
    )
    if operation == BrevoPersonSyncOutcome.ALREADY_SYNCHRONIZED and existing_reference is None:
        operation = BrevoPersonSyncOutcome.EXISTING_PROVIDER_CONTACT_LINKED
    return BrevoPersonSyncResult(
        person_id=person.id,
        outcome=operation,
        contact_id=contact.contact_id,
        reference_id=reference.id,
        provider_state=_provider_state(contact, list_id),
        reason=protected_state if operation == BrevoPersonSyncOutcome.SKIPPED_PROTECTED_PROVIDER_STATE else mobile_reason,
    )


def _known_person_emails(person_id, previous_email, requested_email):
    emails = {
        value.strip().casefold()
        for value in (previous_email, requested_email)
        if value and value.strip()
    }
    for event in AuditEvent.objects.filter(entity_type="Person", entity_id=str(person_id)).only("changes"):
        change = (event.changes or {}).get("primary_email") or {}
        for value in (change.get("from"), change.get("to")):
            if value and str(value).strip():
                emails.add(str(value).strip().casefold())
    return emails


@transaction.atomic
def synchronize_person_email_to_brevo(*, job, client=None):
    """Safely migrate an existing Brevo contact's email by stable contact ID."""
    person = Person.objects.select_for_update().get(pk=job.person_id)
    requested_email = (job.requested_email or "").strip().casefold()
    previous_email = (job.previous_email or "").strip().casefold()

    if requested_email != _safe_email(person):
        return BrevoPersonSyncResult(
            person_id=person.id,
            outcome=BrevoPersonSyncOutcome.EMAIL_MIGRATION_SUPERSEDED,
            reason="CRM_EMAIL_CHANGED_AGAIN",
        )
    if person.record_type != Person.RecordType.BUSINESS:
        return BrevoPersonSyncResult(person_id=person.id, outcome=BrevoPersonSyncOutcome.RECONCILIATION_REQUIRED, reason="NOT_BUSINESS")
    if person.archived_at is not None:
        return BrevoPersonSyncResult(person_id=person.id, outcome=BrevoPersonSyncOutcome.RECONCILIATION_REQUIRED, reason="ARCHIVED")
    try:
        validate_email(requested_email)
    except ValidationError:
        return BrevoPersonSyncResult(person_id=person.id, outcome=BrevoPersonSyncOutcome.RECONCILIATION_REQUIRED, reason="CRM_EMAIL_INVALID")

    preference = get_effective_marketing_preference(person=person)
    if preference.state != MarketingPreference.State.OPTED_IN:
        return BrevoPersonSyncResult(
            person_id=person.id,
            outcome=BrevoPersonSyncOutcome.RECONCILIATION_REQUIRED,
            reason=f"MARKETING_CONSENT_{preference.state}",
        )

    reference = ExternalPersonReference.objects.select_for_update().filter(
        person=person,
        provider=BREVO_PROVIDER,
        reference_type=MARKETING_CONTACT_REFERENCE_TYPE,
        status=ExternalPersonReference.Status.ACTIVE,
    ).first()
    if reference is None:
        return BrevoPersonSyncResult(person_id=person.id, outcome=BrevoPersonSyncOutcome.RECONCILIATION_REQUIRED, reason="NO_ACTIVE_BREVO_REFERENCE")

    client = client or BrevoMarketingClient.from_settings()
    contact = client.get_contact_by_id(reference.external_id)
    if contact is None:
        return BrevoPersonSyncResult(
            person_id=person.id,
            outcome=BrevoPersonSyncOutcome.RECONCILIATION_REQUIRED,
            reference_id=reference.id,
            reason="BREVO_CONTACT_NOT_FOUND_FOR_REFERENCE",
        )
    if contact.email not in _known_person_emails(person.id, previous_email, requested_email):
        return BrevoPersonSyncResult(
            person_id=person.id,
            outcome=BrevoPersonSyncOutcome.RECONCILIATION_REQUIRED,
            contact_id=contact.contact_id,
            reference_id=reference.id,
            reason="BREVO_CONTACT_EMAIL_IDENTITY_UNKNOWN",
        )
    if contact.email_blacklisted or contact.list_unsubscribed:
        return BrevoPersonSyncResult(
            person_id=person.id,
            outcome=BrevoPersonSyncOutcome.RECONCILIATION_REQUIRED,
            contact_id=contact.contact_id,
            reference_id=reference.id,
            reason="BREVO_CONTACT_RESTRICTED",
        )

    target = client.get_contact(requested_email)
    if target is not None and target.contact_id != contact.contact_id:
        return BrevoPersonSyncResult(
            person_id=person.id,
            outcome=BrevoPersonSyncOutcome.RECONCILIATION_REQUIRED,
            contact_id=contact.contact_id,
            reference_id=reference.id,
            reason="BREVO_TARGET_EMAIL_ALREADY_OWNED",
        )
    if contact.email == requested_email:
        return BrevoPersonSyncResult(
            person_id=person.id,
            outcome=BrevoPersonSyncOutcome.EMAIL_MIGRATION_ALREADY_SYNCHRONIZED,
            contact_id=contact.contact_id,
            reference_id=reference.id,
        )

    try:
        client.update_contact(contact_id=contact.contact_id, attributes={"EMAIL": requested_email})
    except BrevoMarketingValidationError as error:
        raise BrevoMarketingIdentityConflictError("Brevo rejected the email identity migration.") from error

    verified = client.get_contact_by_id(contact.contact_id)
    if verified is None or verified.email != requested_email:
        return BrevoPersonSyncResult(
            person_id=person.id,
            outcome=BrevoPersonSyncOutcome.RECONCILIATION_REQUIRED,
            contact_id=contact.contact_id,
            reference_id=reference.id,
            reason="BREVO_EMAIL_MIGRATION_NOT_VERIFIED",
        )
    return BrevoPersonSyncResult(
        person_id=person.id,
        outcome=BrevoPersonSyncOutcome.EMAIL_MIGRATION_UPDATED,
        contact_id=verified.contact_id,
        reference_id=reference.id,
    )


@transaction.atomic
def synchronize_person_profile_to_brevo(*, person_id, client=None):
    """Update only the approved CRM profile fields on an existing Brevo contact."""
    person = Person.objects.select_for_update().get(pk=person_id)
    if person.record_type != Person.RecordType.BUSINESS:
        return BrevoPersonSyncResult(person_id=person.id, outcome=BrevoPersonSyncOutcome.SKIPPED_NOT_BUSINESS)
    if person.archived_at is not None:
        return BrevoPersonSyncResult(person_id=person.id, outcome=BrevoPersonSyncOutcome.SKIPPED_ARCHIVED)

    reference = ExternalPersonReference.objects.select_for_update().filter(
        person=person,
        provider=BREVO_PROVIDER,
        reference_type=MARKETING_CONTACT_REFERENCE_TYPE,
        status=ExternalPersonReference.Status.ACTIVE,
    ).first()
    if reference is None:
        return BrevoPersonSyncResult(
            person_id=person.id,
            outcome=BrevoPersonSyncOutcome.SKIPPED_NO_MARKETING_CONTACT,
            reason="NO_ACTIVE_BREVO_REFERENCE",
        )

    client = client or BrevoMarketingClient.from_settings()
    contact = client.get_contact_by_id(reference.external_id)
    if contact is None:
        return BrevoPersonSyncResult(
            person_id=person.id,
            outcome=BrevoPersonSyncOutcome.RECONCILIATION_REQUIRED,
            reference_id=reference.id,
            reason="BREVO_CONTACT_NOT_FOUND_FOR_REFERENCE",
        )

    email = _safe_email(person)
    try:
        validate_email(email)
    except ValidationError:
        return BrevoPersonSyncResult(
            person_id=person.id,
            outcome=BrevoPersonSyncOutcome.RECONCILIATION_REQUIRED,
            contact_id=contact.contact_id,
            reference_id=reference.id,
            reason="CRM_EMAIL_INVALID_FOR_REFERENCED_CONTACT",
        )
    if not contact.email or contact.email != email:
        return BrevoPersonSyncResult(
            person_id=person.id,
            outcome=BrevoPersonSyncOutcome.RECONCILIATION_REQUIRED,
            contact_id=contact.contact_id,
            reference_id=reference.id,
            reason="CRM_EMAIL_DIFFERS_FROM_REFERENCED_CONTACT",
        )

    attributes = {
        "FIRSTNAME": person.first_name or "",
        "LASTNAME": person.last_name or "",
    }
    mobile_reason = None
    if not person.mobile:
        attributes["SMS"] = ""
    else:
        safe_sms = _safe_brevo_sms(person.mobile)
        if safe_sms:
            attributes["SMS"] = safe_sms
        else:
            mobile_reason = "MOBILE_OMITTED_UNSAFE_FORMAT"

    changed_attributes = {
        key: value
        for key, value in attributes.items()
        if (contact.attributes.get(key) or "") != value
    }
    if changed_attributes:
        _update_contact_with_optional_sms_fallback(
            client=client,
            contact_id=contact.contact_id,
            attributes=changed_attributes,
        )
        outcome = BrevoPersonSyncOutcome.UPDATED_PERSON_PROFILE
    else:
        outcome = BrevoPersonSyncOutcome.PROFILE_ALREADY_SYNCHRONIZED
    return BrevoPersonSyncResult(
        person_id=person.id,
        outcome=outcome,
        contact_id=contact.contact_id,
        reference_id=reference.id,
        provider_state=_provider_state(contact, None),
        reason=mobile_reason,
    )
