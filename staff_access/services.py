import hashlib
import secrets
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.contrib.auth.password_validation import validate_password
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from accounts.models import User
from audit.models import AuditEvent
from audit.services import record_audit_event
from staff_access.exceptions import FinalCrmAdminProtectionError
from staff_access.models import StaffEmailChangeRequest, StaffInvitation, StaffRole, StaffRoleAssignment


def _normalize_invitation_email(email):
    return str(email or "").strip().lower()


def _invitation_token_hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_staff_invitation(*, actor_user, email, first_name, last_name, role_codes, expiry_hours=72):
    """Create a pending Staff identity without creating a User yet."""
    from accounts.models import User
    from people.models import Person
    from staff_access.permissions import user_has_any_active_staff_role

    if not user_has_any_active_staff_role(actor_user, (StaffRole.CRM_ADMIN,)):
        raise PermissionError("Only an active CRM_ADMIN may invite Staff.")
    email = _normalize_invitation_email(email)
    if not email:
        raise ValidationError({"email": "An email address is required."})
    if User.objects.filter(email=email).exists():
        raise ValidationError({"email": "An account already uses this email address."})
    allowed_codes = StaffRole.canonical_codes()
    role_codes = list(dict.fromkeys(role_codes or []))
    if not role_codes or not set(role_codes).issubset(allowed_codes):
        raise ValidationError({"role_codes": "Only canonical CRM roles may be invited."})
    if Person.objects.filter(record_type=Person.RecordType.STAFF, primary_email__iexact=email, user__isnull=True).exists():
        raise ValidationError({"email": "A pending Staff identity already uses this email address."})

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


def resend_staff_invitation(*, invitation, actor_user):
    from staff_access.permissions import user_has_any_active_staff_role
    if not user_has_any_active_staff_role(actor_user, (StaffRole.CRM_ADMIN,)):
        raise PermissionError("Only an active CRM_ADMIN may resend Staff invitations.")
    if invitation.accepted_at or invitation.revoked_at:
        raise ValidationError("Only a pending Staff invitation can be resent.")
    with transaction.atomic():
        invitation = StaffInvitation.objects.select_for_update().get(pk=invitation.pk)
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
            token_hash=None,
            expires_at=timezone.now() + timedelta(hours=72),
            created_by=actor_user,
        )
        _queue_staff_invitation_email(replacement)
    return replacement


def update_pending_staff_invitation_roles(*, invitation, actor_user, role_codes):
    from staff_access.permissions import user_has_any_active_staff_role
    if not user_has_any_active_staff_role(actor_user, (StaffRole.CRM_ADMIN,)):
        raise PermissionError("Only an active CRM_ADMIN may update pending Staff roles.")
    role_codes = list(dict.fromkeys(role_codes or []))
    if not role_codes or not set(role_codes).issubset(StaffRole.canonical_codes()):
        raise ValidationError({"role_codes": "Only canonical CRM roles may be assigned."})
    if invitation.accepted_at or invitation.revoked_at or invitation.superseded_at:
        raise ValidationError("Only a current pending Staff invitation can be updated.")
    with transaction.atomic():
        invitation = StaffInvitation.objects.select_for_update().get(pk=invitation.pk)
        invitation.pending_role_codes = role_codes
        invitation.token_hash = None
        invitation.expires_at = timezone.now() + timedelta(hours=72)
        invitation.save(update_fields=["pending_role_codes", "token_hash", "expires_at", "updated_at"])
        from notifications.models import TransactionalEmailJob
        old_job = TransactionalEmailJob.objects.select_for_update().filter(staff_invitation=invitation).first()
        if old_job and old_job.status in (TransactionalEmailJob.Status.PENDING, TransactionalEmailJob.Status.PROCESSING):
            old_job.status = TransactionalEmailJob.Status.CANCELLED
            old_job.completed_at = timezone.now()
            old_job.last_error_code = "INVITATION_ROLES_UPDATED"
            old_job.save(update_fields=["status", "completed_at", "last_error_code", "updated_at"])
        _queue_staff_invitation_email(invitation)
    return invitation


def offboard_staff_user(*, target_user, actor_user, request=None):
    from staff_access.permissions import user_has_any_active_staff_role
    from accounts.revocation import revoke_user_credentials
    if not user_has_any_active_staff_role(actor_user, (StaffRole.CRM_ADMIN,)):
        raise PermissionError("Only an active CRM_ADMIN may offboard Staff.")
    if target_user.person.record_type != "STAFF":
        raise ValidationError("Only STAFF accounts can be offboarded through Staff lifecycle operations.")
    assignments = StaffRoleAssignment.objects.active().filter(user=target_user)
    with transaction.atomic():
        revoke_staff_role_assignments(assignments=assignments, revoked_by=actor_user, request=request)
        locked = User.objects.select_for_update().get(pk=target_user.pk)
        if locked.is_active:
            locked.is_active = False
            locked.save(update_fields=["is_active"])
            revoke_user_credentials(user=locked, reason="staff_offboarded", actor_user=actor_user, request=request)
    return target_user


def reactivate_staff_user(*, target_user, actor_user):
    """Start a fresh invitation; never restores the old User or assignments."""
    if not user_has_any_active_staff_role(actor_user, (StaffRole.CRM_ADMIN,)):
        raise PermissionError("Only an active CRM_ADMIN may reactivate Staff.")
    if target_user.person.record_type != "STAFF":
        raise ValidationError("Only STAFF accounts can be reactivated through Staff lifecycle operations.")
    roles = list(StaffRoleAssignment.objects.filter(user=target_user).values_list("role__code", flat=True))
    return create_staff_invitation(
        actor_user=actor_user,
        email=target_user.email,
        first_name=target_user.person.first_name,
        last_name=target_user.person.last_name,
        role_codes=[code for code in roles if code in StaffRole.canonical_codes()],
    )


def cancel_staff_invitation(*, invitation, actor_user):
    from staff_access.permissions import user_has_any_active_staff_role

    if not user_has_any_active_staff_role(actor_user, (StaffRole.CRM_ADMIN,)):
        raise PermissionError("Only an active CRM_ADMIN may cancel Staff invitations.")
    from notifications.models import TransactionalEmailJob
    with transaction.atomic():
        invitation = StaffInvitation.objects.select_for_update().get(pk=invitation.pk)
        job = TransactionalEmailJob.objects.select_for_update().filter(staff_invitation=invitation).first()
        if job and job.status in (TransactionalEmailJob.Status.PENDING, TransactionalEmailJob.Status.PROCESSING):
            job.status = TransactionalEmailJob.Status.CANCELLED
            job.completed_at = timezone.now()
            job.last_error_code = "INVITATION_CANCELLED"
            job.save(update_fields=["status", "completed_at", "last_error_code", "updated_at"])
        invitation.revoked_at = timezone.now()
        invitation.save(update_fields=["revoked_at", "updated_at"])
    return invitation


def request_staff_email_change(*, actor_user, user_id, requested_email, expiry_hours=24):
    from accounts.models import User
    from staff_access.permissions import user_has_any_active_staff_role

    if not user_has_any_active_staff_role(actor_user, (StaffRole.CRM_ADMIN,)):
        raise PermissionError("Only an active CRM_ADMIN may request Staff email changes.")
    requested_email = _normalize_invitation_email(requested_email)
    target = User.objects.select_related("person").get(pk=user_id)
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


def redeem_staff_invitation(*, public_id, raw_token, password):
    """Atomically consume an unexpired invitation and provision its User."""
    from accounts.models import User
    from audit.models import AuditEvent
    from audit.services import record_audit_event
    from people.models import Person

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
        existing_user = User.objects.select_for_update().select_related("person").filter(email=invitation.intended_email).first()
        if existing_user is not None and (existing_user.is_active or existing_user.person.record_type != Person.RecordType.STAFF):
            raise ValidationError({"email": "An active or non-Staff account already uses this email address."})
        if existing_user is None:
            person = Person.objects.create(
                record_type=Person.RecordType.STAFF,
                first_name=invitation.first_name,
                last_name=invitation.last_name,
                primary_email=invitation.intended_email,
            )
            user = User.objects.create_user(email=invitation.intended_email, password=password, person=person)
        else:
            user = existing_user
            person = user.person
            user.set_password(password)
            user.is_active = True
            user.auth_version += 1
            user.crm_session_version += 1
            user.save(update_fields=["password", "is_active", "auth_version", "crm_session_version"])
        for role_code in invitation.pending_role_codes:
            role = StaffRole.objects.get(code=role_code, is_active=True)
            StaffRoleAssignment.objects.assign_role(user=user, role=role, assigned_by=invitation.created_by)
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
    with transaction.atomic():
        locked_role = StaffRole.objects.select_for_update().get(pk=role.pk)
        ensure_staff_role_can_be_deactivated(locked_role)
        affected_user_ids = list(
            StaffRoleAssignment.objects.active()
            .filter(role=locked_role)
            .values_list("user_id", flat=True)
        )
        locked_role.is_active = False
        locked_role.save(update_fields=["is_active", "updated_at"])
        User.objects.filter(pk__in=affected_user_ids).update(
            crm_session_version=F("crm_session_version") + 1,
        )
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
    if hasattr(assignments, "values_list"):
        assignment_ids = assignments.values_list("pk", flat=True)
    else:
        assignment_ids = [assignment.pk for assignment in assignments if assignment.pk]

    with transaction.atomic():
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
            active_admin_count = StaffRoleAssignment.objects.active().filter(role=crm_admin_role).count()
            if active_admin_count <= len(effective_admin_revocations):
                raise FinalCrmAdminProtectionError()

        for assignment in active_assignments:
            assignment._revoke_unchecked(revoked_by=revoked_by, revoked_at=revoked_at)

        User.objects.filter(pk__in={assignment.user_id for assignment in active_assignments}).update(
            crm_session_version=F("crm_session_version") + 1,
        )

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
                    },
                    request_id=request_id,
                    ip_address=ip_address,
                )

    return active_assignments
