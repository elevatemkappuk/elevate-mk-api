import hashlib
import secrets
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.contrib.auth.password_validation import validate_password
from django.conf import settings
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from accounts.models import User
from audit.models import AuditEvent
from audit.services import record_audit_event
from people.models import Person
from staff_access.exceptions import FinalCrmAdminProtectionError
from staff_access.models import (
    CAPABILITY_POLICY_STATE_KEY,
    CapabilityPolicyState,
    StaffEmailChangeRequest,
    StaffInvitation,
    StaffCapabilityAssignment,
    StaffRole,
    StaffRoleAssignment,
    StaffAdministratorProposal,
)


def _normalize_invitation_email(email):
    return str(email or "").strip().lower()


def _invitation_token_hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _require_staff_role_mutation_actor(actor_user):
    from staff_access.authorization import authorize_staff_operation, StaffOperation

    authorize_staff_operation(actor=actor_user, operation=StaffOperation.ROLE_ASSIGNMENT)


def create_staff_invitation(*, actor_user, email, first_name, last_name, role_codes, expiry_hours=None, target_user=None, reason=""):
    """Create a pending Staff identity without creating a User yet."""
    from accounts.models import User
    from people.models import Person
    from staff_access.authorization import authorize_staff_operation, StaffOperation

    authorize_staff_operation(actor=actor_user, operation=StaffOperation.ACCESS)
    email = _normalize_invitation_email(email)
    if not email:
        raise ValidationError({"email": "An email address is required."})
    existing_user = User.objects.select_related("person").filter(email=email).first()
    if existing_user is not None and (existing_user.is_active or existing_user.person.record_type != Person.RecordType.STAFF):
        raise ValidationError({"email": "An active or non-Staff account already uses this email address."})
    allowed_codes = StaffRole.canonical_codes()
    role_codes = list(dict.fromkeys(role_codes or []))
    if len(role_codes) != 1 or not set(role_codes).issubset(allowed_codes):
        raise ValidationError({"role_codes": "Exactly one canonical CRM role may be invited."})
    if Person.objects.filter(record_type=Person.RecordType.STAFF, primary_email__iexact=email, user__isnull=True).exists():
        raise ValidationError({"email": "A pending Staff identity already uses this email address."})

    expiry_hours = settings.STAFF_INVITATION_EXPIRY_HOURS if expiry_hours is None else expiry_hours
    reason = str(reason or "").strip()
    if not reason:
        raise ValidationError({"reason": "A reason is required for staff onboarding."})
    with transaction.atomic():
        StaffInvitation.objects.filter(
            intended_email=email,
            accepted_at__isnull=True,
            revoked_at__isnull=True,
            superseded_at__isnull=True,
        ).update(superseded_at=timezone.now())
        invitation = StaffInvitation.objects.create(
            intended_email=email,
            first_name=first_name.strip(),
            last_name=last_name.strip(),
            pending_role_codes=role_codes,
            target_user=target_user or existing_user,
            reason=reason,
            operation="ONBOARD",
            token_hash=None,
            expires_at=timezone.now() + timedelta(hours=expiry_hours),
            created_by=actor_user,
        )
        _queue_staff_invitation_email(invitation)
    return invitation


def _queue_staff_invitation_email(invitation):
    from django.conf import settings
    from notifications.models import TransactionalEmailJob

    return TransactionalEmailJob.objects.create(
        staff_invitation=invitation,
        template_id=settings.BREVO_STAFF_INVITATION_TEMPLATE_ID,
        recipient_email=invitation.intended_email,
        recipient_name=f"{invitation.first_name} {invitation.last_name}".strip(),
        first_name=invitation.first_name,
        expires_in_hours=max(1, int((invitation.expires_at - timezone.now()).total_seconds() // 3600)),
        job_type=TransactionalEmailJob.JobType.STAFF_INVITATION,
    )


def resend_staff_invitation(*, invitation, actor_user, reason=""):
    from staff_access.authorization import authorize_staff_operation, StaffOperation
    authorize_staff_operation(actor=actor_user, operation=StaffOperation.ACCESS)
    if not str(reason or "").strip():
        raise ValidationError({"reason": "A reason is required to resend an invitation."})
    if invitation.accepted_at or invitation.revoked_at:
        raise ValidationError("Only a pending Staff invitation can be resent.")
    with transaction.atomic():
        invitation = StaffInvitation.objects.select_for_update().get(pk=invitation.pk)
        if invitation.target_user_id and (invitation.target_user_id == actor_user.pk or invitation.target_user.is_staff or invitation.target_user.is_superuser):
            raise ValidationError("This invitation cannot be managed by the current administrator.")
        from notifications.models import TransactionalEmailJob
        old_job = TransactionalEmailJob.objects.select_for_update().filter(staff_invitation=invitation).first()
        if old_job and old_job.status in (TransactionalEmailJob.Status.PENDING, TransactionalEmailJob.Status.PROCESSING):
            old_job.status = TransactionalEmailJob.Status.CANCELLED
            old_job.completed_at = timezone.now()
            old_job.last_error_code = "INVITATION_REPLACED"
            old_job.save(update_fields=["status", "completed_at", "last_error_code", "updated_at"])
        invitation.superseded_at = timezone.now()
        invitation.save(update_fields=["superseded_at", "updated_at"])
        replacement = StaffInvitation.objects.create(
            intended_email=invitation.intended_email,
            first_name=invitation.first_name,
            last_name=invitation.last_name,
            pending_role_codes=invitation.pending_role_codes,
            target_user=invitation.target_user,
            reason=invitation.reason,
            operation=invitation.operation,
            token_hash=None,
            expires_at=timezone.now() + timedelta(hours=settings.STAFF_INVITATION_EXPIRY_HOURS),
            created_by=actor_user,
        )
        if invitation.operation == "ONBOARD" and invitation.pending_role_codes == [StaffRole.CRM_ADMIN]:
            from staff_access.models import StaffAdministratorProposal
            proposal = StaffAdministratorProposal.objects.select_for_update().filter(resulting_invitation=invitation).first()
            if proposal is None or proposal.status != StaffAdministratorProposal.Status.APPROVED or proposal.expires_at <= timezone.now():
                raise ValidationError("The Administrator approval is no longer valid for resend.")
            replacement.expires_at = min(replacement.expires_at, proposal.expires_at)
            replacement.save(update_fields=["expires_at", "updated_at"])
            proposal.resulting_invitation = replacement
            proposal.save(update_fields=["resulting_invitation"])
        _queue_staff_invitation_email(replacement)
        if replacement.target_user_id:
            _lifecycle_audit(
                action=AuditEvent.Action.CAPABILITY_PERMISSION_MUTATED,
                actor_user=actor_user,
                target_user=replacement.target_user,
                event_type="STAFF_INVITATION_RESENT",
                reason=str(reason).strip(),
                metadata={"invitation_id": str(replacement.public_id)},
            )
    return replacement


def update_pending_staff_invitation_roles(*, invitation, actor_user, role_codes):
    from staff_access.authorization import authorize_staff_operation, StaffOperation
    authorize_staff_operation(actor=actor_user, operation=StaffOperation.ACCESS)
    role_codes = list(dict.fromkeys(role_codes or []))
    if len(role_codes) != 1 or not set(role_codes).issubset(StaffRole.canonical_codes()):
        raise ValidationError({"role_codes": "Exactly one canonical CRM role may be assigned."})
    if invitation.accepted_at or invitation.revoked_at or invitation.superseded_at:
        raise ValidationError("Only a current pending Staff invitation can be updated.")
    with transaction.atomic():
        invitation = StaffInvitation.objects.select_for_update().get(pk=invitation.pk)
        invitation.superseded_at = timezone.now()
        invitation.save(update_fields=["superseded_at", "updated_at"])
        from notifications.models import TransactionalEmailJob
        old_job = TransactionalEmailJob.objects.select_for_update().filter(staff_invitation=invitation).first()
        if old_job and old_job.status in (TransactionalEmailJob.Status.PENDING, TransactionalEmailJob.Status.PROCESSING):
            old_job.status = TransactionalEmailJob.Status.CANCELLED
            old_job.completed_at = timezone.now()
            old_job.last_error_code = "INVITATION_ROLES_UPDATED"
            old_job.save(update_fields=["status", "completed_at", "last_error_code", "updated_at"])
        replacement = StaffInvitation.objects.create(
            intended_email=invitation.intended_email,
            first_name=invitation.first_name,
            last_name=invitation.last_name,
            pending_role_codes=role_codes,
            token_hash=None,
            expires_at=timezone.now() + timedelta(hours=72),
            created_by=actor_user,
        )
        _queue_staff_invitation_email(replacement)
    return replacement


def _lifecycle_audit(*, action, actor_user, target_user, event_type, reason, metadata=None, request=None):
    request_id = None
    ip_address = None
    if request is not None:
        request_id = request.headers.get("X-Request-ID") or request.META.get("HTTP_X_REQUEST_ID")
        ip_address = request.META.get("REMOTE_ADDR")
    return record_audit_event(
        action=action,
        actor_user=actor_user,
        entity_type="User",
        entity_id=target_user.pk,
        metadata={
            "event_type": event_type,
            "target_user_id": str(target_user.pk),
            "reason": reason,
            **(metadata or {}),
        },
        request_id=request_id,
        ip_address=ip_address,
    )


def _cancel_incompatible_invitations(*, target_user, actor_user, reason, request=None):
    from notifications.models import TransactionalEmailJob

    cancelled = []
    invitations = StaffInvitation.objects.select_for_update().filter(
        intended_email=target_user.email,
        accepted_at__isnull=True,
        revoked_at__isnull=True,
        superseded_at__isnull=True,
    ).order_by("pk")
    for invitation in invitations:
        job = TransactionalEmailJob.objects.select_for_update().filter(staff_invitation=invitation).first()
        if job and job.status in (TransactionalEmailJob.Status.PENDING, TransactionalEmailJob.Status.PROCESSING):
            job.status = TransactionalEmailJob.Status.CANCELLED
            job.completed_at = timezone.now()
            job.last_error_code = "INVITATION_CANCELLED_LIFECYCLE"
            job.save(update_fields=["status", "completed_at", "last_error_code", "updated_at"])
        invitation.revoked_at = timezone.now()
        invitation.save(update_fields=["revoked_at", "updated_at"])
        _lifecycle_audit(
            action=AuditEvent.Action.CAPABILITY_PERMISSION_MUTATED,
            actor_user=actor_user,
            target_user=target_user,
            event_type="STAFF_INVITATION_CANCELLED_LIFECYCLE",
            reason=reason,
            metadata={"invitation_id": str(invitation.public_id)},
            request=request,
        )
        cancelled.append(invitation)
    return cancelled


def deactivate_staff_access(*, target_user, actor_user, reason, request=None):
    """Remove CRM staff authority without disabling global authentication."""
    from staff_access.authorization import authorize_staff_operation, StaffOperation
    authorize_staff_operation(
        actor=actor_user,
        target=target_user,
        operation=StaffOperation.ACCESS_DEACTIVATION,
        request=request,
    )
    reason = str(reason or "").strip()
    if not reason:
        raise ValidationError({"reason": "A reason is required for staff-access deactivation."})
    with transaction.atomic():
        policy_state = CapabilityPolicyState.objects.select_for_update().filter(
            singleton_key=1, key=CAPABILITY_POLICY_STATE_KEY,
        ).first()
        crm_admin_role = StaffRole.objects.select_for_update().get(code=StaffRole.CRM_ADMIN)
        from staff_access.administrator_governance import invalidate_administrator_proposals_for_user
        invalidated_proposals = invalidate_administrator_proposals_for_user(
            user_id=target_user.pk,
            actor_user=actor_user,
            reason="Staff access was deactivated before the appointment was effected.",
            request=request,
        )
        locked = User.objects.select_for_update().select_related("person").get(pk=target_user.pk)
        if locked.person.record_type != Person.RecordType.STAFF:
            raise ValidationError("Only STAFF accounts may have operational staff access deactivated.")
        assignments = list(
            StaffRoleAssignment.objects.select_for_update().select_related("role").filter(
                user=locked,
                role_family=StaffRole.CRM_ROLE_FAMILY,
                is_active=True,
                revoked_at__isnull=True,
            ).order_by("pk")
        )
        admin_assignments = [item for item in assignments if item.role_id == crm_admin_role.pk]
        if admin_assignments:
            active_admin_count = StaffRoleAssignment.objects.active().filter(
                role=crm_admin_role,
                user__is_active=True,
                user__person__archived_at__isnull=True,
            ).count()
            if active_admin_count <= len(admin_assignments):
                raise FinalCrmAdminProtectionError()
        for assignment in assignments:
            assignment._revoke_unchecked(revoked_by=actor_user)
            _lifecycle_audit(
                action=AuditEvent.Action.STAFF_ROLE_REVOKED,
                actor_user=actor_user,
                target_user=locked,
                event_type="STAFF_ACCESS_ROLE_REVOKED",
                reason=reason,
                metadata={"assignment_id": assignment.pk, "staff_role_code": assignment.role.code},
                request=request,
            )
        grants = list(
            StaffCapabilityAssignment.objects.select_for_update().filter(
                user=locked, state=StaffCapabilityAssignment.State.ACTIVE,
            ).order_by("pk")
        )
        for grant in grants:
            grant.state = StaffCapabilityAssignment.State.SUSPENDED
            grant.suspended_at = timezone.now()
            grant.suspended_by = actor_user
            grant.save(update_fields=["state", "suspended_at", "suspended_by", "updated_at"])
            _lifecycle_audit(
                action=AuditEvent.Action.CAPABILITY_PERMISSION_MUTATED,
                actor_user=actor_user,
                target_user=locked,
                event_type="STAFF_PRIVACY_GRANT_AUTO_SUSPENDED",
                reason=reason,
                metadata={"capability_id": grant.capability_id, "grant_id": grant.pk},
                request=request,
            )
        if assignments or grants:
            locked.crm_session_version = F("crm_session_version") + 1
            locked.save(update_fields=["crm_session_version"])
        cancelled_invitations = _cancel_incompatible_invitations(
            target_user=locked,
            actor_user=actor_user,
            reason=reason,
            request=request,
        )
        if not (assignments or grants or cancelled_invitations or invalidated_proposals):
            return locked
        _lifecycle_audit(
            action=AuditEvent.Action.CAPABILITY_PERMISSION_MUTATED,
            actor_user=actor_user,
            target_user=locked,
            event_type="STAFF_ACCESS_DEACTIVATED",
            reason=reason,
            metadata={
                "crm_role_assignments_revoked": len(assignments),
                "privacy_grants_suspended": len(grants),
                "user_is_active_preserved": bool(locked.is_active),
                "person_preserved": True,
                "administrator_proposals_invalidated": len(invalidated_proposals),
                "invitations_cancelled": len(cancelled_invitations),
            },
            request=request,
        )
        return locked


def offboard_staff_user(*, target_user, actor_user, reason, request=None):
    """Compatibility wrapper retaining the old name with independent semantics."""
    return deactivate_staff_access(
        target_user=target_user,
        actor_user=actor_user,
        reason=reason,
        request=request,
    )


def _cancel_incompatible_email_changes(*, target_user, actor_user, reason, request=None):
    from notifications.models import TransactionalEmailJob

    changes = StaffEmailChangeRequest.objects.select_for_update().filter(
        user=target_user, verified_at__isnull=True, superseded_at__isnull=True,
    ).order_by("pk")
    cancelled = []
    for change in changes:
        job = TransactionalEmailJob.objects.select_for_update().filter(
            staff_email_change_request=change,
        ).first()
        if job and job.status in (TransactionalEmailJob.Status.PENDING, TransactionalEmailJob.Status.PROCESSING):
            job.status = TransactionalEmailJob.Status.CANCELLED
            job.completed_at = timezone.now()
            job.last_error_code = "EMAIL_CHANGE_CANCELLED_LIFECYCLE"
            job.save(update_fields=["status", "completed_at", "last_error_code", "updated_at"])
        change.superseded_at = timezone.now()
        change.save(update_fields=["superseded_at", "updated_at"])
        cancelled.append(change)
    return cancelled


def disable_user_account(*, target_user, actor_user, reason, request=None):
    """Disable global authentication without revoking staff assignments."""
    from staff_access.authorization import authorize_staff_operation, StaffOperation
    from accounts.revocation import revoke_user_credentials

    reason = str(reason or "").strip()
    if not reason:
        raise ValidationError({"reason": "A reason is required for account disablement."})
    authorize_staff_operation(
        actor=actor_user,
        target=target_user,
        operation=StaffOperation.ACCOUNT_DISABLE,
        request=request,
    )
    with transaction.atomic():
        policy_state = CapabilityPolicyState.objects.select_for_update().filter(
            singleton_key=1, key=CAPABILITY_POLICY_STATE_KEY,
        ).first()
        crm_admin_role = StaffRole.objects.select_for_update().get(code=StaffRole.CRM_ADMIN)
        admin_assignments = list(
            StaffRoleAssignment.objects.select_for_update().select_related("user__person").filter(
                role=crm_admin_role, is_active=True, revoked_at__isnull=True,
            ).order_by("pk")
        )
        from staff_access.administrator_governance import invalidate_administrator_proposals_for_user
        invalidated_proposals = invalidate_administrator_proposals_for_user(
            user_id=target_user.pk,
            actor_user=actor_user,
            reason="The account was globally disabled before the appointment was effected.",
            request=request,
        )
        locked = User.objects.select_for_update().select_related("person").get(pk=target_user.pk)
        if locked.person.record_type != Person.RecordType.STAFF:
            raise ValidationError("Only STAFF accounts may be disabled through Staff Management.")
        if not locked.is_active:
            if locked.account_lifecycle_state == User.AccountLifecycleState.PENDING_ACTIVATION:
                raise ValidationError("A pending-activation account is not an existing disabled account.")
            return locked
        usable_admins = [item for item in admin_assignments if item.user.is_active and item.user.person.archived_at is None]
        if any(item.user_id == locked.pk for item in admin_assignments) and len(usable_admins) <= 1:
            raise FinalCrmAdminProtectionError()
        User.objects.filter(pk=locked.pk).update(
            is_active=False,
            account_lifecycle_state=User.AccountLifecycleState.DISABLED,
            # Django's password-reset token includes last_login; changing it
            # invalidates tokens issued before this security transition.
            last_login=timezone.now(),
        )
        locked.refresh_from_db()
        revoke_user_credentials(user=locked, reason="account_disabled", actor_user=actor_user, request=request)
        _cancel_incompatible_invitations(target_user=locked, actor_user=actor_user, reason=reason, request=request)
        _cancel_incompatible_email_changes(target_user=locked, actor_user=actor_user, reason=reason, request=request)
        _lifecycle_audit(
            action=AuditEvent.Action.ACCOUNT_DISABLED,
            actor_user=actor_user,
            target_user=locked,
            event_type="ACCOUNT_DISABLED_LIFECYCLE",
            reason=reason,
            metadata={
                "staff_roles_retained": True,
                "credentials_revoked": True,
                "crm_sessions_invalidated": True,
                "administrator_proposals_invalidated": len(invalidated_proposals),
                "policy_version": None if policy_state is None else policy_state.version,
            },
            request=request,
        )
        return locked


def enable_user_account(*, target_user, actor_user, reason, request=None):
    """Enable an explicitly disabled account without restoring other authority."""
    from staff_access.authorization import authorize_staff_operation, StaffOperation

    reason = str(reason or "").strip()
    if not reason:
        raise ValidationError({"reason": "A reason is required for account enablement."})
    authorize_staff_operation(
        actor=actor_user,
        target=target_user,
        operation=StaffOperation.ACCOUNT_ENABLE,
        request=request,
    )
    with transaction.atomic():
        locked = User.objects.select_for_update().select_related("person").get(pk=target_user.pk)
        if locked.person.record_type != Person.RecordType.STAFF:
            raise ValidationError("Only STAFF accounts may be enabled through Staff Management.")
        if locked.is_active:
            if locked.account_lifecycle_state != User.AccountLifecycleState.ACTIVE:
                raise ValidationError("The active account has an incompatible lifecycle state.")
            return locked
        if locked.account_lifecycle_state != User.AccountLifecycleState.DISABLED:
            raise ValidationError("Only an explicitly disabled account may be enabled.")
        User.objects.filter(pk=locked.pk).update(
            is_active=True,
            account_lifecycle_state=User.AccountLifecycleState.ACTIVE,
        )
        locked.refresh_from_db()
        _lifecycle_audit(
            action=AuditEvent.Action.ACCOUNT_REENABLED,
            actor_user=actor_user,
            target_user=locked,
            event_type="ACCOUNT_ENABLED_LIFECYCLE",
            reason=reason,
            metadata={"fresh_authentication_required": True},
            request=request,
        )
        return locked


def request_administrative_password_reset(*, target_user, actor_user, reason, request=None):
    """Queue a reset email without changing credentials or exposing a token."""
    from staff_access.authorization import authorize_staff_operation, StaffOperation
    from notifications.models import TransactionalEmailJob

    reason = str(reason or "").strip()
    if not reason:
        raise ValidationError({"reason": "A reason is required for an administrative password reset."})
    authorize_staff_operation(
        actor=actor_user,
        target=target_user,
        operation=StaffOperation.ADMIN_PASSWORD_RESET,
        request=request,
    )
    template_id = str(getattr(settings, "BREVO_PASSWORD_RESET_TEMPLATE_ID", "") or "").strip()
    if not template_id:
        raise ValidationError("Administrative password reset email is not configured.")
    with transaction.atomic():
        locked = User.objects.select_for_update().select_related("person").get(pk=target_user.pk)
        if locked.person.record_type != Person.RecordType.STAFF:
            raise ValidationError("Only STAFF accounts may receive administrative password resets.")
        if not locked.is_active or locked.account_lifecycle_state != User.AccountLifecycleState.ACTIVE:
            raise ValidationError("Only active Staff accounts may receive administrative password resets.")
        if not locked.has_usable_password():
            raise ValidationError("The target account does not have a usable password.")
        recent_cutoff = timezone.now() - timedelta(hours=1)
        if TransactionalEmailJob.objects.filter(
            password_reset_user=locked,
            job_type=TransactionalEmailJob.JobType.ADMIN_PASSWORD_RESET,
            status=TransactionalEmailJob.Status.PENDING,
            created_at__gte=recent_cutoff,
        ).exists():
            raise ValidationError("A password-reset email was recently queued for this account.")
        job = TransactionalEmailJob.objects.create(
            password_reset_user=locked,
            template_id=template_id,
            recipient_email=locked.email,
            recipient_name=locked.get_full_name() or "",
            first_name=locked.person.first_name,
            expires_in_hours=72,
            job_type=TransactionalEmailJob.JobType.ADMIN_PASSWORD_RESET,
        )
        _lifecycle_audit(
            action=AuditEvent.Action.PASSWORD_RESET,
            actor_user=actor_user,
            target_user=locked,
            event_type="ADMINISTRATIVE_PASSWORD_RESET_REQUESTED",
            reason=reason,
            metadata={
                "operation": "ADMINISTRATIVE_PASSWORD_RESET",
                "outcome": "QUEUED",
                "password_unchanged": True,
                "recipient_bound_to_registered_login": True,
                "job_id": str(job.pk),
            },
            request=request,
        )
        return job


def assign_staff_role(*, user, role, assigned_by=None, audit=True, return_details=False):
    """Assign or reactivate one Staff role under the shared lifecycle lock contract."""
    if assigned_by is None:
        raise PermissionError("Staff role assignment requires an attributable actor.")
    if not audit:
        raise PermissionError("Staff role assignment auditing cannot be disabled.")
    _require_staff_role_mutation_actor(assigned_by)
    if isinstance(role, str):
        role = StaffRole.objects.get(code=role)

    with transaction.atomic():
        policy_state = CapabilityPolicyState.objects.select_for_update().filter(
            singleton_key=1,
            key=CAPABILITY_POLICY_STATE_KEY,
        ).first()
        role_ids = {role.pk, StaffRole.objects.get(code=StaffRole.CRM_ADMIN).pk}
        locked_roles = list(StaffRole.objects.select_for_update().filter(pk__in=role_ids).order_by("pk"))
        locked_role = next(item for item in locked_roles if item.pk == role.pk)

        if locked_role.code not in StaffRole.canonical_codes() or not locked_role.is_active:
            raise ValidationError("Only active canonical Staff roles can be assigned.")

        existing_assignments = list(
            StaffRoleAssignment.objects.select_for_update()
            .filter(user_id=user.pk)
            .order_by("pk")
        )
        locked_user = user.__class__.objects.select_for_update().get(pk=user.pk)
        eligible_person = locked_user.person.record_type == Person.RecordType.STAFF
        if not locked_user.is_active or (not eligible_person and not locked_user.is_superuser):
            raise ValidationError("Staff roles require an active STAFF user or the approved active superuser exception.")

        if locked_role.family == StaffRole.CRM_ROLE_FAMILY:
            active_family_assignment = next(
                (
                    item for item in existing_assignments
                    if item.is_active and item.revoked_at is None
                    and item.role_family == StaffRole.CRM_ROLE_FAMILY
                    and item.role_id != locked_role.pk
                ),
                None,
            )
            if active_family_assignment is not None:
                raise ValidationError(
                    "A CRM role is already active; use replace_staff_role for an atomic role change."
                )

        assignment = next(
            (item for item in existing_assignments if item.role_id == locked_role.pk),
            None,
        )
        created = assignment is None
        reactivated = bool(assignment is not None and not assignment.is_active)
        if created:
            assignment = StaffRoleAssignment.objects.create(
                user=locked_user,
                role=locked_role,
                role_family=locked_role.family,
                assigned_by=assigned_by,
            )
        elif reactivated:
            assignment._reactivate_unchecked(assigned_by=assigned_by)
        else:
            if return_details:
                return assignment, False, False, policy_state.version if policy_state is not None else None
            return assignment

        if policy_state is not None:
            policy_state.version += 1
            policy_state.updated_by = assigned_by
            policy_state.reason = "staff_role_assignment_changed"
            policy_state.save(update_fields=["version", "updated_by", "reason", "updated_at"])

        if audit:
            record_audit_event(
                action=(
                    AuditEvent.Action.STAFF_ROLE_ASSIGNED
                    if created
                    else AuditEvent.Action.STAFF_ROLE_REACTIVATED
                ),
                actor_user=assigned_by,
                entity_type="StaffRoleAssignment",
                entity_id=assignment.pk,
                changes={
                    "is_active": {
                        "from": None if created else False,
                        "to": True,
                    },
                },
                metadata={
                    "target_user_id": str(locked_user.pk),
                    "staff_role_id": str(locked_role.pk),
                    "staff_role_code": locked_role.code,
                    **({"policy_version": policy_state.version} if policy_state is not None else {}),
                },
            )
        if return_details:
            return assignment, created, reactivated, policy_state.version if policy_state is not None else None
        return assignment


def replace_staff_role(*, user, role, replaced_by, reason, request=None):
    """Atomically replace the target's active CRM role and preserve history."""
    from staff_access.authorization import authorize_staff_operation, StaffOperation

    if not str(reason or "").strip():
        raise ValidationError({"reason": "A reason is required for CRM role changes."})
    if isinstance(role, str):
        role = StaffRole.objects.get(code=role)
    authorize_staff_operation(
        actor=replaced_by,
        target=user,
        operation=StaffOperation.ROLE_CHANGE,
        request=request,
    )

    with transaction.atomic():
        crm_admin_role = StaffRole.objects.select_for_update().get(code=StaffRole.CRM_ADMIN)
        requested_role = StaffRole.objects.select_for_update().get(pk=role.pk)
        locked_user = User.objects.select_for_update().select_related("person").get(pk=user.pk)
        if requested_role.family != StaffRole.CRM_ROLE_FAMILY or not requested_role.is_active:
            raise ValidationError("Only active canonical CRM roles can be assigned.")
        if locked_user.person.record_type != Person.RecordType.STAFF or not locked_user.is_active:
            raise ValidationError("CRM role changes require an active STAFF user.")
        if requested_role.code == StaffRole.CRM_ADMIN:
            raise ValidationError("CRM_ADMIN appointments require the Phase 2C approval workflow.")

        active_assignments = list(
            StaffRoleAssignment.objects.select_for_update()
            .select_related("role")
            .filter(
                user=locked_user,
                role_family=StaffRole.CRM_ROLE_FAMILY,
                is_active=True,
                revoked_at__isnull=True,
            )
            .order_by("pk")
        )
        current = active_assignments[0] if active_assignments else None
        if current is not None and current.role_id == requested_role.pk:
            return current
        if current is None:
            raise ValidationError("CRM role replacement requires an existing active CRM role.")

        from staff_access.authorization import usable_crm_admin_assignments
        if (
            current.role_id == crm_admin_role.pk
            and not usable_crm_admin_assignments().exclude(pk=current.pk).exists()
        ):
            raise FinalCrmAdminProtectionError()

        current._revoke_unchecked(revoked_by=replaced_by)
        replacement = next(
            (
                assignment for assignment in StaffRoleAssignment.objects.select_for_update()
                .filter(user=locked_user, role=requested_role)
            ),
            None,
        )
        replacement_created = replacement is None
        if replacement_created:
            replacement = StaffRoleAssignment.objects.create(
                user=locked_user,
                role=requested_role,
                role_family=requested_role.family,
                assigned_by=replaced_by,
            )
        else:
            replacement._reactivate_unchecked(assigned_by=replaced_by)

        locked_user.crm_session_version = F("crm_session_version") + 1
        locked_user.save(update_fields=["crm_session_version"])
        record_audit_event(
            action=AuditEvent.Action.STAFF_ROLE_REVOKED,
            actor_user=replaced_by,
            entity_type="StaffRoleAssignment",
            entity_id=current.pk,
            changes={"is_active": {"from": True, "to": False}},
            metadata={
                "target_user_id": str(locked_user.pk),
                "previous_role_code": current.role.code,
                "new_role_code": requested_role.code,
                "operation": "CRM_ROLE_REPLACED",
                "reason": str(reason).strip(),
                "crm_sessions_invalidated": True,
            },
        )
        record_audit_event(
            action=AuditEvent.Action.STAFF_ROLE_ASSIGNED,
            actor_user=replaced_by,
            entity_type="StaffRoleAssignment",
            entity_id=replacement.pk,
            changes={"is_active": {"from": None if replacement_created else False, "to": True}},
            metadata={
                "target_user_id": str(locked_user.pk),
                "previous_role_code": current.role.code,
                "new_role_code": requested_role.code,
                "operation": "CRM_ROLE_REPLACED",
                "reason": str(reason).strip(),
                "crm_sessions_invalidated": True,
            },
        )
        return replacement


def activate_staff_role(role, *, actor_user=None):
    """Reactivate a role while preserving the shared policy lock contract."""
    if actor_user is None:
        raise PermissionError("Role activation requires an authenticated actor.")
    _require_staff_role_mutation_actor(actor_user)
    with transaction.atomic():
        policy_state = CapabilityPolicyState.objects.select_for_update().filter(
            singleton_key=1,
            key=CAPABILITY_POLICY_STATE_KEY,
        ).first()
        if policy_state is None:
            raise RuntimeError("Capability policy state is required for audited role activation.")
        locked_roles = list(StaffRole.objects.select_for_update().order_by("pk"))
        locked_role = next(item for item in locked_roles if item.pk == role.pk)
        if locked_role.is_active:
            return locked_role
        locked_role.is_active = True
        locked_role.save(update_fields=["is_active", "updated_at"])
        if policy_state is not None:
            policy_state.version += 1
            policy_state.updated_by = actor_user
            policy_state.reason = "staff_role_activated"
            policy_state.save(update_fields=["version", "updated_by", "reason", "updated_at"])
            record_audit_event(
                action=AuditEvent.Action.CAPABILITY_PERMISSION_MUTATED,
                actor_user=actor_user,
                entity_type="StaffRole",
                entity_id=locked_role.pk,
                metadata={
                    "staff_role_code": locked_role.code,
                    "role_active": True,
                    "policy_version": policy_state.version,
                    "reason": "staff_role_activated",
                },
            )
        return locked_role


def reactivate_staff_user(*, target_user, actor_user, role_code, reason, request=None):
    """Start a fresh role-bound invitation or Administrator proposal."""
    from staff_access.authorization import authorize_staff_operation, StaffOperation
    authorize_staff_operation(actor=actor_user, target=target_user, operation=StaffOperation.ACCESS_REACTIVATION, request=request)
    reason = str(reason or "").strip()
    if not reason:
        raise ValidationError({"reason": "A reason is required for staff reactivation."})
    role_code = str(role_code or "")
    if role_code not in {StaffRole.CRM_ADMIN, StaffRole.CRM_MANAGER, StaffRole.CRM_VIEWER}:
        raise ValidationError({"role_code": "A canonical CRM role is required for reactivation."})
    target_user = User.objects.select_related("person").get(pk=target_user.pk)
    if target_user.person.record_type != Person.RecordType.STAFF:
        raise ValidationError("Only STAFF accounts can be reactivated through Staff lifecycle operations.")
    if target_user.account_lifecycle_state == User.AccountLifecycleState.DISABLED:
        raise ValidationError("Globally disabled accounts require separate account enablement before reactivation.")
    if StaffRoleAssignment.objects.active().filter(user=target_user, role__family=StaffRole.CRM_ROLE_FAMILY).exists():
        raise ValidationError("The target already has active CRM staff access.")
    with transaction.atomic():
        if StaffInvitation.objects.select_for_update().filter(
            target_user=target_user,
            operation=StaffAdministratorProposal.Operation.REACTIVATE,
            accepted_at__isnull=True,
            revoked_at__isnull=True,
            superseded_at__isnull=True,
        ).exists():
            raise ValidationError({"invitation": "A current reactivation invitation already exists."})
        if role_code == StaffRole.CRM_ADMIN:
            from staff_access.administrator_governance import create_administrator_proposal
            return create_administrator_proposal(
                actor_user=actor_user,
                operation=StaffAdministratorProposal.Operation.REACTIVATE,
                target_user=target_user,
                reason=reason,
                request=request,
            )
        role = StaffRole.objects.get(code=role_code, family=StaffRole.CRM_ROLE_FAMILY, is_active=True)
        invitation = StaffInvitation.objects.create(
            intended_email=target_user.email,
            first_name=target_user.person.first_name,
            last_name=target_user.person.last_name,
            pending_role_codes=[role.code],
            target_user=target_user,
            operation=StaffAdministratorProposal.Operation.REACTIVATE,
            reason=reason,
            token_hash=None,
            expires_at=timezone.now() + timedelta(hours=settings.STAFF_INVITATION_EXPIRY_HOURS),
            created_by=actor_user,
        )
        _queue_staff_invitation_email(invitation)
        _lifecycle_audit(
            action=AuditEvent.Action.CAPABILITY_PERMISSION_MUTATED,
            actor_user=actor_user,
            target_user=target_user,
            event_type="STAFF_REACTIVATION_INVITATION_ISSUED",
            reason=reason,
            metadata={"invitation_id": str(invitation.public_id), "requested_role": role.code},
            request=request,
        )
        return invitation


def cancel_staff_invitation(*, invitation, actor_user, reason=""):
    from staff_access.authorization import authorize_staff_operation, StaffOperation
    authorize_staff_operation(actor=actor_user, operation=StaffOperation.ACCESS)
    if not str(reason or "").strip():
        raise ValidationError({"reason": "A reason is required to cancel an invitation."})
    from notifications.models import TransactionalEmailJob
    with transaction.atomic():
        invitation = StaffInvitation.objects.select_for_update().get(pk=invitation.pk)
        if invitation.target_user_id and (invitation.target_user_id == actor_user.pk or invitation.target_user.is_staff or invitation.target_user.is_superuser):
            raise ValidationError("This invitation cannot be managed by the current administrator.")
        job = TransactionalEmailJob.objects.select_for_update().filter(staff_invitation=invitation).first()
        if job and job.status in (TransactionalEmailJob.Status.PENDING, TransactionalEmailJob.Status.PROCESSING):
            job.status = TransactionalEmailJob.Status.CANCELLED
            job.completed_at = timezone.now()
            job.last_error_code = "INVITATION_CANCELLED"
            job.save(update_fields=["status", "completed_at", "last_error_code", "updated_at"])
        invitation.revoked_at = timezone.now()
        invitation.save(update_fields=["revoked_at", "updated_at"])
        target = invitation.target_user
        if target is not None:
            _lifecycle_audit(
                action=AuditEvent.Action.CAPABILITY_PERMISSION_MUTATED,
                actor_user=actor_user,
                target_user=target,
                event_type="STAFF_INVITATION_CANCELLED",
                reason=str(reason).strip(),
                metadata={"invitation_id": str(invitation.public_id)},
            )
    return invitation


def request_staff_email_change(*, actor_user, user_id, requested_email, expiry_hours=24):
    from accounts.models import User
    from staff_access.authorization import authorize_staff_operation, StaffOperation

    requested_email = _normalize_invitation_email(requested_email)
    target = User.objects.select_related("person").get(pk=user_id)
    authorize_staff_operation(
        actor=actor_user,
        target=target,
        operation=StaffOperation.ADMIN_EMAIL_CHANGE,
    )
    if target.person.record_type != "STAFF":
        raise ValidationError({"user": "Only STAFF accounts may use the Staff email workflow."})
    if User.objects.filter(email=requested_email).exclude(pk=target.pk).exists():
        raise ValidationError({"email": "An account already uses this email address."})
    with transaction.atomic():
        StaffEmailChangeRequest.objects.filter(
            user=target, verified_at__isnull=True, superseded_at__isnull=True,
        ).update(superseded_at=timezone.now())
        change = StaffEmailChangeRequest.objects.create(
            user=target,
            requested_email=requested_email,
            token_hash=None,
            expires_at=timezone.now() + timedelta(hours=expiry_hours),
            created_by=actor_user,
        )
        from django.conf import settings
        from notifications.models import TransactionalEmailJob
        TransactionalEmailJob.objects.create(
            staff_email_change_request=change,
            template_id=settings.BREVO_STAFF_EMAIL_CHANGE_TEMPLATE_ID,
            recipient_email=change.requested_email,
            recipient_name=f"{target.person.first_name} {target.person.last_name}".strip(),
            first_name=target.person.first_name,
            expires_in_hours=max(1, expiry_hours),
            job_type=TransactionalEmailJob.JobType.STAFF_EMAIL_CHANGE,
        )
    return change


def confirm_staff_email_change(*, request_id, raw_token):
    from accounts.models import User
    from accounts.revocation import revoke_user_credentials
    from audit.models import AuditEvent
    from audit.services import record_audit_event

    with transaction.atomic():
        change = StaffEmailChangeRequest.objects.select_for_update().select_related("user").get(pk=request_id)
        if (
            change.verified_at is not None
            or change.superseded_at is not None
            or change.expires_at <= timezone.now()
            or not change.token_hash
            or not secrets.compare_digest(change.token_hash, _invitation_token_hash(raw_token))
        ):
            raise ValidationError("This email change request is invalid or has expired.")
        user = User.objects.select_for_update().select_related("person").get(pk=change.user_id)
        if User.objects.filter(email=change.requested_email).exclude(pk=user.pk).exists():
            raise ValidationError({"email": "An account already uses this email address."})
        previous_email = user.email
        user.email = change.requested_email
        user.person.primary_email = change.requested_email
        user.person.save(update_fields=["primary_email", "updated_at"])
        user.save(update_fields=["email"])
        revoke_user_credentials(user=user, reason="staff_email_changed", actor_user=change.created_by)
        change.verified_at = timezone.now()
        change.save(update_fields=["verified_at", "updated_at"])
        record_audit_event(
            action=AuditEvent.Action.PERSON_UPDATED,
            actor_user=change.created_by,
            entity_type="Person",
            entity_id=user.person_id,
            changes={"primary_email": {"from": previous_email, "to": user.email}},
            metadata={"source": "STAFF_EMAIL_VERIFICATION"},
        )
        return user


def inspect_staff_invitation(*, public_id, raw_token):
    invitation = StaffInvitation.objects.select_related("target_user").filter(public_id=public_id).first()
    if invitation is None or invitation.accepted_at or invitation.revoked_at or invitation.superseded_at or invitation.expires_at <= timezone.now() or not invitation.token_hash or not secrets.compare_digest(invitation.token_hash, _invitation_token_hash(raw_token)):
        raise ValidationError("This Staff invitation is invalid or has expired.")
    target = invitation.target_user
    existing_account = bool(target and target.is_active)
    return {
        "usable": True,
        "existing_account": existing_account,
        "role_code": invitation.pending_role_codes[0] if len(invitation.pending_role_codes) == 1 else None,
        "expires_at": invitation.expires_at,
    }


def redeem_staff_invitation(*, public_id, raw_token, password=None, authenticated_user=None):
    """Atomically consume an unexpired invitation and provision its User."""
    from accounts.models import User
    from audit.models import AuditEvent
    from audit.services import record_audit_event
    from people.models import Person

    if password is not None:
        validate_password(password)
    with transaction.atomic():
        invitation = StaffInvitation.objects.select_for_update().get(public_id=public_id)
        if (
            invitation.accepted_at is not None
            or invitation.revoked_at is not None
            or invitation.superseded_at is not None
            or invitation.expires_at <= timezone.now()
            or not invitation.token_hash
            or not secrets.compare_digest(invitation.token_hash, _invitation_token_hash(raw_token))
        ):
            raise ValidationError("This Staff invitation is invalid or has expired.")
        existing_user = User.objects.select_related("person").filter(pk=invitation.target_user_id).first() if invitation.target_user_id else User.objects.select_related("person").filter(email=invitation.intended_email).first()
        if existing_user is not None and existing_user.person.record_type != Person.RecordType.STAFF:
            raise ValidationError({"email": "An active or non-Staff account already uses this email address."})
        if existing_user is not None and existing_user.is_active:
            if authenticated_user is None or authenticated_user.pk != existing_user.pk or not authenticated_user.is_authenticated:
                raise ValidationError("Sign in with the invited account before accepting this invitation.")
            password = None
        if existing_user is not None and not existing_user.is_active and password is None:
            raise ValidationError({"password": "A new or pending account must establish a password."})
        if existing_user is None:
            person = Person.objects.create(
                record_type=Person.RecordType.STAFF,
                first_name=invitation.first_name,
                last_name=invitation.last_name,
                primary_email=invitation.intended_email,
            )
            user = User.objects.create_user(email=invitation.intended_email, password=None, person=person, is_active=False, account_lifecycle_state=User.AccountLifecycleState.PENDING_ACTIVATION)
            user.set_unusable_password()
            user.save(update_fields=["password", "is_active", "account_lifecycle_state"])
        else:
            user = existing_user
            person = user.person
            user = User.objects.select_for_update().get(pk=existing_user.pk)
            if user.account_lifecycle_state == User.AccountLifecycleState.DISABLED:
                raise ValidationError("A disabled account cannot be reactivated through invitation acceptance.")
            if password is not None:
                user.set_password(password)
            user.is_active = True
            user.account_lifecycle_state = User.AccountLifecycleState.ACTIVE
            user.auth_version += 1
            user.crm_session_version += 1
            update_fields = ["is_active", "account_lifecycle_state", "auth_version", "crm_session_version"]
            if password is not None:
                update_fields.append("password")
            user.save(update_fields=update_fields)
        if len(invitation.pending_role_codes) != 1:
            raise ValidationError("Staff invitations must contain exactly one CRM role.")
        role = StaffRole.objects.get(code=invitation.pending_role_codes[0], is_active=True)
        administrator_proposal = None
        if role.code == StaffRole.CRM_ADMIN:
            from staff_access.models import StaffAdministratorProposal
            from staff_access.authorization import can_access_staff_management
            administrator_proposal = StaffAdministratorProposal.objects.select_for_update().filter(
                target_user=user,
                operation__in=(StaffAdministratorProposal.Operation.ONBOARD, StaffAdministratorProposal.Operation.REACTIVATE),
                status=StaffAdministratorProposal.Status.APPROVED,
                resulting_invitation=invitation,
            ).first()
            if administrator_proposal is None or administrator_proposal.expires_at <= timezone.now():
                raise ValidationError("This Administrator invitation is not backed by a valid approved proposal.")
            if not can_access_staff_management(administrator_proposal.proposed_by) or not can_access_staff_management(administrator_proposal.approved_by):
                raise ValidationError("The Administrator proposal no longer has eligible proposer and approver authority.")
        StaffRoleAssignment.objects.assign_role(
            user=user,
            role=role,
            assigned_by=invitation.created_by,
            audit=True,
        )
        if administrator_proposal is not None:
            from staff_access.administrator_governance import consume_administrator_proposal
            consume_administrator_proposal(
                proposal=administrator_proposal,
                actor_user=user,
                resulting_assignment=StaffRoleAssignment.objects.get(user=user, role=role),
                expected_operation=invitation.operation,
            )
        invitation.accepted_at = timezone.now()
        invitation.token_hash = None
        invitation.save(update_fields=["accepted_at", "token_hash", "updated_at"])
        record_audit_event(
            action=AuditEvent.Action.PERSON_CREATED,
            actor_user=invitation.created_by,
            entity_type="Person",
            entity_id=person.pk,
            changes={"created": {"from": False, "to": True}},
            metadata={"source": "STAFF_INVITATION_REDEEMED", "invitation_id": str(invitation.public_id)},
        )
        return user


def ensure_staff_role_can_be_deactivated(role):
    if role.code == StaffRole.CRM_ADMIN:
        raise FinalCrmAdminProtectionError()


def deactivate_staff_role(role, *, actor_user=None, request=None):
    """Deactivate a non-canonical-admin role through the Staff Access domain."""
    if actor_user is None:
        raise PermissionError("Staff role deactivation requires an attributable actor.")
    _require_staff_role_mutation_actor(actor_user)
    with transaction.atomic():
        policy_state = CapabilityPolicyState.objects.select_for_update().filter(
            singleton_key=1,
            key=CAPABILITY_POLICY_STATE_KEY,
        ).first()
        locked_roles = list(StaffRole.objects.select_for_update().order_by("pk"))
        locked_role = next(item for item in locked_roles if item.pk == role.pk)
        ensure_staff_role_can_be_deactivated(locked_role)
        affected_assignments = list(
            StaffRoleAssignment.objects.select_for_update()
            .filter(role=locked_role, is_active=True, revoked_at__isnull=True)
            .order_by("pk")
        )
        affected_user_ids = sorted({assignment.user_id for assignment in affected_assignments})
        affected_users = list(
            User.objects.select_for_update().filter(pk__in=affected_user_ids).order_by("pk")
        )
        locked_role.is_active = False
        locked_role.save(update_fields=["is_active", "updated_at"])
        if policy_state is not None:
            policy_state.version += 1
            policy_state.updated_by = actor_user
            policy_state.reason = "staff_role_deactivated"
            policy_state.save(update_fields=["version", "updated_by", "reason", "updated_at"])
        for user in affected_users:
            user.crm_session_version = F("crm_session_version") + 1
            user.save(update_fields=["crm_session_version"])
        request_id = None
        ip_address = None
        if request is not None:
            request_id = request.headers.get("X-Request-ID") or request.META.get("HTTP_X_REQUEST_ID")
            ip_address = request.META.get("REMOTE_ADDR")
        record_audit_event(
            action=AuditEvent.Action.STAFF_ROLE_REVOKED,
            actor_user=actor_user,
            entity_type="StaffRole",
            entity_id=locked_role.pk,
            metadata={
                "reason": "staff_role_deactivated",
                "staff_role_code": locked_role.code,
                "affected_user_count": len(affected_user_ids),
                "crm_sessions_invalidated": True,
                **({"policy_version": policy_state.version} if policy_state is not None else {}),
            },
            request_id=request_id,
            ip_address=ip_address,
        )
    return locked_role


def revoke_staff_role_assignments(*, assignments, revoked_by=None, revoked_at=None, request=None, audit=True):
    """Revoke active assignments without permitting loss of the final CRM admin.

    Locking the canonical CRM_ADMIN role first serializes all supported revoke
    operations. The selected assignment rows are then locked in primary-key
    order before the effective-admin count is re-evaluated.
    """
    if revoked_by is None:
        raise PermissionError("Staff role revocation requires an attributable actor.")
    if not audit:
        raise PermissionError("Staff role revocation auditing cannot be disabled.")
    _require_staff_role_mutation_actor(revoked_by)

    if hasattr(assignments, "values_list"):
        assignment_ids = assignments.values_list("pk", flat=True)
    else:
        assignment_ids = [assignment.pk for assignment in assignments if assignment.pk]

    with transaction.atomic():
        policy_state = CapabilityPolicyState.objects.select_for_update().filter(
            singleton_key=1,
            key=CAPABILITY_POLICY_STATE_KEY,
        ).first()
        crm_admin_role = StaffRole.objects.select_for_update().get(code=StaffRole.CRM_ADMIN)
        locked_assignments = list(
            StaffRoleAssignment.objects.select_for_update()
            .select_related("role")
            .filter(pk__in=assignment_ids)
            .order_by("pk")
        )
        active_assignments = [
            assignment
            for assignment in locked_assignments
            if assignment.is_active and assignment.revoked_at is None
        ]
        effective_admin_revocations = [
            assignment
            for assignment in active_assignments
            if assignment.role_id == crm_admin_role.id and assignment.role.is_active
        ]

        if effective_admin_revocations:
            active_admin_count = StaffRoleAssignment.objects.active().filter(
                role=crm_admin_role,
                user__is_active=True,
                user__person__archived_at__isnull=True,
            ).count()
            if active_admin_count <= len(effective_admin_revocations):
                raise FinalCrmAdminProtectionError()

        for assignment in active_assignments:
            assignment._revoke_unchecked(revoked_by=revoked_by, revoked_at=revoked_at)

        affected_user_ids = sorted({assignment.user_id for assignment in active_assignments})
        affected_users = list(
            User.objects.select_for_update().filter(pk__in=affected_user_ids).order_by("pk")
        )
        for user in affected_users:
            user.crm_session_version = F("crm_session_version") + 1
            user.save(update_fields=["crm_session_version"])

        if active_assignments:
            if policy_state is not None:
                policy_state.version += 1
                policy_state.updated_by = revoked_by
                policy_state.reason = "staff_role_assignments_revoked"
                policy_state.save(update_fields=["version", "updated_by", "reason", "updated_at"])

        if audit:
            request_id = None
            ip_address = None
            if request is not None:
                request_id = request.headers.get("X-Request-ID") or request.META.get("HTTP_X_REQUEST_ID")
                ip_address = request.META.get("REMOTE_ADDR")
            for assignment in active_assignments:
                record_audit_event(
                    action=AuditEvent.Action.STAFF_ROLE_REVOKED,
                    actor_user=revoked_by,
                    entity_type="StaffRoleAssignment",
                    entity_id=assignment.id,
                    changes={"is_active": {"from": True, "to": False}},
                    metadata={
                        "target_user_id": str(assignment.user_id),
                        "staff_role_id": str(assignment.role_id),
                        "staff_role_code": assignment.role.code,
                        **({"policy_version": policy_state.version} if policy_state is not None else {}),
                    },
                    request_id=request_id,
                    ip_address=ip_address,
                )

    return active_assignments
