from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from accounts.models import User
from audit.models import AuditEvent
from audit.services import record_audit_event
from people.models import Person
from staff_access.models import (
    CAPABILITY_POLICY_STATE_KEY,
    CapabilityPolicyState,
    StaffAdministratorProposal,
    StaffAdministratorProposalDecision,
    StaffRole,
    StaffRoleAssignment,
)


PROPOSAL_EXPIRY = timedelta(days=30)
_CRM_ADMIN = StaffRole.CRM_ADMIN
_PROMOTION_ROLES = (StaffRole.CRM_MANAGER, StaffRole.CRM_VIEWER)


def _reason(reason):
    value = str(reason or "").strip()
    if not value:
        raise ValidationError({"reason": "A reason is required for Administrator proposals."})
    return value


def _authorized(actor):
    from staff_access.authorization import can_access_staff_management

    if not can_access_staff_management(actor):
        raise PermissionDenied("Staff Management authority is required.")


def _snapshot(user):
    role = (
        StaffRoleAssignment.objects.active()
        .filter(user=user, role__family=StaffRole.CRM_ROLE_FAMILY)
        .values_list("role__code", flat=True)
        .first()
    )
    return {
        "user_is_active": bool(user.is_active),
        "person_record_type": getattr(user.person, "record_type", None),
        "person_archived": getattr(user.person, "archived_at", None) is not None,
        "active_crm_role": role,
    }


def _audit(*, proposal, actor, event_type, reason, changes=None, request=None):
    request_id = None
    ip_address = None
    if request is not None:
        request_id = request.headers.get("X-Request-ID") or request.META.get("HTTP_X_REQUEST_ID")
        ip_address = request.META.get("REMOTE_ADDR")
    return record_audit_event(
        action=AuditEvent.Action.CAPABILITY_PERMISSION_MUTATED,
        actor_user=actor,
        entity_type="StaffAdministratorProposal",
        entity_id=proposal.public_id,
        changes=changes or {},
        metadata={
            "event_type": event_type,
            "proposal_id": str(proposal.public_id),
            "operation": proposal.operation,
            "target_user_id": str(proposal.target_user_id),
            "proposer_user_id": str(proposal.proposed_by_id),
            "requested_role_code": proposal.requested_role.code,
            "reason": reason,
        },
        request_id=request_id,
        ip_address=ip_address,
    )


def _validate_target(*, operation, target):
    if target.person.record_type != Person.RecordType.STAFF:
        raise ValidationError("Administrator proposals require a STAFF target.")
    if target.person.archived_at is not None:
        raise ValidationError("The target Person is archived.")
    if operation == StaffAdministratorProposal.Operation.PROMOTE_EXISTING:
        if not target.is_active:
            raise ValidationError("Promotion requires an active target User.")
        if not StaffRoleAssignment.objects.active().filter(
            user=target, role__code__in=_PROMOTION_ROLES, role__family=StaffRole.CRM_ROLE_FAMILY
        ).exists():
            raise ValidationError("Promotion requires an active CRM Manager or Viewer role.")
        if StaffRoleAssignment.objects.active().filter(user=target, role__code=_CRM_ADMIN).exists():
            raise ValidationError("The target already has CRM Administrator authority.")
    elif operation == StaffAdministratorProposal.Operation.ONBOARD:
        if target.is_active:
            raise ValidationError("Onboarding requires an inactive, established Staff target.")
    elif operation == StaffAdministratorProposal.Operation.REACTIVATE:
        if target.is_active:
            raise ValidationError("Reactivation requires an inactive Staff target.")
    else:
        raise ValidationError("Unsupported Administrator proposal operation.")


def create_administrator_proposal(*, actor_user, operation, target_user, reason, request=None):
    _authorized(actor_user)
    reason = _reason(reason)
    operation = str(operation)
    if actor_user.pk == target_user.pk:
        raise PermissionDenied("A proposer cannot appoint themselves.")
    from staff_access.authorization import authorize_staff_operation, StaffOperation

    authorize_staff_operation(
        actor=actor_user,
        target=target_user,
        operation=StaffOperation.ADMIN_APPROVAL,
        request=request,
    )
    with transaction.atomic():
        target = User.objects.select_for_update().select_related("person").get(pk=target_user.pk)
        admin_role = StaffRole.objects.select_for_update().get(code=_CRM_ADMIN)
        if not admin_role.is_active or admin_role.family != StaffRole.CRM_ROLE_FAMILY:
            raise ValidationError("The canonical CRM Administrator role is unavailable.")
        _validate_target(operation=operation, target=target)
        state = CapabilityPolicyState.objects.select_for_update().get(
            singleton_key=1, key=CAPABILITY_POLICY_STATE_KEY
        )
        snapshot = _snapshot(target)
        proposal = StaffAdministratorProposal.objects.create(
            operation=operation,
            target_user=target,
            requested_role=admin_role,
            proposed_by=actor_user,
            reason=reason,
            policy_version=state.version,
            expected_target_state=snapshot,
            expires_at=timezone.now() + PROPOSAL_EXPIRY,
        )
        _audit(proposal=proposal, actor=actor_user, event_type="STAFF_ADMIN_PROPOSAL_CREATED", reason=reason, request=request)
        return proposal


def _lock_proposal(proposal):
    return StaffAdministratorProposal.objects.select_for_update().select_related(
        "requested_role", "target_user__person", "proposed_by__person"
    ).get(pk=proposal.pk if isinstance(proposal, StaffAdministratorProposal) else proposal)


def _invalidate(*, proposal, actor, reason, request=None):
    reason = _reason(reason)
    _audit(proposal=proposal, actor=actor, event_type="STAFF_ADMIN_PROPOSAL_INVALIDATED", reason=reason, request=request)
    proposal.status = StaffAdministratorProposal.Status.INVALIDATED
    proposal.decided_at = timezone.now()
    proposal.invalidation_reason = reason
    proposal.save(update_fields=["status", "decided_at", "invalidation_reason"])
    StaffAdministratorProposalDecision.objects.create(
        proposal=proposal, outcome=proposal.status, decided_by=actor, reason=reason
    )
    return proposal


def approve_administrator_proposal(*, proposal, actor_user, request=None):
    with transaction.atomic():
        proposal = _lock_proposal(proposal)
        _authorized(actor_user)
        if proposal.status != StaffAdministratorProposal.Status.PENDING:
            raise ValidationError("Only a pending Administrator proposal can be approved.")
        if proposal.proposed_by_id == actor_user.pk:
            raise PermissionDenied("A proposal cannot be approved by its proposer.")
        if proposal.target_user_id == actor_user.pk:
            raise PermissionDenied("The appointment target cannot approve the proposal.")
        now = timezone.now()
        if proposal.expires_at <= now:
            return _invalidate(proposal=proposal, actor=actor_user, reason="The proposal expired before approval.", request=request)
        target = User.objects.select_for_update().select_related("person").get(pk=proposal.target_user_id)
        proposer = User.objects.select_for_update().select_related("person").get(pk=proposal.proposed_by_id)
        if not target.is_active and proposal.operation == StaffAdministratorProposal.Operation.PROMOTE_EXISTING:
            return _invalidate(proposal=proposal, actor=actor_user, reason="The promotion target is no longer active.", request=request)
        _authorized(proposer)
        if proposal.policy_version != CapabilityPolicyState.objects.get(
            singleton_key=1, key=CAPABILITY_POLICY_STATE_KEY
        ).version or proposal.expected_target_state != _snapshot(target):
            return _invalidate(proposal=proposal, actor=actor_user, reason="The proposal target state is stale.", request=request)
        _validate_target(operation=proposal.operation, target=target)
        _audit(proposal=proposal, actor=actor_user, event_type="STAFF_ADMIN_PROPOSAL_APPROVED", reason=proposal.reason, request=request)
        proposal.approved_by = actor_user
        proposal.decided_at = now
        proposal.status = StaffAdministratorProposal.Status.APPROVED
        proposal.save(update_fields=["approved_by", "decided_at", "status"])
        StaffAdministratorProposalDecision.objects.create(
            proposal=proposal, outcome=proposal.status, decided_by=actor_user, reason=proposal.reason
        )
        if proposal.operation == StaffAdministratorProposal.Operation.PROMOTE_EXISTING:
            _effect_existing_promotion(proposal=proposal, actor=actor_user, request=request)
        return proposal


def _effect_existing_promotion(*, proposal, actor, request=None):
    target = User.objects.select_for_update().select_related("person").get(pk=proposal.target_user_id)
    admin_role = StaffRole.objects.select_for_update().get(code=_CRM_ADMIN)
    current = StaffRoleAssignment.objects.select_for_update().select_related("role").filter(
        user=target, role_family=StaffRole.CRM_ROLE_FAMILY, is_active=True, revoked_at__isnull=True
    ).first()
    if current is None or current.role.code not in _PROMOTION_ROLES:
        raise ValidationError("The approved promotion target no longer has an eligible CRM role.")
    current._revoke_unchecked(revoked_by=actor)
    assignment = StaffRoleAssignment.objects.select_for_update().filter(user=target, role=admin_role).first()
    created = assignment is None
    if created:
        assignment = StaffRoleAssignment.objects.create(
            user=target, role=admin_role, role_family=admin_role.family, assigned_by=actor
        )
    else:
        assignment._reactivate_unchecked(assigned_by=actor)
    target.crm_session_version = F("crm_session_version") + 1
    target.save(update_fields=["crm_session_version"])
    _audit(proposal=proposal, actor=actor, event_type="STAFF_ADMIN_PROMOTION_EFFECTED", reason=proposal.reason, changes={"previous_role": current.role.code, "new_role": _CRM_ADMIN}, request=request)
    proposal.status = StaffAdministratorProposal.Status.EFFECTED
    proposal.effected_at = timezone.now()
    proposal.resulting_assignment = assignment
    proposal.save(update_fields=["status", "effected_at", "resulting_assignment"])
    return assignment


def resolve_administrator_proposal(*, proposal, actor_user, outcome, reason, request=None):
    reason = _reason(reason)
    if outcome not in {
        StaffAdministratorProposal.Status.REJECTED,
        StaffAdministratorProposal.Status.CANCELLED,
    }:
        raise ValidationError("Only rejection and cancellation are supported by this operation.")
    with transaction.atomic():
        proposal = _lock_proposal(proposal)
        _authorized(actor_user)
        if proposal.status != StaffAdministratorProposal.Status.PENDING:
            raise ValidationError("Only a pending Administrator proposal can be resolved.")
        if outcome == StaffAdministratorProposal.Status.CANCELLED and proposal.proposed_by_id != actor_user.pk:
            raise PermissionDenied("Only the proposer may cancel a proposal.")
        if outcome == StaffAdministratorProposal.Status.REJECTED and proposal.proposed_by_id == actor_user.pk:
            raise PermissionDenied("The proposer cannot reject their own proposal.")
        _audit(proposal=proposal, actor=actor_user, event_type=f"STAFF_ADMIN_PROPOSAL_{outcome}", reason=reason, request=request)
        proposal.status = outcome
        proposal.decided_at = timezone.now()
        proposal.invalidation_reason = reason
        proposal.save(update_fields=["status", "decided_at", "invalidation_reason"])
        StaffAdministratorProposalDecision.objects.create(proposal=proposal, outcome=outcome, decided_by=actor_user, reason=reason)
        return proposal


def consume_administrator_proposal(*, proposal, actor_user, resulting_assignment, expected_operation, request=None):
    """Consume approved onboarding/reactivation authority after invitation acceptance.

    Invitation services supply the resulting assignment and the operation they
    are effecting. This function is deliberately separate from approval so approved authority
    cannot be mistaken for effective CRM Administrator access.
    """
    with transaction.atomic():
        proposal = _lock_proposal(proposal)
        _authorized(actor_user)
        if proposal.status != StaffAdministratorProposal.Status.APPROVED:
            raise ValidationError("Only an approved Administrator proposal can be consumed.")
        if expected_operation != proposal.operation:
            raise ValidationError("The approved Administrator operation does not match the consuming workflow.")
        if proposal.operation == StaffAdministratorProposal.Operation.PROMOTE_EXISTING:
            raise ValidationError("Existing-staff promotions are consumed during approval.")
        if proposal.expires_at <= timezone.now():
            return _invalidate(proposal=proposal, actor=actor_user, reason="The approved authority expired before consumption.", request=request)
        assignment = StaffRoleAssignment.objects.select_for_update().select_related("role").get(pk=resulting_assignment.pk)
        if assignment.user_id != proposal.target_user_id or assignment.role.code != _CRM_ADMIN or not assignment.is_active or assignment.revoked_at is not None:
            raise ValidationError("The resulting assignment does not match the approved Administrator appointment.")
        _audit(proposal=proposal, actor=actor_user, event_type="STAFF_ADMIN_PROPOSAL_EFFECTED", reason=proposal.reason, changes={"resulting_assignment_id": assignment.pk}, request=request)
        proposal.status = StaffAdministratorProposal.Status.EFFECTED
        proposal.effected_at = timezone.now()
        proposal.resulting_assignment = assignment
        proposal.save(update_fields=["status", "effected_at", "resulting_assignment"])
        return proposal


def invalidate_administrator_proposals_for_user(*, user_id, actor_user, reason, request=None):
    """Invalidate pending or approved-but-unused authority for one User."""
    reason = _reason(reason)
    invalidated = []
    proposals = StaffAdministratorProposal.objects.select_for_update().select_related(
        "requested_role"
    ).filter(
        Q(target_user_id=user_id) | Q(proposed_by_id=user_id) | Q(approved_by_id=user_id),
        status__in=(StaffAdministratorProposal.Status.PENDING, StaffAdministratorProposal.Status.APPROVED),
    ).order_by("pk")
    for proposal in proposals:
        _audit(
            proposal=proposal,
            actor=actor_user,
            event_type="STAFF_ADMIN_PROPOSAL_INVALIDATED",
            reason=reason,
            request=request,
        )
        proposal.status = StaffAdministratorProposal.Status.INVALIDATED
        proposal.decided_at = proposal.decided_at or timezone.now()
        proposal.invalidation_reason = reason
        proposal.save(update_fields=["status", "decided_at", "invalidation_reason"])
        if not hasattr(proposal, "decision"):
            StaffAdministratorProposalDecision.objects.create(
                proposal=proposal,
                outcome=proposal.status,
                decided_by=actor_user,
                reason=reason,
            )
        invalidated.append(proposal)
    return invalidated
