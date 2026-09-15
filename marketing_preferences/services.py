from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from audit.models import AuditEvent
from audit.services import record_audit_event
from external_references.services import enqueue_person_sync_job
from marketing_preferences.models import MarketingPreference, MarketingPreferenceHistory


MAILCHIMP_PROVIDER = "MAILCHIMP"
EMAIL_MARKETING_PREFERENCE_SYNC = "EMAIL_MARKETING_PREFERENCE"


@dataclass(frozen=True)
class EffectiveMarketingPreference:
    person_id: int
    channel: str
    state: str
    source: str | None
    recorded_at: object | None
    actor_user_id: int | None


@dataclass(frozen=True)
class MarketingPreferenceWriteResult:
    preference: EffectiveMarketingPreference
    changed: bool


def get_effective_marketing_preference(*, person, channel=MarketingPreference.Channel.EMAIL):
    preference = MarketingPreference.objects.select_related("actor_user").filter(
        person=person,
        channel=channel,
    ).first()
    if preference is None:
        return EffectiveMarketingPreference(
            person_id=person.id,
            channel=channel,
            state=MarketingPreference.State.UNKNOWN,
            source=None,
            recorded_at=None,
            actor_user_id=None,
        )
    return _effective_from_model(preference)


def record_opt_in(*, person, source=MarketingPreference.Source.STAFF_RECORDED, actor_user=None, recorded_at=None):
    return _record_explicit_preference(
        person=person,
        state=MarketingPreference.State.OPTED_IN,
        source=source,
        actor_user=actor_user,
        recorded_at=recorded_at,
    )


def record_opt_out(*, person, source=MarketingPreference.Source.STAFF_RECORDED, actor_user=None, recorded_at=None):
    return _record_explicit_preference(
        person=person,
        state=MarketingPreference.State.OPTED_OUT,
        source=source,
        actor_user=actor_user,
        recorded_at=recorded_at,
    )


@transaction.atomic
def _record_explicit_preference(*, person, state, source, actor_user=None, recorded_at=None):
    if state not in {MarketingPreference.State.OPTED_IN, MarketingPreference.State.OPTED_OUT}:
        raise ValueError("Only OPTED_IN and OPTED_OUT may be recorded explicitly.")
    if source not in MarketingPreference.Source.values:
        raise ValueError(f"Unsupported marketing preference source: {source}")

    recorded_at = recorded_at or timezone.now()
    preference = MarketingPreference.objects.select_for_update().filter(
        person=person,
        channel=MarketingPreference.Channel.EMAIL,
    ).first()
    previous_state = preference.state if preference is not None else MarketingPreference.State.UNKNOWN
    previous_source = preference.source if preference is not None else None
    previous_actor_id = preference.actor_user_id if preference is not None else None
    if (
        preference is not None
        and previous_state == state
        and previous_source == source
        and previous_actor_id == getattr(actor_user, "id", None)
    ):
        return MarketingPreferenceWriteResult(_effective_from_model(preference), changed=False)

    if preference is None:
        preference = MarketingPreference.objects.create(
            person=person,
            channel=MarketingPreference.Channel.EMAIL,
            state=state,
            source=source,
            recorded_at=recorded_at,
            actor_user=actor_user,
        )
    else:
        preference.state = state
        preference.source = source
        preference.recorded_at = recorded_at
        preference.actor_user = actor_user
        preference.save(update_fields=["state", "source", "recorded_at", "actor_user", "updated_at"])

    history = MarketingPreferenceHistory.objects.create(
        preference=preference,
        channel=preference.channel,
        state=state,
        source=source,
        recorded_at=recorded_at,
        actor_user=actor_user,
    )
    action = (
        AuditEvent.Action.MARKETING_PREFERENCE_OPTED_IN
        if state == MarketingPreference.State.OPTED_IN
        else AuditEvent.Action.MARKETING_PREFERENCE_OPTED_OUT
    )
    record_audit_event(
        action=action,
        actor_user=actor_user,
        entity_type="MarketingPreference",
        entity_id=preference.id,
        changes={
            "state": {"from": previous_state, "to": state},
            "source": {"from": previous_source, "to": source},
        },
        metadata={"person_id": str(person.id), "channel": preference.channel},
    )
    enqueue_person_sync_job(
        person=person,
        provider=MAILCHIMP_PROVIDER,
        job_type=EMAIL_MARKETING_PREFERENCE_SYNC,
        source_event_id=history.id,
    )
    return MarketingPreferenceWriteResult(_effective_from_model(preference), changed=True)


def _effective_from_model(preference):
    return EffectiveMarketingPreference(
        person_id=preference.person_id,
        channel=preference.channel,
        state=preference.state,
        source=preference.source,
        recorded_at=preference.recorded_at,
        actor_user_id=preference.actor_user_id,
    )
