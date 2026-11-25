from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import models, transaction
from django.utils import timezone

from audit.models import (
    AuditEvent,
    AuditEventSubject,
    LegalHold,
    LegalHoldReleaseRequest,
    RetentionPolicyState,
    RetentionPolicyVersion,
)
from audit.services import record_audit_event
from staff_access.models import CapabilityDefinition
from staff_access.permissions import user_has_capability


def _reason(value):
    value = str(value or "").strip()
    if not value:
        raise ValidationError({"reason": "A reason is required."})
    if len(value) > 4000:
        raise ValidationError({"reason": "The reason must be 4000 characters or fewer."})
    return value


def _can_manage(actor):
    if not getattr(actor, "is_authenticated", False) or not getattr(actor, "is_active", False):
        return False
    return bool(getattr(actor, "is_superuser", False) or user_has_capability(actor, CapabilityDefinition.LEGAL_HOLD_ADMIN))


def _require_manager(actor):
    if not _can_manage(actor):
        raise PermissionDenied("Legal-hold administration is required.")


@transaction.atomic
def create_legal_hold(*, actor_user, reason, person=None, audit_event=None, request=None):
    _require_manager(actor_user)
    reason = _reason(reason)
    if (person is None) == (audit_event is None):
        raise ValidationError("Exactly one Person or AuditEvent hold target is required.")
    hold = LegalHold.objects.create(
        scope=LegalHold.Scope.PERSON if person is not None else LegalHold.Scope.EVENT,
        person=person,
        audit_event=audit_event,
        reason=reason,
        placed_by=actor_user,
    )
    subject_person = person
    if subject_person is None and audit_event is not None:
        subject = audit_event.subjects.filter(subject_type=AuditEventSubject.SubjectType.PERSON).first()
        subject_person = getattr(subject, "person", None)
    record_audit_event(
        action=AuditEvent.Action.LEGAL_HOLD_PLACED,
        actor_user=actor_user,
        entity_type="LegalHold",
        entity_id=hold.public_id,
        metadata={"scope": hold.scope, "person_id": str(person.pk) if person is not None else None},
        audit_domain=AuditEvent.Domain.PRIVACY,
        subject_person=subject_person,
    )
    return hold


@transaction.atomic
def request_legal_hold_release(*, actor_user, hold, reason):
    _require_manager(actor_user)
    hold = LegalHold.objects.select_for_update().get(pk=hold.pk)
    if hold.status != LegalHold.Status.ACTIVE:
        raise ValidationError("Only active legal holds may be released.")
    if LegalHoldReleaseRequest.objects.filter(
        legal_hold=hold,
        status=LegalHoldReleaseRequest.Status.PENDING,
    ).exists():
        raise ValidationError("A release request is already pending for this legal hold.")
    reason = _reason(reason)
    request = LegalHoldReleaseRequest.objects.create(
        legal_hold=hold,
        reason=reason,
        requested_by=actor_user,
    )
    subject_person = hold.person
    if subject_person is None and hold.audit_event_id:
        subject = hold.audit_event.subjects.filter(subject_type=AuditEventSubject.SubjectType.PERSON).first()
        subject_person = getattr(subject, "person", None)
    record_audit_event(
        action=AuditEvent.Action.LEGAL_HOLD_RELEASE_REQUESTED,
        actor_user=actor_user,
        entity_type="LegalHold",
        entity_id=hold.public_id,
        metadata={"release_request_id": str(request.public_id)},
        audit_domain=AuditEvent.Domain.PRIVACY,
        subject_person=subject_person,
    )
    return request


@transaction.atomic
def approve_legal_hold_release(*, actor_user, release_request, reason):
    _require_manager(actor_user)
    reason = _reason(reason)
    release_request = LegalHoldReleaseRequest.objects.select_related("legal_hold", "requested_by").get(pk=release_request.pk)
    hold = LegalHold.objects.select_for_update().get(pk=release_request.legal_hold_id)
    release_request = LegalHoldReleaseRequest.objects.select_for_update().select_related("legal_hold", "requested_by").get(pk=release_request.pk)
    if release_request.status != LegalHoldReleaseRequest.Status.PENDING:
        raise ValidationError("The release request is no longer pending.")
    if hold.status != LegalHold.Status.ACTIVE:
        raise ValidationError("The legal hold is no longer active.")
    if release_request.requested_by_id == actor_user.pk and not getattr(actor_user, "is_superuser", False):
        raise PermissionDenied("A legal-hold release cannot be self-approved.")
    release_request.status = LegalHoldReleaseRequest.Status.APPROVED
    release_request.approved_by = actor_user
    release_request.approved_at = timezone.now()
    release_request.approval_reason = reason
    release_request.save(update_fields=["status", "approved_by", "approved_at", "approval_reason"])
    hold.status = LegalHold.Status.RELEASED
    hold.released_by = actor_user
    hold.released_at = timezone.now()
    hold.release_reason = reason
    hold.save(update_fields=["status", "released_by", "released_at", "release_reason", "updated_at"])
    subject_person = hold.person
    if subject_person is None and hold.audit_event_id:
        subject = hold.audit_event.subjects.filter(subject_type=AuditEventSubject.SubjectType.PERSON).first()
        subject_person = getattr(subject, "person", None)
    record_audit_event(
        action=AuditEvent.Action.LEGAL_HOLD_RELEASED,
        actor_user=actor_user,
        entity_type="LegalHold",
        entity_id=hold.public_id,
        metadata={"release_request_id": str(release_request.public_id)},
        audit_domain=AuditEvent.Domain.PRIVACY,
        subject_person=subject_person,
    )
    return hold


@transaction.atomic
def direct_release_legal_hold(*, actor_user, hold, reason):
    if not getattr(actor_user, "is_active", False) or not getattr(actor_user, "is_superuser", False):
        raise PermissionDenied("An active Django superuser is required.")
    reason = _reason(reason)
    hold = LegalHold.objects.select_for_update().get(pk=hold.pk)
    if hold.status != LegalHold.Status.ACTIVE:
        raise ValidationError("Only active legal holds may be released.")
    pending_request = (
        LegalHoldReleaseRequest.objects.select_for_update()
        .filter(legal_hold=hold, status=LegalHoldReleaseRequest.Status.PENDING)
        .first()
    )
    now = timezone.now()
    if pending_request is not None:
        pending_request.status = LegalHoldReleaseRequest.Status.APPROVED
        pending_request.approved_by = actor_user
        pending_request.approved_at = now
        pending_request.approval_reason = reason
        pending_request.save(update_fields=["status", "approved_by", "approved_at", "approval_reason"])
    hold.status = LegalHold.Status.RELEASED
    hold.released_by = actor_user
    hold.released_at = now
    hold.release_reason = reason
    hold.save(update_fields=["status", "released_by", "released_at", "release_reason", "updated_at"])
    subject_person = hold.person
    if subject_person is None and hold.audit_event_id:
        subject = hold.audit_event.subjects.filter(subject_type=AuditEventSubject.SubjectType.PERSON).first()
        subject_person = getattr(subject, "person", None)
    record_audit_event(
        action=AuditEvent.Action.LEGAL_HOLD_RELEASED,
        actor_user=actor_user,
        entity_type="LegalHold",
        entity_id=hold.public_id,
        metadata={
            "operation": "LEGAL_HOLD_DIRECT_RELEASE",
            "superuser_authority": True,
            "release_request_id": str(pending_request.public_id) if pending_request else None,
        },
        audit_domain=AuditEvent.Domain.PRIVACY,
        subject_person=subject_person,
    )
    return hold


@transaction.atomic
def reject_legal_hold_release(*, actor_user, release_request, reason):
    _require_manager(actor_user)
    reason = _reason(reason)
    release_request = LegalHoldReleaseRequest.objects.select_related("legal_hold", "requested_by").get(pk=release_request.pk)
    hold = LegalHold.objects.select_for_update().get(pk=release_request.legal_hold_id)
    release_request = LegalHoldReleaseRequest.objects.select_for_update().select_related("legal_hold", "requested_by").get(pk=release_request.pk)
    if release_request.status != LegalHoldReleaseRequest.Status.PENDING:
        raise ValidationError("The release request is no longer pending.")
    if hold.status != LegalHold.Status.ACTIVE:
        raise ValidationError("The legal hold is no longer active.")
    if release_request.requested_by_id == actor_user.pk and not getattr(actor_user, "is_superuser", False):
        raise PermissionDenied("A legal-hold release cannot be self-rejected.")
    release_request.status = LegalHoldReleaseRequest.Status.REJECTED
    release_request.rejected_by = actor_user
    release_request.rejected_at = timezone.now()
    release_request.rejection_reason = reason
    release_request.save(update_fields=["status", "rejected_by", "rejected_at", "rejection_reason"])
    subject_person = hold.person
    if subject_person is None and hold.audit_event_id:
        subject = hold.audit_event.subjects.filter(subject_type=AuditEventSubject.SubjectType.PERSON).first()
        subject_person = getattr(subject, "person", None)
    record_audit_event(
        action=AuditEvent.Action.LEGAL_HOLD_RELEASE_REQUESTED,
        actor_user=actor_user,
        entity_type="LegalHoldReleaseRequest",
        entity_id=release_request.public_id,
        metadata={
            "lifecycle": "REJECTED",
            "operation": "LEGAL_HOLD_RELEASE_REJECTED",
            "outcome": "REJECTED",
            "release_request_id": str(release_request.public_id),
        },
        audit_domain=AuditEvent.Domain.PRIVACY,
        subject_person=subject_person,
    )
    return release_request


@transaction.atomic
def create_retention_policy_version(
    *, actor_user, audit_domain, retention_class, retention_days, effective_at=None, reason=""
):
    _require_manager(actor_user)
    reason = _reason(reason)
    retention_class = str(retention_class or "").strip()
    if not retention_class:
        raise ValidationError({"retention_class": "A retention class is required."})
    if retention_days is not None and retention_days <= 0:
        raise ValidationError("retention_days must be positive or null.")
    state, _created = RetentionPolicyState.objects.select_for_update().get_or_create(singleton_key=1)
    state.version += 1
    state.updated_by = actor_user
    state.reason = reason
    state.save(update_fields=["version", "updated_by", "reason", "updated_at"])
    policy = RetentionPolicyVersion.objects.create(
        audit_domain=audit_domain,
        retention_class=retention_class,
        version=state.version,
        retention_days=retention_days,
        effective_at=effective_at or timezone.now(),
        reason=reason,
        created_by=actor_user,
    )
    record_audit_event(
        action=AuditEvent.Action.RETENTION_POLICY_VERSION_CREATED,
        actor_user=actor_user,
        entity_type="RetentionPolicyVersion",
        entity_id=policy.pk,
        metadata={"audit_domain": audit_domain, "version": policy.version, "retention_days": retention_days},
        audit_domain=AuditEvent.Domain.PRIVACY,
    )
    return policy


def evaluate_retention(*, actor_user, evaluation_timestamp=None, audit_domain=None):
    _require_manager(actor_user)
    evaluation_timestamp = evaluation_timestamp or timezone.now()
    events = AuditEvent.objects.all().order_by("pk")
    if audit_domain:
        events = events.filter(audit_domain=audit_domain)
    report = {"evaluation_timestamp": evaluation_timestamp, "eligible": [], "held": [], "unclassified": [], "unresolved_subject": [], "excluded": []}
    for event in events.iterator():
        has_normalized_subject = AuditEventSubject.objects.filter(event_id=event.pk).exists()
        if event.audit_domain == AuditEvent.Domain.PRIVACY and not has_normalized_subject:
            report["unresolved_subject"].append(str(event.pk))
            continue
        if event.retention_policy_version is None or not event.retention_class:
            report["unclassified"].append(str(event.pk))
            continue
        held = LegalHold.objects.filter(status=LegalHold.Status.ACTIVE).filter(
            models.Q(audit_event_id=event.pk) | models.Q(person__audit_subjects__event=event.pk)
        ).exists()
        if held:
            report["held"].append(str(event.pk))
            continue
        try:
            policy = RetentionPolicyVersion.objects.get(
                audit_domain=event.audit_domain,
                retention_class=event.retention_class,
                version=event.retention_policy_version,
            )
        except RetentionPolicyVersion.DoesNotExist:
            report["excluded"].append(str(event.pk))
            continue
        if policy.retention_days is None:
            report["excluded"].append(str(event.pk))
        elif event.occurred_at + timedelta(days=policy.retention_days) <= evaluation_timestamp:
            report["eligible"].append(str(event.pk))
        else:
            report["excluded"].append(str(event.pk))
    return report
