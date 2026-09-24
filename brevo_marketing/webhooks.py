import json
import logging
import hashlib
from base64 import b64decode
from dataclasses import dataclass
from datetime import datetime, timezone as datetime_timezone
import hmac

from django.conf import settings
from django.core.validators import validate_email
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from brevo_marketing.routing import BREVO_PROVIDER
from external_references.models import ExternalPersonReference
from marketing_preferences.models import MarketingPreference, MarketingWebhookReceipt
from marketing_preferences.services import record_opt_out
from people.models import Person
from people.services import normalize_email


logger = logging.getLogger(__name__)
SUPPORTED_UNSUBSCRIBE_EVENT = "unsubscribe"


class BrevoWebhookConfigurationError(Exception):
    pass


class BrevoWebhookAuthenticationError(Exception):
    pass


class BrevoWebhookPayloadError(Exception):
    pass


@dataclass(frozen=True)
class BrevoUnsubscribeEvent:
    event_fingerprint: str | None
    email: str
    event_recorded_at: datetime | None
    list_ids: tuple[int, ...]
    campaign_id: str | None


@dataclass(frozen=True)
class BrevoWebhookResult:
    outcome: str
    person_id: int | None = None
    history_id: int | None = None
    receipt_id: int | None = None


def authenticate_webhook_request(request):
    username = str(getattr(settings, "BREVO_MARKETING_WEBHOOK_USERNAME", "") or "")
    password = str(getattr(settings, "BREVO_MARKETING_WEBHOOK_PASSWORD", "") or "")
    if not username or not password:
        raise BrevoWebhookConfigurationError()

    header = request.headers.get("Authorization", "")
    if not header.startswith("Basic "):
        raise BrevoWebhookAuthenticationError()
    try:
        decoded = b64decode(header[6:], validate=True).decode("utf-8")
        supplied_username, supplied_password = decoded.split(":", 1)
    except (ValueError, UnicodeDecodeError):
        raise BrevoWebhookAuthenticationError()
    if not hmac.compare_digest(supplied_username, username) or not hmac.compare_digest(supplied_password, password):
        raise BrevoWebhookAuthenticationError()


def parse_webhook_payload(payload):
    if not isinstance(payload, dict):
        raise BrevoWebhookPayloadError("Webhook payload must be a JSON object.")
    raw_event = payload.get("event")
    if not isinstance(raw_event, str):
        raise BrevoWebhookPayloadError("Webhook event is invalid.")
    event = raw_event.strip().casefold()
    if not event:
        raise BrevoWebhookPayloadError("Webhook event is required.")
    if event != SUPPORTED_UNSUBSCRIBE_EVENT:
        return None

    raw_email = payload.get("email")
    if not isinstance(raw_email, str):
        raise BrevoWebhookPayloadError("A valid webhook recipient is required.")
    email = normalize_email(raw_email)
    try:
        validate_email(email)
    except ValidationError as error:
        raise BrevoWebhookPayloadError("A valid webhook recipient is required.") from error

    has_list_context = "list_id" in payload
    list_ids = _parse_list_ids(payload.get("list_id")) if has_list_context else ()
    if has_list_context and _configured_list_id() not in list_ids:
        raise BrevoWebhookPayloadError("The unsubscribe event is outside the configured marketing list.")

    event_recorded_at = _parse_event_timestamp(payload.get("ts_event"), payload.get("date_event"))
    if event_recorded_at is None:
        event_recorded_at = _parse_event_timestamp(payload.get("ts"), None)
    if not has_list_context and event_recorded_at is None:
        raise BrevoWebhookPayloadError("A reliable unsubscribe event timestamp is required.")

    return BrevoUnsubscribeEvent(
        event_fingerprint=_event_fingerprint(
            event_type=event,
            email=email,
            list_ids=list_ids,
            campaign_id=_safe_identifier(payload.get("camp_id")),
            event_recorded_at=event_recorded_at,
            webhook_id=_safe_identifier(payload.get("id")),
        ),
        email=email,
        event_recorded_at=event_recorded_at,
        list_ids=tuple(list_ids),
        campaign_id=_safe_identifier(payload.get("camp_id")),
    )


def handle_unsubscribe_event(event: BrevoUnsubscribeEvent):
    with transaction.atomic():
        if event.event_fingerprint:
            existing = MarketingWebhookReceipt.objects.select_for_update().filter(
                provider=BREVO_PROVIDER,
                event_fingerprint=event.event_fingerprint,
            ).first()
            if existing is not None:
                logger.info("Brevo marketing webhook replay acknowledged. fingerprint=%s outcome=%s", event.event_fingerprint, existing.outcome)
                return BrevoWebhookResult(outcome="REPLAY_IGNORED", receipt_id=existing.id)

        people = list(Person.objects.business().filter(primary_email__iexact=event.email).order_by("id")[:2])
        if not people:
            result = BrevoWebhookResult(outcome="PERSON_NOT_FOUND")
            logger.info("Brevo marketing unsubscribe acknowledged without CRM Person. fingerprint=%s outcome=%s", event.event_fingerprint, result.outcome)
            return _store_receipt(event, result)
        if len(people) > 1:
            result = BrevoWebhookResult(outcome="IDENTITY_CONFLICT")
            logger.warning("Brevo marketing unsubscribe rejected for ambiguous CRM identity. fingerprint=%s outcome=%s", event.event_fingerprint, result.outcome)
            return _store_receipt(event, result)

        person = people[0]
        result = record_opt_out(
            person=person,
            source=MarketingPreference.Source.BREVO,
            recorded_at=event.event_recorded_at or timezone.now(),
            origin_provider=BREVO_PROVIDER,
            provider_event_id=event.event_fingerprint,
            provider_event_type=SUPPORTED_UNSUBSCRIBE_EVENT,
        )
        preference = result.preference
        outcome = "CRM_OPTED_OUT_RECORDED" if result.changed else "CRM_OPTED_OUT_ALREADY_RECORDED"
        webhook_result = BrevoWebhookResult(outcome=outcome, person_id=person.id, history_id=_latest_history_id(preference.person_id))
        logger.info("Brevo marketing unsubscribe applied. person_id=%s fingerprint=%s outcome=%s", person.id, event.event_fingerprint, outcome)
        return _store_receipt(event, webhook_result, person=person)


def _store_receipt(event, result, person=None):
    if not event.event_fingerprint:
        return result
    try:
        with transaction.atomic():
            receipt, created = MarketingWebhookReceipt.objects.get_or_create(
                provider=BREVO_PROVIDER,
                event_fingerprint=event.event_fingerprint,
                defaults={
                    "event_type": SUPPORTED_UNSUBSCRIBE_EVENT,
                    "person": person,
                    "event_recorded_at": event.event_recorded_at,
                    "list_ids": list(event.list_ids),
                    "campaign_id": event.campaign_id,
                    "outcome": result.outcome,
                },
            )
    except IntegrityError:
        receipt = MarketingWebhookReceipt.objects.get(provider=BREVO_PROVIDER, event_fingerprint=event.event_fingerprint)
        created = False
    if not created:
        return BrevoWebhookResult(outcome="REPLAY_IGNORED", receipt_id=receipt.id)
    return BrevoWebhookResult(
        outcome=result.outcome,
        person_id=result.person_id,
        history_id=result.history_id,
        receipt_id=receipt.id,
    )


def _latest_history_id(person_id):
    from marketing_preferences.models import MarketingPreferenceHistory

    return MarketingPreferenceHistory.objects.filter(
        preference__person_id=person_id,
        channel=MarketingPreference.Channel.EMAIL,
    ).order_by("-id").values_list("id", flat=True).first()


def _configured_list_id():
    try:
        value = int(str(getattr(settings, "BREVO_MARKETING_LIST_ID", "")).strip())
    except (TypeError, ValueError) as error:
        raise BrevoWebhookConfigurationError() from error
    if value <= 0:
        raise BrevoWebhookConfigurationError()
    return value


def _parse_list_ids(value):
    if not isinstance(value, list) or not value:
        raise BrevoWebhookPayloadError("Webhook unsubscribe list context is required.")
    ids = []
    for item in value:
        if isinstance(item, bool):
            raise BrevoWebhookPayloadError("Webhook list context is invalid.")
        try:
            item = int(item)
        except (TypeError, ValueError) as error:
            raise BrevoWebhookPayloadError("Webhook list context is invalid.") from error
        if item <= 0:
            raise BrevoWebhookPayloadError("Webhook list context is invalid.")
        ids.append(item)
    return tuple(dict.fromkeys(ids))


def _safe_identifier(value):
    if value in (None, "") or isinstance(value, (dict, list, bool)):
        return None
    value = str(value).strip()
    return value[:255] or None


def _event_fingerprint(*, event_type, email, list_ids, campaign_id, event_recorded_at, webhook_id):
    """Return a replay key only when Brevo supplied an event occurrence time.

    Brevo's ``id`` identifies the configured webhook, not this delivery. The
    email is represented only by a digest, and the timestamp prevents a later
    unsubscribe after re-consent from colliding with an earlier one.
    """
    if event_recorded_at is None:
        return None
    canonical = "|".join(
        (
            event_type,
            hashlib.sha256(email.encode("utf-8")).hexdigest(),
            ",".join(str(value) for value in sorted(list_ids)),
            campaign_id or "",
            event_recorded_at.astimezone(datetime_timezone.utc).isoformat(),
            webhook_id or "",
        )
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _parse_event_timestamp(timestamp, date_value):
    try:
        if timestamp not in (None, ""):
            return datetime.fromtimestamp(int(timestamp), tz=datetime_timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        pass
    if isinstance(date_value, str):
        try:
            parsed = datetime.strptime(date_value.strip(), "%Y-%m-%d %H:%M:%S")
            return parsed.replace(tzinfo=datetime_timezone.utc)
        except ValueError:
            return None
    return None
