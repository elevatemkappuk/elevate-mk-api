from django.db import IntegrityError, transaction
from django.utils import timezone

from audit.models import AuditEvent
from audit.services import record_audit_event
from external_references.models import ExternalPersonReference, ExternalPersonSyncJob
from people.models import Person


def enqueue_person_sync_job(
    *, person, provider, job_type, source_event_id, available_at=None,
    previous_email=None, requested_email=None,
):
    """Create one durable sync job for a source-domain event, idempotently."""
    job, _ = ExternalPersonSyncJob.objects.get_or_create(
        provider=provider,
        job_type=job_type,
        source_event_id=source_event_id,
        defaults={
            "person": person,
            "available_at": available_at or timezone.now(),
            "previous_email": previous_email,
            "requested_email": requested_email,
        },
    )
    if job.person_id != person.id:
        raise ValueError("External sync event is already assigned to a different Person.")
    return job


@transaction.atomic
def enqueue_coalesced_person_sync_job(*, person, provider, job_type, source_event_id, available_at=None):
    """Reuse an existing pending job for this Person and job type when safe."""
    existing = ExternalPersonSyncJob.objects.select_for_update().filter(
        person=person,
        provider=provider,
        job_type=job_type,
        status=ExternalPersonSyncJob.Status.PENDING,
    ).order_by("id").first()
    if existing is not None:
        return existing
    return enqueue_person_sync_job(
        person=person,
        provider=provider,
        job_type=job_type,
        source_event_id=source_event_id,
        available_at=available_at,
    )


@transaction.atomic
def attach_person_reference(*, person, provider, reference_type, external_id, actor_user=None):
    """Idempotently link an external person identity and audit the authoritative change."""
    person = Person.objects.select_for_update().get(pk=person.pk)
    if person.record_type != Person.RecordType.BUSINESS:
        raise ValueError("External person references may only be attached to BUSINESS People.")

    provider = provider.strip().upper()
    external_id = external_id.strip()
    reference = ExternalPersonReference.objects.select_for_update().filter(
        provider=provider,
        reference_type=reference_type,
        external_id=external_id,
    ).first()

    if reference is not None and reference.person_id != person.id:
        raise ValueError("External identity is already attached to a different Person.")
    created = False
    if reference is None:
        try:
            with transaction.atomic():
                reference = ExternalPersonReference.objects.create(
                    person=person,
                    provider=provider,
                    reference_type=reference_type,
                    external_id=external_id,
                )
            created = True
        except IntegrityError:
            reference = ExternalPersonReference.objects.select_for_update().filter(
                provider=provider,
                reference_type=reference_type,
                external_id=external_id,
            ).first()
            if reference is None:
                raise ValueError("Person already has an external identity of this provider and type.")
            if reference.person_id != person.id:
                raise ValueError("External identity is already attached to a different Person.")

    was_revoked = reference.status == ExternalPersonReference.Status.REVOKED
    if was_revoked:
        reference.status = ExternalPersonReference.Status.ACTIVE
        reference.revoked_at = None
        reference.save(update_fields=["status", "revoked_at", "updated_at"])

    if was_revoked:
        action = AuditEvent.Action.EXTERNAL_PERSON_REFERENCE_REACTIVATED
    elif created:
        action = AuditEvent.Action.EXTERNAL_PERSON_REFERENCE_LINKED
    else:
        return reference

    record_audit_event(
        action=action,
        entity_type="ExternalPersonReference",
        entity_id=reference.pk,
        actor_user=actor_user,
        changes={"person_id": str(person.pk), "status": reference.status},
        metadata={
            "provider": reference.provider,
            "reference_type": reference.reference_type,
            "external_id": reference.external_id,
        },
    )
    return reference


@transaction.atomic
def revoke_person_reference(*, reference, actor_user=None):
    """Retain the external identity for provenance while stopping it from being active."""
    reference = ExternalPersonReference.objects.select_for_update().get(pk=reference.pk)
    if reference.status == ExternalPersonReference.Status.REVOKED:
        return reference

    reference.status = ExternalPersonReference.Status.REVOKED
    reference.revoked_at = timezone.now()
    reference.save(update_fields=["status", "revoked_at", "updated_at"])
    record_audit_event(
        action=AuditEvent.Action.EXTERNAL_PERSON_REFERENCE_REVOKED,
        entity_type="ExternalPersonReference",
        entity_id=reference.pk,
        actor_user=actor_user,
        changes={"status": reference.status},
        metadata={
            "person_id": str(reference.person_id),
            "provider": reference.provider,
            "reference_type": reference.reference_type,
            "external_id": reference.external_id,
        },
    )
    return reference
