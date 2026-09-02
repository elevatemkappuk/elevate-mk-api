from django.db import IntegrityError, transaction

from events.models import Event, EventParticipation, ExternalEventReference


@transaction.atomic
def get_or_create_event_from_reference(*, provider, external_event_id, event_defaults, import_record=None):
    """Reuse a provider event identity without putting provider data on Event."""
    reference = ExternalEventReference.objects.select_for_update().filter(
        provider=provider,
        reference_type=ExternalEventReference.ReferenceType.EVENT,
        external_id=external_event_id,
    ).first()
    if reference is not None:
        return reference.event, False

    try:
        with transaction.atomic():
            event = Event.objects.create(**event_defaults)
            ExternalEventReference.objects.create(
                provider=provider,
                reference_type=ExternalEventReference.ReferenceType.EVENT,
                external_id=external_event_id,
                event=event,
                import_record=import_record,
            )
            return event, True
    except IntegrityError:
        reference = ExternalEventReference.objects.select_for_update().get(
            provider=provider,
            reference_type=ExternalEventReference.ReferenceType.EVENT,
            external_id=external_event_id,
        )
        return reference.event, False


@transaction.atomic
def get_or_create_event_participation(*, event, person, participation_defaults=None):
    """Return the single authoritative participation for a Person and Event."""
    return EventParticipation.objects.get_or_create(
        event=event,
        person=person,
        defaults=participation_defaults or {},
    )


@transaction.atomic
def attach_participation_reference(*, provider, external_participation_id, event, participation, import_record=None):
    """Attach idempotent external registration/order provenance to a participation."""
    reference, created = ExternalEventReference.objects.select_for_update().get_or_create(
        provider=provider,
        reference_type=ExternalEventReference.ReferenceType.PARTICIPATION,
        external_id=external_participation_id,
        defaults={
            "event": event,
            "participation": participation,
            "import_record": import_record,
        },
    )
    if reference.event_id != event.id or reference.participation_id != participation.id:
        raise ValueError("External participation reference is already attached to a different authoritative record.")
    return reference, created
