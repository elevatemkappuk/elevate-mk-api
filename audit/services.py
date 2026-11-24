from dataclasses import dataclass
from contextvars import ContextVar
import uuid
from django.db import transaction

from audit.models import AuditEvent, AuditEventSubject

_FORBIDDEN_METADATA_KEYS = frozenset({
    "password", "password_hash", "token", "access_token", "refresh_token",
    "session", "session_id", "cookie", "csrf", "authorization", "request_body",
    "email", "mobile", "primary_email", "first_name", "last_name", "gender", "age_range",
    "provider_payload", "secret",
})
_MAX_AUDIT_DEPTH = 4
_MAX_AUDIT_TEXT = 4000


def _validate_safe_payload(value, *, depth=0):
    if depth > _MAX_AUDIT_DEPTH:
        raise ValueError("Audit payload nesting is too deep.")
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).strip().lower()
            if normalized in _FORBIDDEN_METADATA_KEYS or any(part in normalized for part in ("password", "token", "secret", "cookie")):
                raise ValueError(f"Forbidden audit payload key: {key}")
            _validate_safe_payload(child, depth=depth + 1)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _validate_safe_payload(child, depth=depth + 1)
    elif isinstance(value, str) and len(value) > _MAX_AUDIT_TEXT:
        raise ValueError("Audit payload text is too long.")


@dataclass(frozen=True)
class TrustedAuditProvenance:
    source: str
    provenance_id: str
    request_id: str | None = None
    ip_address: str | None = None
    trusted: bool = True


_provenance_context = ContextVar("audit_provenance", default=None)


def set_audit_provenance(provenance):
    return _provenance_context.set(provenance)


def reset_audit_provenance(token):
    _provenance_context.reset(token)


def get_audit_provenance():
    return _provenance_context.get()


def build_http_provenance(request):
    return TrustedAuditProvenance(
        source=AuditEvent.ProvenanceSource.HTTP_REQUEST,
        provenance_id=uuid.uuid4().hex,
        request_id=uuid.uuid4().hex,
        ip_address=None,
    )


def _resolve_person_subject(*, subject_person=None, entity_type=None, entity_id=None, metadata=None):
    person_id = getattr(subject_person, "pk", subject_person)
    if person_id is None and isinstance(metadata, dict):
        person_id = metadata.get("person_id")
    if person_id is None and entity_type == "Person":
        person_id = entity_id
    if person_id is None:
        return None

    try:
        return int(person_id)
    except (TypeError, ValueError):
        return None


def record_audit_event(
    *,
    action: str,
    entity_type: str,
    actor_user=None,
    entity_id=None,
    changes=None,
    metadata=None,
    request_id=None,
    ip_address=None,
    audit_domain=AuditEvent.Domain.OPERATIONAL,
    subject_person=None,
    provenance=None,
    retention_class=None,
    retention_policy_version=None,
) -> AuditEvent:
    normalized_entity_type = (entity_type or "").strip()
    if not normalized_entity_type:
        raise ValueError("entity_type is required.")

    if action not in AuditEvent.Action.values:
        raise ValueError(f"Unsupported audit action: {action}")
    if audit_domain not in AuditEvent.Domain.values:
        raise ValueError(f"Unsupported audit domain: {audit_domain}")
    normalized_changes = {} if changes is None else changes
    normalized_metadata = {} if metadata is None else metadata
    if audit_domain == AuditEvent.Domain.PRIVACY:
        _validate_safe_payload(normalized_changes)
        _validate_safe_payload(normalized_metadata)

    resolved_provenance = provenance or get_audit_provenance()
    if resolved_provenance is None:
        resolved_provenance = TrustedAuditProvenance(
            source=AuditEvent.ProvenanceSource.SYSTEM,
            provenance_id=uuid.uuid4().hex,
        )
    if resolved_provenance.source not in AuditEvent.ProvenanceSource.values:
        raise ValueError(f"Unsupported provenance source: {resolved_provenance.source}")

    subject_person_id = _resolve_person_subject(
        subject_person=subject_person,
        entity_type=normalized_entity_type,
        entity_id=entity_id,
        metadata=metadata,
    )
    trusted_request_id = resolved_provenance.request_id if resolved_provenance.source == AuditEvent.ProvenanceSource.HTTP_REQUEST else request_id
    trusted_ip = resolved_provenance.ip_address if resolved_provenance.source == AuditEvent.ProvenanceSource.HTTP_REQUEST else ip_address
    with transaction.atomic():
        event = AuditEvent(
            actor_user=actor_user,
            action=action,
            entity_type=normalized_entity_type,
            entity_id=None if entity_id is None else str(entity_id),
            changes=normalized_changes,
            metadata=normalized_metadata,
            request_id=trusted_request_id,
            ip_address=trusted_ip,
            audit_domain=audit_domain,
            contract_version=AuditEvent.CURRENT_CONTRACT_VERSION,
            provenance_source=resolved_provenance.source,
            provenance_id=resolved_provenance.provenance_id,
            provenance_trusted=bool(resolved_provenance.trusted),
            retention_class=retention_class,
            retention_policy_version=retention_policy_version,
        )
        event.save()
        if subject_person_id is not None:
            AuditEventSubject.objects.get_or_create(
                event=event,
                subject_type=AuditEventSubject.SubjectType.PERSON,
                person_id=subject_person_id,
            )
    return event
