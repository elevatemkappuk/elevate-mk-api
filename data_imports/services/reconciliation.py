from django.db import transaction
from django.utils import timezone

from audit.models import AuditEvent
from audit.services import record_audit_event
from data_imports.models import ImportBatch, ImportRecord
from data_imports.services.source_data import person_identity_source
from people.models import Person
from people.services import CreateNewIdentityCollision, evaluate_create_new_identity


class ReconciliationConflict(Exception):
    """Raised when a staff decision cannot be safely applied."""


class ReconciliationValidationError(Exception):
    """Raised when a requested decision is not valid for the analyzed record."""


def resolve_import_record(*, batch_id, record_id, action, person_id, reviewed_by, confirm_identity_override=False):
    """Persist one explicit staff decision without mutating authoritative CRM records."""
    with transaction.atomic():
        batch = ImportBatch.objects.select_for_update().filter(pk=batch_id).first()
        if batch is None:
            raise ImportBatch.DoesNotExist

        record = batch.records.select_for_update().filter(pk=record_id).first()
        if record is None:
            raise ImportRecord.DoesNotExist
        if record.status != ImportRecord.Status.REVIEW_REQUIRED:
            raise ReconciliationConflict("This import record is no longer awaiting review.")

        if action == "SAME_PERSON":
            resolved_person = _validated_candidate(record, person_id)
            record.resolved_person = resolved_person
            record.resolution_method = ImportRecord.ResolutionMethod.STAFF_MATCH
            record.resolution_reason = "STAFF_CONFIRMED_SAME_PERSON"
            audit_action = AuditEvent.Action.IMPORT_RECORD_MATCH_CONFIRMED
        elif action == "DIFFERENT_PERSON":
            source = person_identity_source(record)
            identity_policy = evaluate_create_new_identity(
                primary_email=source.get("email"),
                mobile=source.get("mobile"),
                staff_confirmed_different=True,
                confirm_identity_override=confirm_identity_override,
            )
            if not identity_policy.is_safe_to_create:
                raise ReconciliationValidationError(
                    "This record uses contact details already associated with another CRM Person. Confirm that these are different people before creating a separate Person."
                )
            match_evidence = dict(record.match_evidence or {})
            match_evidence["staff_create_new_review"] = identity_policy.review_evidence()
            record.match_evidence = match_evidence
            record.resolved_person = None
            record.resolution_method = ImportRecord.ResolutionMethod.STAFF_CREATE_NEW
            record.resolution_reason = "STAFF_CONFIRMED_DIFFERENT_PERSON"
            audit_action = AuditEvent.Action.IMPORT_RECORD_CREATE_NEW_CONFIRMED
        else:
            raise ReconciliationValidationError("Unsupported reconciliation action.")

        record.status = ImportRecord.Status.RESOLVED
        record.outcome = None
        record.reviewed_by = reviewed_by
        record.reviewed_at = timezone.now()
        record.save(
            update_fields=[
                "resolved_person",
                "resolution_method",
                "resolution_reason",
                "status",
                "outcome",
                "reviewed_by",
                "reviewed_at",
                "match_evidence",
                "updated_at",
            ]
        )

        if batch.records.filter(status=ImportRecord.Status.REVIEW_REQUIRED).exists():
            next_status = ImportBatch.Status.READY_FOR_REVIEW
        else:
            next_status = ImportBatch.Status.READY_FOR_IMPORT
        if batch.status != next_status:
            batch.status = next_status
            batch.save(update_fields=["status", "updated_at"])

        metadata = {
            "import_batch_id": str(batch.id),
            "import_record_id": str(record.id),
            "resolution_method": record.resolution_method,
        }
        if record.resolved_person_id is not None:
            metadata["resolved_person_id"] = str(record.resolved_person_id)
        if action == "DIFFERENT_PERSON" and identity_policy.requires_review:
            metadata["identity_collision"] = identity_policy.collision.value
            metadata["identity_collision_person_ids"] = [str(person_id) for person_id in identity_policy.matched_person_ids]
            metadata["identity_override_confirmed"] = bool(confirm_identity_override)
        record_audit_event(
            action=audit_action,
            actor_user=reviewed_by,
            entity_type="ImportRecord",
            entity_id=record.id,
            metadata=metadata,
        )

    return record


def _validated_candidate(record, person_id):
    candidate_ids = {
        candidate.get("person_id")
        for candidate in record.match_candidates
        if isinstance(candidate, dict) and isinstance(candidate.get("person_id"), int)
    }
    if person_id not in candidate_ids:
        raise ReconciliationValidationError("The selected Person is not an analyzer candidate for this record.")

    person = Person.objects.business().filter(pk=person_id).first()
    if person is None:
        raise ReconciliationValidationError("The selected Person is not available for reconciliation.")
    return person
