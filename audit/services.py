from audit.models import AuditEvent


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
) -> AuditEvent:
    normalized_entity_type = (entity_type or "").strip()
    if not normalized_entity_type:
        raise ValueError("entity_type is required.")

    if action not in AuditEvent.Action.values:
        raise ValueError(f"Unsupported audit action: {action}")

    event = AuditEvent(
        actor_user=actor_user,
        action=action,
        entity_type=normalized_entity_type,
        entity_id=None if entity_id is None else str(entity_id),
        changes={} if changes is None else changes,
        metadata={} if metadata is None else metadata,
        request_id=request_id or None,
        ip_address=ip_address,
    )
    event.save()
    return event

