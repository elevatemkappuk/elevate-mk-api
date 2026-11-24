"""Transactional capability-governance services.

This module is the approved service boundary, not universal ORM enforcement.
Direct ORM writes and management scripts remain privileged operational bypass
risks until every writer coordinates through these services.

Lock order for governance participants and connected Staff lifecycle writers
is: policy state (when applicable) -> canonical StaffRole rows ->
StaffRoleAssignment rows -> User rows -> capability rows. When resolving an
existing proposal, that proposal row is locked immediately after policy state
and before the shared participant locks. Callers must acquire rows in
primary-key order within each relation and must revalidate authority and target
state after all required locks are held.
"""

from datetime import timedelta
from types import SimpleNamespace

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from accounts.models import User
from audit.models import AuditEvent
from audit.services import record_audit_event
from staff_access.models import (
    CapabilityDefinition,
    CapabilityPolicyState,
    CapabilityProposal,
    CapabilityProposalDecision,
    RoleCapabilityDefault,
    StaffCapabilityAssignment,
    StaffCapabilityRestriction,
    StaffRole,
    StaffRoleAssignment,
)


POLICY_STATE_KEY = "capability-governance"
PROPOSAL_EXPIRY = timedelta(days=30)


def _reason(reason):
    value = str(reason or "").strip()
    if not value:
        raise ValidationError({"reason": "A reason is required."})
    if len(value) > 4000:
        raise ValidationError({"reason": "The reason must be 4000 characters or fewer."})
    return value


def _request_context(request):
    if request is None:
        return None, None
    return (
        request.headers.get("X-Request-ID") or request.META.get("HTTP_X_REQUEST_ID"),
        request.META.get("REMOTE_ADDR"),
    )


def _policy_state():
    try:
        return CapabilityPolicyState.objects.select_for_update().get(
            singleton_key=1,
            key=POLICY_STATE_KEY,
        )
    except CapabilityPolicyState.DoesNotExist as exc:
        raise RuntimeError("The capability governance baseline is not installed.") from exc


def _lock_users(*user_ids):
    ids = sorted({int(user_id) for user_id in user_ids if user_id is not None})
    if not ids:
        return {}
    users = User.objects.select_for_update().select_related("person").filter(pk__in=ids).order_by("pk")
    return {user.pk: user for user in users}


def _lock_authorization_rows(user_ids):
    ids = sorted({int(user_id) for user_id in user_ids if user_id is not None})
    roles = list(
        StaffRole.objects.select_for_update()
        .filter(code__in=StaffRole.canonical_codes())
        .order_by("pk")
    )
    assignments = list(
        StaffRoleAssignment.objects.select_for_update()
        .select_related("role")
        .filter(user_id__in=ids)
        .order_by("user_id", "pk")
    )
    return roles, assignments


def _lock_governance_participants(*user_ids):
    """Acquire shared Staff authorization locks before User locks."""
    ids = sorted({int(user_id) for user_id in user_ids if user_id is not None})
    roles, assignments = _lock_authorization_rows(ids)
    users = _lock_users(*ids)
    return users, roles, assignments


def _lock_capabilities(*capability_ids):
    ids = sorted({int(value) for value in capability_ids if value is not None})
    return list(
        CapabilityDefinition.objects.select_for_update()
        .filter(pk__in=ids)
        .order_by("pk")
    )


def _fresh_actor(users, actor_user):
    actor = users.get(actor_user.pk)
    if actor is None:
        raise ValidationError("The acting user no longer exists.")
    return actor


def _active_role_codes(user_id, assignments):
    return {
        assignment.role.code
        for assignment in assignments
        if assignment.user_id == user_id
        and assignment.is_active
        and assignment.revoked_at is None
        and assignment.role.is_active
        and assignment.role.code in StaffRole.canonical_codes()
    }


def _is_privacy_admin(actor, assignments):
    """Check authority without invoking the evaluator's superuser bypass."""
    if not actor.is_active or actor.is_superuser or actor.person.record_type != "STAFF":
        return False
    role_codes = _active_role_codes(actor.pk, assignments)
    if not role_codes:
        return False
    admin_capability = CapabilityDefinition.objects.select_for_update().get(
        code=CapabilityDefinition.STAFF_CAPABILITY_ADMIN,
        is_active=True,
    )
    role_default = RoleCapabilityDefault.objects.select_for_update().filter(
        role__code__in=role_codes,
        role__is_active=True,
        capability=admin_capability,
        capability__is_active=True,
        is_enabled=True,
    ).exists()
    grant = StaffCapabilityAssignment.objects.select_for_update().filter(
        user=actor,
        capability=admin_capability,
        state=StaffCapabilityAssignment.State.ACTIVE,
    ).exists()
    restricted = StaffCapabilityRestriction.objects.select_for_update().filter(
        user=actor,
        capability=admin_capability,
        is_active=True,
        removed_at__isnull=True,
    ).exists()
    return (role_default or grant) and not restricted


def _require_privacy_admin(actor, assignments):
    if not _is_privacy_admin(actor, assignments):
        raise PermissionError("An active unrestricted STAFF_CAPABILITY_ADMIN is required.")


def _validate_target_user(actor, target_user, assignments):
    if target_user.pk == actor.pk:
        raise ValidationError("Capability governance cannot target the acting user.")
    if not target_user.is_active or target_user.person.record_type != "STAFF":
        raise ValidationError("Capability targets must be active STAFF users.")
    if not _active_role_codes(target_user.pk, assignments):
        raise ValidationError("Capability targets require an active canonical CRM role.")


def _get_capability(capability):
    capability_id = capability.pk if isinstance(capability, CapabilityDefinition) else capability
    return CapabilityDefinition.objects.get(pk=capability_id, is_active=True)


def _get_role(role):
    role_id = role.pk if isinstance(role, StaffRole) else role
    role = StaffRole.objects.select_for_update().get(pk=role_id, is_active=True)
    if role.code not in StaffRole.canonical_codes():
        raise ValidationError("Only canonical CRM roles may have capability defaults.")
    return role


def _get_role_for_proposal(role):
    """Lock a proposal role even when lifecycle changes made it inactive."""
    role_id = role.pk if isinstance(role, StaffRole) else role
    role = StaffRole.objects.select_for_update().get(pk=role_id)
    if role.code not in StaffRole.canonical_codes():
        raise ValidationError("Only canonical CRM roles may have capability defaults.")
    return role


def _iso(value):
    return None if value is None else value.isoformat()


def _audit_cursor(entity_type, entity_id):
    if entity_id is None:
        return None
    return (
        AuditEvent.objects.filter(entity_type=entity_type, entity_id=str(entity_id))
        .order_by("-id")
        .values_list("id", flat=True)
        .first()
    )


def _target_snapshot(*, operation, role=None, target_user=None, capability=None):
    if operation in CapabilityProposal.ROLE_DEFAULT_OPERATIONS:
        default = RoleCapabilityDefault.objects.select_for_update().filter(
            role=role,
            capability=capability,
        ).first()
        return {
            "captured_at": timezone.now().isoformat(),
            "target_kind": "role_default",
            "role_id": role.pk,
            "capability_id": capability.pk,
            "role_active": role.is_active,
            "role_default": {
                "id": None if default is None else default.pk,
                "enabled": None if default is None else default.is_enabled,
                "updated_at": None if default is None else _iso(default.updated_at),
                "audit_cursor": None if default is None else _audit_cursor("RoleCapabilityDefault", default.pk),
            },
        }

    assignment = StaffCapabilityAssignment.objects.select_for_update().filter(
        user=target_user,
        capability=capability,
        state__in=StaffCapabilityAssignment.current_states(),
    ).first()
    restriction = StaffCapabilityRestriction.objects.select_for_update().filter(
        user=target_user,
        capability=capability,
        is_active=True,
        removed_at__isnull=True,
    ).first()
    role_assignments = list(
        StaffRoleAssignment.objects.select_for_update()
        .select_related("role")
        .filter(user=target_user)
        .order_by("pk")
    )
    return {
        "captured_at": timezone.now().isoformat(),
        "target_kind": "individual",
        "target_user_id": target_user.pk,
        "target_user_is_active": target_user.is_active,
        "target_person_record_type": target_user.person.record_type,
        "target_active_role_codes": sorted(_active_role_codes(target_user.pk, role_assignments)),
        "role_assignments": [
            {
                "id": item.pk,
                "role_id": item.role_id,
                "is_active": item.is_active,
                "revoked_at": _iso(item.revoked_at),
                "role_is_active": item.role.is_active,
                "updated_at": _iso(item.updated_at),
            }
            for item in role_assignments
        ],
        "assignment": {
            "id": None if assignment is None else assignment.pk,
            "state": None if assignment is None else assignment.state,
            "updated_at": None if assignment is None else _iso(assignment.updated_at),
            "audit_cursor": None if assignment is None else _audit_cursor("StaffCapabilityAssignment", assignment.pk),
        },
        "restriction": {
            "id": None if restriction is None else restriction.pk,
            "active": restriction is not None,
            "updated_at": None if restriction is None else _iso(restriction.updated_at),
            "audit_cursor": None if restriction is None else _audit_cursor("StaffCapabilityRestriction", restriction.pk),
        },
    }


def _store_snapshot(proposal, snapshot):
    proposal.expected_target_state = snapshot
    proposal.save(update_fields=["expected_target_state"])


def _snapshots_match(expected, current):
    expected = dict(expected or {})
    current = dict(current or {})
    expected.pop("captured_at", None)
    current.pop("captured_at", None)
    return expected == current


def _proposal_metadata(proposal, *, approver_id=None, executor_id=None):
    return {
        "proposal_id": str(proposal.public_id),
        "operation": proposal.operation,
        "policy_version": proposal.policy_version,
        "proposer_user_id": str(proposal.proposed_by_id),
        "approver_user_id": None if approver_id is None else str(approver_id),
        "executor_user_id": None if executor_id is None else str(executor_id),
        "target_user_id": None if proposal.target_user_id is None else str(proposal.target_user_id),
        "target_role_id": None if proposal.role_id is None else str(proposal.role_id),
        "capability_id": str(proposal.capability_id),
        "controlled_recovery": proposal.controlled_recovery,
    }


def _audit_proposal(*, action, proposal, actor, reason, request=None, changes=None, approver_id=None, executor_id=None):
    request_id, ip_address = _request_context(request)
    record_audit_event(
        action=action,
        actor_user=actor,
        entity_type="CapabilityProposal",
        entity_id=proposal.public_id,
        changes={} if changes is None else changes,
        metadata={
            **_proposal_metadata(
                proposal,
                approver_id=approver_id,
                executor_id=executor_id,
            ),
            "reason": reason,
        },
        request_id=request_id,
        ip_address=ip_address,
    )


def _audit_mutation(*, proposal, actor, reason, changes, request=None, phase="BEFORE", approver_id=None, action=None):
    request_id, ip_address = _request_context(request)
    entity_type = changes.get("entity", "CapabilityProposal")
    entity_id = changes.get("entity_id") or str(proposal.public_id)
    record_audit_event(
        action=action or AuditEvent.Action.CAPABILITY_PERMISSION_MUTATED,
        actor_user=actor,
        entity_type=entity_type,
        entity_id=entity_id,
        changes=changes,
        metadata={
            **_proposal_metadata(proposal, approver_id=approver_id, executor_id=actor.pk),
            "reason": reason,
            "mutation_phase": phase,
        },
        request_id=request_id,
        ip_address=ip_address,
    )


def _require_active_superuser(actor):
    if not actor.is_active or not actor.is_superuser:
        raise PermissionError("An active Django superuser is required.")


def _direct_mutation_changes(*, operation, snapshot, role_id=None, capability_id=None, target_user_id=None):
    if operation in CapabilityProposal.ROLE_DEFAULT_OPERATIONS:
        before = snapshot["role_default"]["enabled"]
        after = operation == CapabilityProposal.Operation.ROLE_DEFAULT_ENABLE
        return {
            "entity": "RoleCapabilityDefault",
            "entity_id": snapshot["role_default"]["id"] or f"role:{role_id}:capability:{capability_id}",
            "before": before,
            "after": after,
        }
    if operation in {
        CapabilityProposal.Operation.INDIVIDUAL_GRANT,
        CapabilityProposal.Operation.INDIVIDUAL_RESTORE,
        CapabilityProposal.Operation.INDIVIDUAL_SUSPEND,
        CapabilityProposal.Operation.INDIVIDUAL_REVOKE,
    }:
        before = snapshot["assignment"]["state"]
        after = {
            CapabilityProposal.Operation.INDIVIDUAL_GRANT: StaffCapabilityAssignment.State.ACTIVE,
            CapabilityProposal.Operation.INDIVIDUAL_RESTORE: StaffCapabilityAssignment.State.ACTIVE,
            CapabilityProposal.Operation.INDIVIDUAL_SUSPEND: StaffCapabilityAssignment.State.SUSPENDED,
            CapabilityProposal.Operation.INDIVIDUAL_REVOKE: StaffCapabilityAssignment.State.REVOKED,
        }[operation]
        return {
            "entity": "StaffCapabilityAssignment",
            "entity_id": snapshot["assignment"]["id"] or f"user:{target_user_id}:capability:{capability_id}",
            "before": before,
            "after": after,
        }
    before = snapshot["restriction"]["active"]
    after = operation == CapabilityProposal.Operation.INDIVIDUAL_RESTRICT
    return {
        "entity": "StaffCapabilityRestriction",
        "entity_id": snapshot["restriction"]["id"] or f"user:{target_user_id}:capability:{capability_id}",
        "before": before,
        "after": after,
    }


def _audit_direct_superuser_mutation(
    *,
    actor,
    operation,
    reason,
    changes,
    policy_version,
    capability_id,
    target_user_id=None,
    target_role_id=None,
    request=None,
    phase="BEFORE",
):
    request_id, ip_address = _request_context(request)
    record_audit_event(
        action=AuditEvent.Action.CAPABILITY_PERMISSION_MUTATED,
        actor_user=actor,
        entity_type=changes["entity"],
        entity_id=changes["entity_id"],
        changes=changes,
        metadata={
            "operation": operation,
            "reason": reason,
            "mutation_phase": phase,
            "actor_type": "DJANGO_SUPERUSER",
            "superuser_authority": True,
            "policy_version": policy_version,
            "capability_id": str(capability_id),
            "target_user_id": None if target_user_id is None else str(target_user_id),
            "target_role_id": None if target_role_id is None else str(target_role_id),
        },
        request_id=request_id,
        ip_address=ip_address,
    )


def _bump_policy(state, *, actor, reason):
    state.version += 1
    state.updated_by = actor
    state.reason = reason
    state.save(update_fields=["version", "updated_by", "reason", "updated_at"])


def _requires_two_person(proposal):
    return proposal.operation in CapabilityProposal.ROLE_DEFAULT_OPERATIONS or (
        proposal.capability.code == CapabilityDefinition.STAFF_CAPABILITY_ADMIN
        and proposal.operation in {
            CapabilityProposal.Operation.INDIVIDUAL_GRANT,
            CapabilityProposal.Operation.INDIVIDUAL_RESTORE,
            CapabilityProposal.Operation.INDIVIDUAL_UNRESTRICT,
        }
    )


def _grant_is_restricted(snapshot):
    return snapshot.get("restriction", {}).get("active", False)


def _planned_changes(proposal):
    snapshot = proposal.expected_target_state
    if proposal.operation in CapabilityProposal.ROLE_DEFAULT_OPERATIONS:
        before = snapshot["role_default"]["enabled"]
        after = proposal.operation == CapabilityProposal.Operation.ROLE_DEFAULT_ENABLE
        return {
            "from": before,
            "to": after,
            "entity": "RoleCapabilityDefault",
            "entity_id": snapshot["role_default"]["id"] or f"role:{proposal.role_id}:capability:{proposal.capability_id}",
        }
    before = snapshot["assignment"]["state"]
    if proposal.operation in {
        CapabilityProposal.Operation.INDIVIDUAL_GRANT,
        CapabilityProposal.Operation.INDIVIDUAL_RESTORE,
        CapabilityProposal.Operation.INDIVIDUAL_SUSPEND,
        CapabilityProposal.Operation.INDIVIDUAL_REVOKE,
    }:
        after = {
            CapabilityProposal.Operation.INDIVIDUAL_GRANT: StaffCapabilityAssignment.State.ACTIVE,
            CapabilityProposal.Operation.INDIVIDUAL_RESTORE: StaffCapabilityAssignment.State.ACTIVE,
            CapabilityProposal.Operation.INDIVIDUAL_SUSPEND: StaffCapabilityAssignment.State.SUSPENDED,
            CapabilityProposal.Operation.INDIVIDUAL_REVOKE: StaffCapabilityAssignment.State.REVOKED,
        }[proposal.operation]
        return {
            "from": before,
            "to": after,
            "entity": "StaffCapabilityAssignment",
            "entity_id": snapshot["assignment"]["id"] or f"user:{proposal.target_user_id}:capability:{proposal.capability_id}",
        }
    before = snapshot["restriction"]["active"]
    after = proposal.operation == CapabilityProposal.Operation.INDIVIDUAL_RESTRICT
    return {
        "from": before,
        "to": after,
        "entity": "StaffCapabilityRestriction",
        "entity_id": snapshot["restriction"]["id"] or f"user:{proposal.target_user_id}:capability:{proposal.capability_id}",
    }


def _expire_same_key_pending_proposal(*, operation, role, target_user, capability, actor, request=None):
    now = timezone.now()
    pending = CapabilityProposal.objects.select_for_update().filter(
        status=CapabilityProposal.Status.PENDING,
        expires_at__lte=now,
        capability=capability,
    )
    if operation in CapabilityProposal.ROLE_DEFAULT_OPERATIONS:
        pending = pending.filter(
            role=role,
            operation__in=CapabilityProposal.ROLE_DEFAULT_OPERATIONS,
        )
    else:
        pending = pending.filter(
            target_user=target_user,
            operation__in=CapabilityProposal.INDIVIDUAL_OPERATIONS,
        )
    expired = pending.order_by("pk").first()
    if expired is not None:
        _finalize_without_mutation(
            proposal=expired,
            actor=actor,
            outcome=CapabilityProposalDecision.Outcome.EXPIRED,
            reason="The proposal expired before a replacement was created.",
            request=request,
        )


def create_capability_proposal(*, actor_user, operation, capability, reason, role=None, target_user=None, request=None):
    reason = _reason(reason)
    operation = str(operation)
    if operation not in CapabilityProposal.Operation.values:
        raise ValidationError({"operation": "Unknown capability governance operation."})
    with transaction.atomic():
        state = _policy_state()
        users, _, auth_rows = _lock_governance_participants(
            actor_user.pk, None if target_user is None else target_user.pk
        )
        actor = _fresh_actor(users, actor_user)
        requested_capability_id = capability.pk if isinstance(capability, CapabilityDefinition) else capability
        _lock_capabilities(requested_capability_id)
        _require_privacy_admin(actor, auth_rows)
        fresh_target = None if target_user is None else users.get(target_user.pk)
        if operation in CapabilityProposal.INDIVIDUAL_OPERATIONS:
            if fresh_target is None:
                raise ValidationError("An individual proposal requires a target user.")
            _validate_target_user(actor, fresh_target, auth_rows)
        elif role is None or target_user is not None:
            raise ValidationError("A role-default proposal requires exactly one target role.")
        fresh_capability = _get_capability(capability)
        if operation == CapabilityProposal.Operation.INDIVIDUAL_SUSPEND and fresh_capability.code != CapabilityDefinition.STAFF_CAPABILITY_ADMIN:
            raise ValidationError("Only STAFF_CAPABILITY_ADMIN may use the direct suspend operation.")
        fresh_role = _get_role(role) if operation in CapabilityProposal.ROLE_DEFAULT_OPERATIONS else None
        _expire_same_key_pending_proposal(
            operation=operation,
            role=fresh_role,
            target_user=fresh_target,
            capability=fresh_capability,
            actor=actor,
            request=request,
        )
        snapshot = _target_snapshot(
            operation=operation,
            role=fresh_role,
            target_user=fresh_target,
            capability=fresh_capability,
        )
        if operation in {
            CapabilityProposal.Operation.INDIVIDUAL_GRANT,
            CapabilityProposal.Operation.INDIVIDUAL_RESTORE,
        } and _grant_is_restricted(snapshot):
            raise ValidationError("A capability cannot be granted or restored while restricted.")
        now = timezone.now()
        proposal = CapabilityProposal.objects.create(
            operation=operation,
            role=fresh_role,
            target_user=fresh_target,
            capability=fresh_capability,
            proposed_by=actor,
            reason=reason,
            policy_version=state.version,
            expires_at=now + PROPOSAL_EXPIRY,
            expected_target_state=snapshot,
        )
        _audit_proposal(
            action=AuditEvent.Action.CAPABILITY_PROPOSAL_CREATED,
            proposal=proposal,
            actor=actor,
            reason=reason,
            request=request,
            executor_id=None,
        )
        return proposal


def _apply_mutation(*, proposal, actor):
    operation = proposal.operation
    if operation in CapabilityProposal.ROLE_DEFAULT_OPERATIONS:
        desired = operation == CapabilityProposal.Operation.ROLE_DEFAULT_ENABLE
        default = RoleCapabilityDefault.objects.select_for_update().filter(
            role=proposal.role,
            capability=proposal.capability,
        ).first()
        before = None if default is None else default.is_enabled
        if before == desired:
            raise ValidationError("The role default already has the requested state.")
        if default is None:
            RoleCapabilityDefault.objects.create(role=proposal.role, capability=proposal.capability, is_enabled=desired)
        else:
            default.is_enabled = desired
            default.save(update_fields=["is_enabled", "updated_at"])
        return

    assignment = StaffCapabilityAssignment.objects.select_for_update().filter(
        user=proposal.target_user,
        capability=proposal.capability,
        state__in=StaffCapabilityAssignment.current_states(),
    ).first()
    restriction = StaffCapabilityRestriction.objects.select_for_update().filter(
        user=proposal.target_user,
        capability=proposal.capability,
        is_active=True,
        removed_at__isnull=True,
    ).first()
    if operation == CapabilityProposal.Operation.INDIVIDUAL_GRANT:
        if assignment is not None:
            raise ValidationError("The target already has a current individual assignment.")
        StaffCapabilityAssignment.objects.create(
            user=proposal.target_user,
            capability=proposal.capability,
            state=StaffCapabilityAssignment.State.ACTIVE,
            granted_by=actor,
        )
        return
    if operation == CapabilityProposal.Operation.INDIVIDUAL_RESTORE:
        if assignment is None or assignment.state != StaffCapabilityAssignment.State.SUSPENDED:
            raise ValidationError("Only a suspended current assignment can be restored.")
        assignment.state = StaffCapabilityAssignment.State.ACTIVE
        assignment.suspended_at = None
        assignment.suspended_by = None
        assignment.save(update_fields=["state", "suspended_at", "suspended_by", "updated_at"])
        return
    if operation == CapabilityProposal.Operation.INDIVIDUAL_SUSPEND:
        if assignment is None or assignment.state != StaffCapabilityAssignment.State.ACTIVE:
            raise ValidationError("Only an active assignment can be suspended.")
        assignment.state = StaffCapabilityAssignment.State.SUSPENDED
        assignment.suspended_at = timezone.now()
        assignment.suspended_by = actor
        assignment.save(update_fields=["state", "suspended_at", "suspended_by", "updated_at"])
        return
    if operation == CapabilityProposal.Operation.INDIVIDUAL_REVOKE:
        if assignment is None:
            raise ValidationError("The target has no current individual assignment.")
        assignment.state = StaffCapabilityAssignment.State.REVOKED
        assignment.revoked_at = timezone.now()
        assignment.revoked_by = actor
        assignment.save(update_fields=["state", "revoked_at", "revoked_by", "updated_at"])
        return
    if operation == CapabilityProposal.Operation.INDIVIDUAL_RESTRICT:
        if restriction is not None:
            raise ValidationError("The target capability is already restricted.")
        StaffCapabilityRestriction.objects.create(
            user=proposal.target_user,
            capability=proposal.capability,
            restricted_by=actor,
        )
        return
    if operation == CapabilityProposal.Operation.INDIVIDUAL_UNRESTRICT:
        if restriction is None:
            raise ValidationError("The target capability has no active restriction.")
        restriction.is_active = False
        restriction.removed_at = timezone.now()
        restriction.removed_by = actor
        restriction.save(update_fields=["is_active", "removed_at", "removed_by", "updated_at"])
        return
    raise ValidationError("Unknown capability governance operation.")


def apply_superuser_capability_change(
    *,
    actor_user,
    capability,
    operation,
    reason,
    role=None,
    target_user=None,
    request=None,
):
    """Apply one directly audited Django-superuser governance operation.

    This is the sole direct-operation path for the approved superuser
    exception. Ordinary administrators continue through proposals and their
    existing eligibility, restriction, and separation-of-duty rules.
    """
    reason = _reason(reason)
    operation = str(operation)
    if operation not in CapabilityProposal.Operation.values:
        raise ValidationError({"operation": "Unknown capability governance operation."})
    is_role_operation = operation in CapabilityProposal.ROLE_DEFAULT_OPERATIONS
    if is_role_operation and (role is None or target_user is not None):
        raise ValidationError("A role-default operation requires exactly one target role.")
    if not is_role_operation and (target_user is None or role is not None):
        raise ValidationError("An individual operation requires exactly one target user.")

    with transaction.atomic():
        state = _policy_state()
        users, _, _auth_rows = _lock_governance_participants(
            actor_user.pk,
            None if target_user is None else target_user.pk,
        )
        actor = _fresh_actor(users, actor_user)
        _require_active_superuser(actor)
        fresh_capability = _get_capability(capability)
        _lock_capabilities(fresh_capability.pk)
        fresh_role = _get_role_for_proposal(role) if is_role_operation else None
        fresh_target = None if target_user is None else users.get(target_user.pk)
        if target_user is not None and fresh_target is None:
            raise ValidationError("The target user no longer exists.")
        before = _target_snapshot(
            operation=operation,
            role=fresh_role,
            target_user=fresh_target,
            capability=fresh_capability,
        )
        changes = _direct_mutation_changes(
            operation=operation,
            snapshot=before,
            role_id=None if fresh_role is None else fresh_role.pk,
            capability_id=fresh_capability.pk,
            target_user_id=None if fresh_target is None else fresh_target.pk,
        )
        changes["before_state"] = before
        _audit_direct_superuser_mutation(
            actor=actor,
            operation=operation,
            reason=reason,
            changes=changes,
            policy_version=state.version,
            capability_id=fresh_capability.pk,
            target_user_id=None if fresh_target is None else fresh_target.pk,
            target_role_id=None if fresh_role is None else fresh_role.pk,
            request=request,
            phase="BEFORE",
        )
        direct_mutation = SimpleNamespace(
            operation=operation,
            role=fresh_role,
            target_user=fresh_target,
            capability=fresh_capability,
        )
        _apply_mutation(proposal=direct_mutation, actor=actor)
        after = _target_snapshot(
            operation=operation,
            role=fresh_role,
            target_user=fresh_target,
            capability=fresh_capability,
        )
        after_changes = dict(changes)
        after_changes["entity_id"] = _direct_mutation_changes(
            operation=operation,
            snapshot=after,
            role_id=None if fresh_role is None else fresh_role.pk,
            capability_id=fresh_capability.pk,
            target_user_id=None if fresh_target is None else fresh_target.pk,
        )["entity_id"]
        after_changes["after_state"] = after
        _audit_direct_superuser_mutation(
            actor=actor,
            operation=operation,
            reason=reason,
            changes=after_changes,
            policy_version=state.version,
            capability_id=fresh_capability.pk,
            target_user_id=None if fresh_target is None else fresh_target.pk,
            target_role_id=None if fresh_role is None else fresh_role.pk,
            request=request,
            phase="AFTER",
        )
        _bump_policy(state, actor=actor, reason=reason)
        return after


def _finalize_without_mutation(*, proposal, actor, outcome, reason, request=None):
    action = {
        CapabilityProposalDecision.Outcome.REJECTED: AuditEvent.Action.CAPABILITY_PROPOSAL_REJECTED,
        CapabilityProposalDecision.Outcome.CANCELLED: AuditEvent.Action.CAPABILITY_PROPOSAL_CANCELLED,
        CapabilityProposalDecision.Outcome.EXPIRED: AuditEvent.Action.CAPABILITY_PROPOSAL_EXPIRED,
        CapabilityProposalDecision.Outcome.INVALIDATED: AuditEvent.Action.CAPABILITY_PROPOSAL_INVALIDATED,
    }[outcome]
    _audit_proposal(action=action, proposal=proposal, actor=actor, reason=reason, request=request)
    # policy_version consistently means the singleton version at resolution;
    # callers already hold its FOR UPDATE lock.
    resolved_policy_version = CapabilityPolicyState.objects.get(
        singleton_key=1,
        key=POLICY_STATE_KEY,
    ).version
    CapabilityProposalDecision.objects.create(
        proposal=proposal,
        outcome=outcome,
        decided_by=actor,
        reason=reason,
        policy_version=resolved_policy_version,
    )
    proposal.status = outcome
    proposal.resolved_at = timezone.now()
    proposal.resolution_reason = reason
    proposal.save(update_fields=["status", "resolved_at", "resolution_reason"])
    return proposal


def _execute_pending(*, proposal_id, actor_user, request=None, approval=False, controlled_recovery=False):
    with transaction.atomic():
        state = _policy_state()
        # Do not combine FOR UPDATE with select_related() across nullable
        # proposal targets; PostgreSQL rejects locking the nullable side of
        # those outer joins. Related authorization rows are locked explicitly
        # below in the shared lock order.
        proposal = CapabilityProposal.objects.select_for_update().get(pk=proposal_id)
        if proposal.status != CapabilityProposal.Status.PENDING:
            raise ValidationError("Only a pending proposal can be resolved.")
        users, _, auth_rows = _lock_governance_participants(
            proposal.proposed_by_id, actor_user.pk, proposal.target_user_id
        )
        actor = _fresh_actor(users, actor_user)
        proposer = users.get(proposal.proposed_by_id)
        target = users.get(proposal.target_user_id)
        _lock_capabilities(proposal.capability_id)
        if proposal.expires_at <= timezone.now():
            _require_privacy_admin(actor, auth_rows)
            return _finalize_without_mutation(
                proposal=proposal,
                actor=actor,
                outcome=CapabilityProposalDecision.Outcome.EXPIRED,
                reason="The proposal expired before execution.",
                request=request,
            )
        if proposer is None:
            raise ValidationError("The proposer no longer exists.")
        proposer_authorized = _is_privacy_admin(proposer, auth_rows)
        if not proposer_authorized:
            if actor.pk != proposer.pk and _is_privacy_admin(actor, auth_rows):
                return _finalize_without_mutation(
                    proposal=proposal,
                    actor=actor,
                    outcome=CapabilityProposalDecision.Outcome.INVALIDATED,
                    reason="The proposer is no longer authorized.",
                    request=request,
                )
            raise PermissionError("The proposer is no longer authorized; another administrator must invalidate it.")
        if target is not None:
            try:
                _validate_target_user(actor, target, auth_rows)
            except (ValidationError, PermissionError):
                if _is_privacy_admin(actor, auth_rows):
                    return _finalize_without_mutation(
                        proposal=proposal,
                        actor=actor,
                        outcome=CapabilityProposalDecision.Outcome.INVALIDATED,
                        reason="The proposal target is no longer eligible.",
                        request=request,
                    )
                raise
        if approval:
            if not _requires_two_person(proposal):
                raise ValidationError("This proposal uses the ordinary single-admin path.")
            if actor.pk == proposer.pk:
                raise PermissionError("A proposal cannot be approved by its proposer.")
            if actor.is_superuser:
                raise PermissionError(
                    "Django superusers must use the audited direct-operation path; "
                    "pending proposals remain subject to their existing workflow."
                )
            _require_privacy_admin(actor, auth_rows)
        else:
            _require_privacy_admin(actor, auth_rows)
            if _requires_two_person(proposal):
                raise PermissionError("This proposal requires a distinct second approver.")

        if proposal.policy_version != state.version:
            return _finalize_without_mutation(
                proposal=proposal,
                actor=actor,
                outcome=CapabilityProposalDecision.Outcome.INVALIDATED,
                reason="The proposal policy version is stale.",
                request=request,
            )
        capability = _get_capability(proposal.capability)
        fresh_role = None if proposal.role_id is None else _get_role_for_proposal(proposal.role)
        fresh_snapshot = _target_snapshot(
            operation=proposal.operation,
            role=fresh_role,
            target_user=target,
            capability=capability,
        )
        if proposal.operation in CapabilityProposal.ROLE_DEFAULT_OPERATIONS and not fresh_role.is_active:
            return _finalize_without_mutation(
                proposal=proposal,
                actor=actor,
                outcome=CapabilityProposalDecision.Outcome.INVALIDATED,
                reason="The proposal role is no longer active.",
                request=request,
            )
        if not _snapshots_match(proposal.expected_target_state, fresh_snapshot):
            return _finalize_without_mutation(
                proposal=proposal,
                actor=actor,
                outcome=CapabilityProposalDecision.Outcome.INVALIDATED,
                reason="The proposal target state is stale.",
                request=request,
            )
        if proposal.operation in {
            CapabilityProposal.Operation.INDIVIDUAL_GRANT,
            CapabilityProposal.Operation.INDIVIDUAL_RESTORE,
        } and _grant_is_restricted(fresh_snapshot):
            return _finalize_without_mutation(
                proposal=proposal,
                actor=actor,
                outcome=CapabilityProposalDecision.Outcome.INVALIDATED,
                reason="The target capability is now restricted.",
                request=request,
            )

        changes = _planned_changes(proposal)
        approval_action = AuditEvent.Action.CAPABILITY_PROPOSAL_APPROVED
        if proposal.controlled_recovery:
            approval_action = AuditEvent.Action.CAPABILITY_RECOVERY_APPROVED
        metadata = _proposal_metadata(
            proposal,
            approver_id=actor.pk if approval else None,
            executor_id=actor.pk,
        )
        metadata["approval_mode"] = "TWO_PERSON" if approval else "SINGLE_ADMIN"
        _audit_proposal(
            action=approval_action,
            proposal=proposal,
            actor=actor,
            reason=proposal.reason,
            request=request,
            approver_id=actor.pk if approval else None,
            executor_id=actor.pk,
        )
        mutation_action = AuditEvent.Action.CAPABILITY_PERMISSION_MUTATED
        if proposal.operation == CapabilityProposal.Operation.INDIVIDUAL_SUSPEND:
            mutation_action = AuditEvent.Action.CAPABILITY_EMERGENCY_SUSPENDED
        elif proposal.operation == CapabilityProposal.Operation.INDIVIDUAL_REVOKE:
            mutation_action = AuditEvent.Action.CAPABILITY_EMERGENCY_REVOKED
        elif proposal.operation == CapabilityProposal.Operation.INDIVIDUAL_RESTRICT:
            mutation_action = AuditEvent.Action.CAPABILITY_EMERGENCY_RESTRICTED
        _audit_mutation(
            proposal=proposal,
            actor=actor,
            reason=proposal.reason,
            changes=changes,
            request=request,
            approver_id=actor.pk if approval else None,
            action=mutation_action,
        )
        _apply_mutation(proposal=proposal, actor=actor)
        actual = dict(changes)
        if proposal.operation in CapabilityProposal.ROLE_DEFAULT_OPERATIONS:
            actual["entity_id"] = RoleCapabilityDefault.objects.get(
                role_id=proposal.role_id,
                capability_id=proposal.capability_id,
            ).pk
        elif proposal.operation in CapabilityProposal.INDIVIDUAL_OPERATIONS:
            if proposal.operation in {
                CapabilityProposal.Operation.INDIVIDUAL_RESTRICT,
                CapabilityProposal.Operation.INDIVIDUAL_UNRESTRICT,
            }:
                actual["entity_id"] = StaffCapabilityRestriction.objects.filter(
                    user_id=proposal.target_user_id,
                    capability_id=proposal.capability_id,
                ).order_by("-pk").first().pk
            else:
                actual["entity_id"] = StaffCapabilityAssignment.objects.filter(
                    user_id=proposal.target_user_id,
                    capability_id=proposal.capability_id,
                ).order_by("-pk").first().pk
        _audit_mutation(
            proposal=proposal,
            actor=actor,
            reason=proposal.reason,
            changes=actual,
            request=request,
            phase="AFTER",
            approver_id=actor.pk if approval else None,
            action=mutation_action,
        )
        _bump_policy(state, actor=actor, reason=proposal.reason)
        CapabilityProposalDecision.objects.create(
            proposal=proposal,
            outcome=CapabilityProposalDecision.Outcome.APPROVED,
            decided_by=actor,
            reason=proposal.reason,
            policy_version=state.version,
        )
        proposal.status = CapabilityProposal.Status.APPROVED
        proposal.resolved_at = timezone.now()
        proposal.resolution_reason = proposal.reason
        proposal.save(update_fields=["status", "resolved_at", "resolution_reason"])
        return proposal


def apply_capability_proposal(*, proposal, actor_user, request=None):
    proposal_id = proposal.pk if isinstance(proposal, CapabilityProposal) else proposal
    return _execute_pending(proposal_id=proposal_id, actor_user=actor_user, request=request)


def approve_capability_proposal(*, proposal, actor_user, request=None, controlled_recovery=False):
    """Approve an ordinary proposal; superusers use the direct-operation path."""
    if controlled_recovery:
        raise PermissionError("The superseded controlled-recovery workflow is not supported.")
    proposal_id = proposal.pk if isinstance(proposal, CapabilityProposal) else proposal
    return _execute_pending(proposal_id=proposal_id, actor_user=actor_user, request=request, approval=True)


def apply_individual_capability_change(*, actor_user, target_user, capability, operation, reason, request=None):
    if operation not in CapabilityProposal.INDIVIDUAL_OPERATIONS:
        raise ValidationError("This wrapper accepts individual operations only.")
    with transaction.atomic():
        proposal = create_capability_proposal(
            actor_user=actor_user,
            target_user=target_user,
            capability=capability,
            operation=operation,
            reason=reason,
            request=request,
        )
        return apply_capability_proposal(proposal=proposal, actor_user=actor_user, request=request)


def apply_urgent_staff_capability_admin_action(*, actor_user, target_user, operation, reason, request=None):
    if operation not in {
        CapabilityProposal.Operation.INDIVIDUAL_SUSPEND,
        CapabilityProposal.Operation.INDIVIDUAL_RESTRICT,
        CapabilityProposal.Operation.INDIVIDUAL_REVOKE,
    }:
        raise ValidationError("Only urgent suspend, restrict, or revoke operations are supported.")
    capability = CapabilityDefinition.objects.get(code=CapabilityDefinition.STAFF_CAPABILITY_ADMIN)
    return apply_individual_capability_change(
        actor_user=actor_user,
        target_user=target_user,
        capability=capability,
        operation=operation,
        reason=reason,
        request=request,
    )


def _resolve_proposal(*, proposal, actor_user, outcome, reason, request=None):
    reason = _reason(reason)
    with transaction.atomic():
        _policy_state()
        proposal = CapabilityProposal.objects.select_for_update().get(
            pk=proposal.pk if isinstance(proposal, CapabilityProposal) else proposal
        )
        users, _, auth_rows = _lock_governance_participants(
            proposal.proposed_by_id, actor_user.pk, proposal.target_user_id
        )
        actor = _fresh_actor(users, actor_user)
        _require_privacy_admin(actor, auth_rows)
        if proposal.status != CapabilityProposal.Status.PENDING:
            raise ValidationError("Only a pending proposal can be resolved.")
        if outcome == CapabilityProposalDecision.Outcome.CANCELLED:
            if actor.pk != proposal.proposed_by_id:
                raise PermissionError("Only the proposer may cancel a proposal.")
        elif actor.pk == proposal.proposed_by_id:
            raise PermissionError("The proposer cannot resolve their own proposal.")
        return _finalize_without_mutation(proposal=proposal, actor=actor, outcome=outcome, reason=reason, request=request)


def reject_capability_proposal(*, proposal, actor_user, reason, request=None):
    return _resolve_proposal(
        proposal=proposal,
        actor_user=actor_user,
        outcome=CapabilityProposalDecision.Outcome.REJECTED,
        reason=reason,
        request=request,
    )


def cancel_capability_proposal(*, proposal, actor_user, reason, request=None):
    return _resolve_proposal(
        proposal=proposal,
        actor_user=actor_user,
        outcome=CapabilityProposalDecision.Outcome.CANCELLED,
        reason=reason,
        request=request,
    )


def reconcile_staff_capability_assignments(*, user, actor_user, request=None):
    """Role-transition helper; it is not a proposal operation and requires an attributable actor."""
    if actor_user is None:
        raise PermissionError("Role reconciliation requires an attributable actor.")
    with transaction.atomic():
        state = _policy_state()
        users, _, auth_rows = _lock_governance_participants(user.pk, actor_user.pk)
        target = users.get(user.pk)
        if target is None:
            raise ValidationError("The target user no longer exists.")
        actor = _fresh_actor(users, actor_user)
        if actor.pk == target.pk:
            raise PermissionError("A user cannot self-suspend through role reconciliation.")
        _require_privacy_admin(actor, auth_rows)
        if _active_role_codes(target.pk, auth_rows):
            return []
        assignments = list(
            StaffCapabilityAssignment.objects.select_for_update()
            .filter(user=target, state=StaffCapabilityAssignment.State.ACTIVE)
            .select_related("capability")
            .order_by("pk")
        )
        request_id, ip_address = _request_context(request)
        for assignment in assignments:
            record_audit_event(
                action=AuditEvent.Action.CAPABILITY_ASSIGNMENT_SUSPENDED,
                actor_user=actor,
                entity_type="StaffCapabilityAssignment",
                entity_id=assignment.pk,
                changes={"state": {"from": assignment.state, "to": StaffCapabilityAssignment.State.SUSPENDED}},
                metadata={
                    "target_user_id": str(target.pk),
                    "capability_id": str(assignment.capability_id),
                    "proposer_user_id": None,
                    "approver_user_id": None,
                    "executor_user_id": str(actor.pk),
                    "actor_type": "STAFF_ADMIN",
                    "reason": "staff_role_inactive",
                },
                request_id=request_id,
                ip_address=ip_address,
            )
            assignment.state = StaffCapabilityAssignment.State.SUSPENDED
            assignment.suspended_at = timezone.now()
            assignment.suspended_by = actor
            assignment.save(update_fields=["state", "suspended_at", "suspended_by", "updated_at"])
        if assignments:
            _bump_policy(state, actor=actor, reason="staff_role_inactive")
        return assignments


def expire_due_capability_proposals(*, actor_user, request=None):
    expired = []
    ids = CapabilityProposal.objects.filter(
        status=CapabilityProposal.Status.PENDING,
        expires_at__lte=timezone.now(),
    ).values_list("pk", flat=True)
    for proposal_id in ids:
        with transaction.atomic():
            _policy_state()
            proposal = CapabilityProposal.objects.select_for_update().get(pk=proposal_id)
            if proposal.status != CapabilityProposal.Status.PENDING or proposal.expires_at > timezone.now():
                continue
            users, _, auth_rows = _lock_governance_participants(
                proposal.proposed_by_id, actor_user.pk
            )
            actor = _fresh_actor(users, actor_user)
            _require_privacy_admin(actor, auth_rows)
            expired.append(
                _finalize_without_mutation(
                    proposal=proposal,
                    actor=actor,
                    outcome=CapabilityProposalDecision.Outcome.EXPIRED,
                    reason="The proposal expired before execution.",
                    request=request,
                )
            )
    return expired
