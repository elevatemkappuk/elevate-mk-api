import hashlib
import hmac
from dataclasses import dataclass

from django.contrib.auth import get_user_model, password_validation
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from audit.models import AuditEvent
from audit.services import record_audit_event
from community.models import CommunityAccountInvitation
from memberships.models import Membership
from notifications.models import TransactionalEmailJob
from people.models import Person
from accounts.models import normalize_email_address


INVALID_ACTIVATION_CODE = "INVALID_OR_EXPIRED_ACTIVATION"
INVALID_ACTIVATION_DETAIL = "This activation link is invalid or has expired."
ACCOUNT_SETUP_UNAVAILABLE_CODE = "ACCOUNT_SETUP_UNAVAILABLE"
ACCOUNT_SETUP_UNAVAILABLE_DETAIL = "Account setup is unavailable. Please contact support."
PASSWORD_VALIDATION_CODE = "PASSWORD_VALIDATION_ERROR"


class InvalidCommunityActivation(Exception):
    pass


class CommunityAccountSetupUnavailable(Exception):
    pass


class CommunityPasswordValidationError(Exception):
    def __init__(self, messages):
        self.messages = list(messages)
        super().__init__("Password validation failed.")


@dataclass(frozen=True)
class CommunityActivationResult:
    user: object


def redeem_community_activation(*, invitation_id, raw_token, password):
    """Atomically redeem one invitation and create the linked Community User."""
    with transaction.atomic():
        # Keep invitation/job lock ordering aligned with the transactional
        # worker: invitation first, then its delivery job.
        invitation = CommunityAccountInvitation.objects.select_for_update().filter(pk=invitation_id).first()
        if invitation is None or not _token_matches(invitation, raw_token):
            raise InvalidCommunityActivation

        job = (
            TransactionalEmailJob.objects.select_for_update()
            .filter(invitation_id=invitation.id)
            .first()
        )
        if job is not None and job.status == TransactionalEmailJob.Status.PROCESSING:
            raise CommunityAccountSetupUnavailable

        person = Person.objects.select_for_update().get(pk=invitation.person_id)
        membership = Membership.objects.select_for_update().filter(
            person=person,
            status=Membership.Status.ACTIVE,
        ).first()
        if not _invitation_is_eligible(invitation=invitation, person=person, membership=membership):
            raise InvalidCommunityActivation

        user_model = get_user_model()
        existing_user = user_model.objects.select_for_update().filter(person_id=person.id).first()
        if existing_user is not None:
            raise CommunityAccountSetupUnavailable

        intended_email = normalize_email_address(invitation.intended_email)
        person_email = normalize_email_address(person.primary_email or "")
        if not intended_email or intended_email != person_email:
            raise CommunityAccountSetupUnavailable
        if user_model.objects.filter(email=intended_email).exists():
            raise CommunityAccountSetupUnavailable

        user = user_model(
            email=intended_email,
            person=person,
            is_active=True,
            is_staff=False,
            is_superuser=False,
        )
        try:
            password_validation.validate_password(password, user)
        except DjangoValidationError as error:
            raise CommunityPasswordValidationError(error.messages) from error

        user.set_password(password)
        try:
            user.save(force_insert=True)
        except IntegrityError as error:
            raise CommunityAccountSetupUnavailable from error

        invitation.used_at = timezone.now()
        invitation.save(update_fields=["used_at", "updated_at"])
        _stop_pending_delivery(job)
        record_audit_event(
            action=AuditEvent.Action.COMMUNITY_ACCOUNT_ACTIVATED,
            actor_user=user,
            entity_type="User",
            entity_id=user.id,
            metadata={"source": "COMMUNITY_ACTIVATION", "person_id": str(person.id)},
        )

    return CommunityActivationResult(user=user)


def _token_matches(invitation, raw_token):
    if not invitation.token_hash or not isinstance(raw_token, str):
        return False
    supplied_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    return hmac.compare_digest(invitation.token_hash, supplied_hash)


def _invitation_is_eligible(*, invitation, person, membership):
    return (
        invitation.is_current
        and invitation.expires_at > timezone.now()
        and person.record_type == Person.RecordType.BUSINESS
        and person.archived_at is None
        and membership is not None
    )


def _stop_pending_delivery(job):
    if job is None:
        return
    if job.status in {
        TransactionalEmailJob.Status.PENDING,
        TransactionalEmailJob.Status.PROCESSING,
    }:
        job.status = TransactionalEmailJob.Status.CANCELLED
        job.completed_at = timezone.now()
        job.locked_at = None
        job.last_error_code = "ACTIVATION_REDEEMED"
        job.last_error_message = "Activation was completed before delivery processing finished."
        job.save(update_fields=["status", "completed_at", "locked_at", "last_error_code", "last_error_message", "updated_at"])
