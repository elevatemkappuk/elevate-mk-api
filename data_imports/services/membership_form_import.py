from dataclasses import dataclass
from datetime import date, datetime

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from django.utils.text import slugify

from audit.models import AuditEvent
from audit.services import record_audit_event
from data_imports.models import ImportBatch, ImportRecord
from memberships.models import Membership
from people.models import Person
from people.services import find_business_duplicate_people, normalize_email, normalize_mobile
from professional_profiles.models import Industry, ProfessionalProfile


class MembershipFormImportError(Exception):
    """Base class for expected Membership Form import failures."""


class ImportBatchNotImportable(MembershipFormImportError):
    pass


class ImportBatchPreflightError(MembershipFormImportError):
    pass


@dataclass
class ImportResult:
    batch_id: int
    status: str
    valid_processed: int = 0
    people_created: int = 0
    people_matched: int = 0
    people_enriched: int = 0
    memberships_created: int = 0
    memberships_reused: int = 0
    profiles_created: int = 0
    profiles_enriched: int = 0
    invalid_skipped: int = 0


@dataclass
class ImportPlan:
    record: ImportRecord
    source: dict
    person: Person | None
    joined_at: date | None
    industry: Industry | None
    create_person: bool


MATCH_METHODS = {
    ImportRecord.ResolutionMethod.AUTO_MATCH,
    ImportRecord.ResolutionMethod.STAFF_MATCH,
}
CREATE_METHODS = {
    ImportRecord.ResolutionMethod.NO_MATCH,
    ImportRecord.ResolutionMethod.STAFF_CREATE_NEW,
}
PERSON_SOURCE_FIELDS = {
    "first_name": "first_name",
    "last_name": "last_name",
    "primary_email": "email",
    "mobile": "mobile",
    "location": "location",
    "age_range": "age_range",
    "gender": "gender",
}


def import_membership_form_batch(*, batch_id, imported_by=None) -> ImportResult:
    """Atomically import a fully resolved Membership Form batch into CRM records."""
    with transaction.atomic():
        batch = ImportBatch.objects.select_for_update().filter(pk=batch_id).first()
        if batch is None:
            raise ImportBatch.DoesNotExist
        _validate_batch(batch)

        records = list(
            batch.records.select_for_update().select_related("resolved_person").order_by(
                "source_row_identifier", "id"
            )
        )
        plans = _preflight_records(batch, records)
        result = ImportResult(batch_id=batch.id, status=ImportBatch.Status.IMPORTED)
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
            metadata={"import_batch_id": str(batch.id)},
            changes={
                "valid_processed": {"to": result.valid_processed},
                "invalid_skipped": {"to": result.invalid_skipped},
            },
        )

    return result


def _validate_batch(batch):
    if batch.source_type != ImportBatch.SourceType.MEMBERSHIP_FORM:
        raise ImportBatchNotImportable("Only Membership Form batches are importable.")
    if batch.status != ImportBatch.Status.READY_FOR_IMPORT:
        raise ImportBatchNotImportable("This import batch is not ready for import.")


def _preflight_records(batch, records):
    plans = []
    create_identity_keys = set()
    for record in records:
        if record.status == ImportRecord.Status.INVALID:
            if record.committed_at is not None or record.outcome is not None:
                raise ImportBatchPreflightError("The batch contains an already processed invalid record.")
            plans.append(ImportPlan(record, {}, None, None, None, False))
            continue
        if record.status == ImportRecord.Status.REVIEW_REQUIRED:
            raise ImportBatchPreflightError("The batch contains an unresolved review record.")
        if record.status != ImportRecord.Status.RESOLVED:
            raise ImportBatchPreflightError("The batch contains a record that is not resolved.")
        if record.committed_at is not None or record.outcome is not None:
            raise ImportBatchPreflightError("The batch contains an already processed record.")

        source = record.normalized_data or {}
        _validate_demographics(source)
        joined_at = _source_timestamp_date(source.get("source_timestamp"))
        method = record.resolution_method
        if method in MATCH_METHODS:
            if record.resolved_person_id is None:
                raise ImportBatchPreflightError("A matched record has no resolved Person.")
            person = Person.objects.select_for_update().filter(
                pk=record.resolved_person_id,
                record_type=Person.RecordType.BUSINESS,
            ).first()
            if person is None:
                raise ImportBatchPreflightError("A matched record resolves to an unavailable Person.")
            _validate_existing_membership(person)
            plans.append(ImportPlan(record, source, person, joined_at, _match_industry(source), False))
        elif method in CREATE_METHODS:
            if record.resolved_person_id is not None:
                raise ImportBatchPreflightError("A create-new record already has a resolved Person.")
            _validate_new_person_source(source)
            _validate_new_person_identity(source, create_identity_keys)
            plans.append(ImportPlan(record, source, None, joined_at, _match_industry(source), True))
        else:
            raise ImportBatchPreflightError("The batch contains an inconsistent resolution decision.")
    return plans


def _validate_existing_membership(person):
    membership = Membership.objects.select_for_update().filter(person=person).first()
    if membership is not None and membership.status != Membership.Status.ACTIVE:
        raise ImportBatchPreflightError("A resolved Person has a non-active Membership.")


def _validate_new_person_source(source):
    if not _value(source.get("first_name")) or not _value(source.get("last_name")):
        raise ImportBatchPreflightError("A create-new record requires first_name and last_name.")


def _validate_new_person_identity(source, create_identity_keys):
    email = _value(source.get("email"))
    mobile = _value(source.get("mobile"))
    if find_business_duplicate_people(primary_email=email, mobile=mobile):
        raise ImportBatchPreflightError("A create-new record now matches an existing Business Person.")
    keys = {
        ("email", normalize_email(email)),
        ("mobile", normalize_mobile(mobile)),
    }
    keys.discard(("email", ""))
    keys.discard(("mobile", ""))
    if create_identity_keys.intersection(keys):
        raise ImportBatchPreflightError("The batch contains duplicate create-new identity signals.")
    create_identity_keys.update(keys)


def _validate_demographics(source):
    age_range = _value(source.get("age_range"))
    gender = _value(source.get("gender"))
    if age_range and age_range not in Person.AgeRange.values:
        raise ImportBatchPreflightError("A record has an unsupported age_range.")
    if gender and gender not in Person.Gender.values:
        raise ImportBatchPreflightError("A record has an unsupported gender.")


def _source_timestamp_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        parsed_datetime = parse_datetime(value)
        if parsed_datetime is not None:
            return parsed_datetime.date()
        parsed_date = parse_date(value)
        if parsed_date is not None:
            return parsed_date
    raise ImportBatchPreflightError("A record has an invalid source timestamp.")


def _match_industry(source):
    source_industry = _value(source.get("industry"))
    if not source_industry:
        return None
    candidates = list(
        Industry.objects.filter(is_active=True).filter(
            models.Q(name__iexact=source_industry) | models.Q(slug=slugify(source_industry))
        ).distinct()[:2]
    )
    return candidates[0] if len(candidates) == 1 else None


def _import_record(plan, batch, imported_by, committed_at, result):
    person = plan.person
    created_person = False
    person_changes = []
    if plan.create_person:
        person = _create_person(plan.source)
        plan.record.resolved_person = person
        created_person = True
        result.people_created += 1
        _audit_person_created(batch, plan.record, person, imported_by)
    else:
        person_changes = _fill_missing_person_fields(person, plan.source)
        result.people_matched += 1
        if person_changes:
            result.people_enriched += 1
            _audit_person_enriched(batch, plan.record, person, person_changes, imported_by)

    membership_created = _create_or_reuse_membership(person, plan.joined_at)
    if membership_created:
        result.memberships_created += 1
        _audit_membership_created(batch, plan.record, person, imported_by)
    else:
        result.memberships_reused += 1

    profile_created, profile_changes = _create_or_fill_profile(person, plan.source, plan.industry)
    if profile_created:
        result.profiles_created += 1
        _audit_profile(batch, plan.record, person, imported_by, True, [])
    elif profile_changes:
        result.profiles_enriched += 1
        _audit_profile(batch, plan.record, person, imported_by, False, profile_changes)

    outcome = ImportRecord.Outcome.CREATED if created_person else (
        ImportRecord.Outcome.UPDATED if person_changes or membership_created or profile_created or profile_changes
        else ImportRecord.Outcome.MATCHED
    )
    plan.record.status = ImportRecord.Status.COMMITTED
    plan.record.outcome = outcome
    plan.record.committed_at = committed_at
    plan.record.save(update_fields=["resolved_person", "status", "outcome", "committed_at", "updated_at"])
    result.valid_processed += 1


def _create_person(source):
    person = Person(
        record_type=Person.RecordType.BUSINESS,
        first_name=_value(source.get("first_name")),
        last_name=_value(source.get("last_name")),
        primary_email=_value(source.get("email")) or None,
        mobile=_value(source.get("mobile")) or "",
        location=_value(source.get("location")) or "",
        age_range=_value(source.get("age_range")) or "",
        gender=_value(source.get("gender")) or "",
    )
    _save_validated(person)
    return person


def _fill_missing_person_fields(person, source):
    changed = []
    for person_field, source_field in PERSON_SOURCE_FIELDS.items():
        current = getattr(person, person_field)
        source_value = _value(source.get(source_field))
        if _missing(current) and source_value:
            setattr(person, person_field, source_value)
            changed.append(person_field)
    if changed:
        _save_validated(person, update_fields=[*changed, "updated_at"])
    return changed


def _create_or_reuse_membership(person, joined_at):
    membership = Membership.objects.select_for_update().filter(person=person).first()
    if membership is not None:
        if membership.status != Membership.Status.ACTIVE:
            raise ImportBatchPreflightError("A Person has a non-active Membership.")
        return False
    membership = Membership(
        person=person,
        status=Membership.Status.ACTIVE,
        joined_at=joined_at,
        membership_source=Membership.Source.MEMBERSHIP_FORM,
    )
    _save_validated(membership)
    return True


def _create_or_fill_profile(person, source, industry):
    source_fields = {
        "job_title": _value(source.get("job_title")),
        "linkedin_url": _value(source.get("linkedin_url")),
        "industry": industry,
    }
    profile = ProfessionalProfile.objects.select_for_update().filter(person=person).first()
    if profile is None:
        values = {field: value for field, value in source_fields.items() if value}
        if not values:
            return False, []
        profile = ProfessionalProfile(person=person, **values)
        _save_validated(profile)
        return True, []

    changed = []
    for field, source_value in source_fields.items():
        if _missing(getattr(profile, field)) and source_value:
            setattr(profile, field, source_value)
            changed.append(field)
    if changed:
        _save_validated(profile, update_fields=[*changed, "updated_at"])
    return False, changed


def _skip_invalid_record(record, committed_at):
    record.outcome = ImportRecord.Outcome.SKIPPED
    record.committed_at = committed_at
    record.save(update_fields=["outcome", "committed_at", "updated_at"])


def _save_validated(instance, update_fields=None):
    try:
        instance.full_clean()
    except ValidationError as error:
        raise ImportBatchPreflightError(error.message_dict) from error
    instance.save(update_fields=update_fields)


def _value(value):
    if value is None:
        return ""
    return str(value).strip()


def _missing(value):
    return value is None or (isinstance(value, str) and not value.strip())


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


def _audit_membership_created(batch, record, person, actor):
    record_audit_event(
        action=AuditEvent.Action.MEMBERSHIP_CREATED,
        actor_user=actor,
        entity_type="Membership",
        entity_id=person.membership.id,
        changes={"created": {"to": True}},
        metadata=_audit_metadata(batch, record, person),
    )


def _audit_profile(batch, record, person, actor, created, fields):
    profile = person.professional_profile
    record_audit_event(
        action=(
            AuditEvent.Action.PROFESSIONAL_PROFILE_CREATED
            if created
            else AuditEvent.Action.PROFESSIONAL_PROFILE_UPDATED
        ),
        actor_user=actor,
        entity_type="ProfessionalProfile",
        entity_id=profile.id,
        changes={"created": {"to": True}} if created else {"fields": {"changed": sorted(fields)}},
        metadata=_audit_metadata(batch, record, person),
    )
