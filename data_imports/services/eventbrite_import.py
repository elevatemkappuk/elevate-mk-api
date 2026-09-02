from dataclasses import dataclass
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from audit.models import AuditEvent
from audit.services import record_audit_event
from data_imports.models import ImportBatch, ImportRecord
from data_imports.services.membership_form_import import (
    CREATE_METHODS,
    MATCH_METHODS,
    ImportBatchNotImportable,
    ImportBatchPreflightError,
    _create_person,
    _fill_missing_person_fields,
    _save_validated,
    _validate_new_person_identity,
    _value,
)
from data_imports.services.source_data import person_identity_source
from events.models import Event, EventParticipation
from events.services import get_or_create_event_from_reference, get_or_create_event_participation
from people.models import Person
from people.services import normalize_email, normalize_mobile


EVENTBRITE_PROVIDER = "EVENTBRITE"


@dataclass
class EventbriteImportResult:
    batch_id: int
    status: str
    valid_processed: int = 0
    people_created: int = 0
    people_matched: int = 0
    people_enriched: int = 0
    events_created: int = 0
    events_reused: int = 0
    participations_created: int = 0
    participations_reused: int = 0
    participations_preserved: int = 0
    invalid_skipped: int = 0


@dataclass
class EventbriteImportPlan:
    record: ImportRecord
    person_source: dict
    event_source: dict
    person: Person | None
    create_person: bool
    created_person_plan: "EventbriteImportPlan | None" = None


def import_eventbrite_batch(*, batch_id, imported_by=None) -> EventbriteImportResult:
    """Atomically import one fully reconciled Eventbrite batch into Events records."""
    with transaction.atomic():
        batch = ImportBatch.objects.select_for_update().filter(pk=batch_id).first()
        if batch is None:
            raise ImportBatch.DoesNotExist
        _validate_batch(batch)

        # resolved_person is nullable; avoid a joined FOR UPDATE query on PostgreSQL.
        records = list(batch.records.select_for_update().order_by("source_row_identifier", "id"))
        plans = _preflight_records(records)
        result = EventbriteImportResult(batch_id=batch.id, status=ImportBatch.Status.IMPORTED)
        committed_at = timezone.now()

        for plan in plans:
            if plan.record.status == ImportRecord.Status.INVALID:
                _skip_invalid_record(plan.record, committed_at)
                result.invalid_skipped += 1
                continue
            _import_record(plan, batch, imported_by, committed_at, result)

        batch.status = ImportBatch.Status.IMPORTED
        batch.completed_at = committed_at
        batch.save(update_fields=["status", "completed_at", "updated_at"])
        record_audit_event(
            action=AuditEvent.Action.IMPORT_BATCH_IMPORTED,
            actor_user=imported_by,
            entity_type="ImportBatch",
            entity_id=batch.id,
            metadata={
                "import_batch_id": str(batch.id),
                "source_type": batch.source_type,
                "processed_count": result.valid_processed,
                "people_created_count": result.people_created,
                "people_matched_count": result.people_matched,
                "events_created_count": result.events_created,
                "events_reused_count": result.events_reused,
                "participations_created_count": result.participations_created,
                "participations_reused_count": result.participations_reused,
                "participations_preserved_count": result.participations_preserved,
                "skipped_count": result.invalid_skipped,
            },
        )
    return result


def _validate_batch(batch):
    if batch.source_type != ImportBatch.SourceType.EVENTBRITE:
        raise ImportBatchNotImportable("Only Eventbrite batches are importable.")
    if batch.status != ImportBatch.Status.READY_FOR_IMPORT:
        raise ImportBatchNotImportable("This import batch is not ready for import.")


def _preflight_records(records):
    plans = []
    create_identity_keys = set()
    create_person_plans = {}
    for record in records:
        if record.status == ImportRecord.Status.INVALID:
            if record.committed_at is not None or record.outcome is not None:
                raise ImportBatchPreflightError("The batch contains an already processed invalid record.")
            plans.append(EventbriteImportPlan(record, {}, {}, None, False))
            continue
        if record.status == ImportRecord.Status.REVIEW_REQUIRED:
            raise ImportBatchPreflightError("The batch contains an unresolved review record.")
        if record.status != ImportRecord.Status.RESOLVED:
            raise ImportBatchPreflightError("The batch contains a record that is not resolved.")
        if record.committed_at is not None or record.outcome is not None:
            raise ImportBatchPreflightError("The batch contains an already processed record.")

        person_source = _person_source(record)
        event_source = _event_source(record)
        _validate_event_source(event_source)
        if record.resolution_method in MATCH_METHODS:
            if record.resolved_person_id is None:
                raise ImportBatchPreflightError("A matched record has no resolved Person.")
            person = Person.objects.select_for_update().business().filter(pk=record.resolved_person_id).first()
            if person is None:
                raise ImportBatchPreflightError("A matched record resolves to an unavailable Person.")
            plans.append(EventbriteImportPlan(record, person_source, event_source, person, False))
        elif record.resolution_method in CREATE_METHODS:
            if record.resolved_person_id is not None:
                raise ImportBatchPreflightError("A create-new record already has a resolved Person.")
            _validate_new_person_source(person_source)
            identity_keys = _identity_keys(person_source)
            existing_plans = {
                id(create_person_plans[key]): create_person_plans[key]
                for key in identity_keys
                if key in create_person_plans
            }
            if len(existing_plans) > 1:
                raise ImportBatchPreflightError("The batch contains inconsistent duplicate create-new identity signals.")
            _validate_new_person_identity(
                record,
                person_source,
                set() if existing_plans else create_identity_keys,
                staff_create_new=record.resolution_method == ImportRecord.ResolutionMethod.STAFF_CREATE_NEW,
            )
            if existing_plans:
                plans.append(EventbriteImportPlan(
                    record, person_source, event_source, None, False, next(iter(existing_plans.values()))
                ))
            else:
                plan = EventbriteImportPlan(record, person_source, event_source, None, True)
                plans.append(plan)
                for key in identity_keys:
                    create_person_plans[key] = plan
        else:
            raise ImportBatchPreflightError("The batch contains an inconsistent resolution decision.")
    return plans


def _identity_keys(source):
    keys = {
        ("email", normalize_email(source.get("email"))),
        ("mobile", normalize_mobile(source.get("mobile"))),
    }
    keys.discard(("email", ""))
    keys.discard(("mobile", ""))
    return keys


def _person_source(record):
    source = person_identity_source(record)
    return {
        "first_name": _value(source.get("first_name")),
        "last_name": _value(source.get("last_name")),
        "email": _value(source.get("email")),
        "mobile": _value(source.get("mobile")),
        "location": _value(source.get("city")),
    }


def _event_source(record):
    source = (record.normalized_data or {}).get("event")
    return source if isinstance(source, dict) else {}


def _validate_new_person_source(source):
    if not source["first_name"] or not source["last_name"]:
        raise ImportBatchPreflightError("A create-new record requires first_name and last_name.")


def _validate_event_source(source):
    external_event_id = _value(source.get("external_event_id"))
    name = _value(source.get("name"))
    start_at = parse_datetime(_value(source.get("start_at")))
    timezone_name = _value(source.get("timezone"))
    if not external_event_id or not name or start_at is None or start_at.tzinfo is None or not timezone_name:
        raise ImportBatchPreflightError("A record has invalid Eventbrite event data.")
    try:
        ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as error:
        raise ImportBatchPreflightError("A record has invalid Eventbrite event data.") from error


def _import_record(plan, batch, imported_by, committed_at, result):
    person = plan.person
    created_person = False
    person_changes = []
    if plan.created_person_plan is not None:
        person = plan.created_person_plan.person
        if person is None:
            raise ImportBatchPreflightError("The batch has an invalid duplicate buyer plan.")
        plan.record.resolved_person = person
    elif plan.create_person:
        person = _create_person(plan.person_source)
        plan.person = person
        plan.record.resolved_person = person
        created_person = True
        result.people_created += 1
        _audit_person_created(batch, plan.record, person, imported_by)
    else:
        person_changes = _fill_missing_person_fields(person, plan.person_source)
        result.people_matched += 1
        if person_changes:
            result.people_enriched += 1
            _audit_person_enriched(batch, plan.record, person, person_changes, imported_by)

    event, created_event = get_or_create_event_from_reference(
        provider=EVENTBRITE_PROVIDER,
        external_event_id=_value(plan.event_source["external_event_id"]),
        event_defaults=_event_defaults(plan.event_source),
        import_record=plan.record,
    )
    if created_event:
        result.events_created += 1
    else:
        result.events_reused += 1
        _fill_missing_event_fields(event, plan.event_source)

    participation, created_participation = get_or_create_event_participation(
        event=event,
        person=person,
        participation_defaults={"status": EventParticipation.Status.REGISTERED},
    )
    if created_participation:
        result.participations_created += 1
    elif participation.status == EventParticipation.Status.REGISTERED:
        result.participations_reused += 1
    else:
        result.participations_preserved += 1

    outcome = ImportRecord.Outcome.CREATED if created_person else (
        ImportRecord.Outcome.UPDATED if person_changes or created_event or created_participation else ImportRecord.Outcome.MATCHED
    )
    plan.record.status = ImportRecord.Status.COMMITTED
    plan.record.outcome = outcome
    plan.record.committed_at = committed_at
    plan.record.save(update_fields=["resolved_person", "status", "outcome", "committed_at", "updated_at"])
    result.valid_processed += 1


def _event_defaults(source):
    return {
        "name": _value(source["name"]),
        "start_at": parse_datetime(_value(source["start_at"])),
        "timezone": _value(source["timezone"]),
        "location_name": _value(source.get("location_name")),
    }


def _fill_missing_event_fields(event, source):
    event = Event.objects.select_for_update().get(pk=event.pk)
    source_values = _event_defaults(source)
    changed = []
    for field, source_value in source_values.items():
        current = getattr(event, field)
        if (current is None or (isinstance(current, str) and not current.strip())) and source_value:
            setattr(event, field, source_value)
            changed.append(field)
    if changed:
        _save_validated(event, update_fields=[*changed, "updated_at"])
    return event, changed


def _skip_invalid_record(record, committed_at):
    record.outcome = ImportRecord.Outcome.SKIPPED
    record.committed_at = committed_at
    record.save(update_fields=["outcome", "committed_at", "updated_at"])


def _audit_metadata(batch, record, person):
    return {
        "import_batch_id": str(batch.id),
        "import_record_id": str(record.id),
        "person_id": str(person.id),
    }


def _audit_person_created(batch, record, person, actor):
    record_audit_event(
        action=AuditEvent.Action.PERSON_CREATED,
        actor_user=actor,
        entity_type="Person",
        entity_id=person.id,
        changes={"created": {"to": True}},
        metadata=_audit_metadata(batch, record, person),
    )


def _audit_person_enriched(batch, record, person, fields, actor):
    record_audit_event(
        action=AuditEvent.Action.PERSON_UPDATED,
        actor_user=actor,
        entity_type="Person",
        entity_id=person.id,
        changes={"fields": {"changed": sorted(fields)}},
        metadata=_audit_metadata(batch, record, person),
    )
