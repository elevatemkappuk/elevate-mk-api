import hashlib
import logging
import math
import secrets
from dataclasses import dataclass
from datetime import timedelta
from threading import Event

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from community.models import CommunityAccountInvitation
from community.services import build_community_activation_url
from notifications.exceptions import TransactionalEmailConfigurationError, TransactionalEmailError
from notifications.models import TransactionalEmailJob
from notifications.services import send_transactional_email
from people.models import Person
from memberships.models import Membership


logger = logging.getLogger(__name__)

RETRY_BACKOFF_MINUTES = (1, 5, 15, 60)
MAX_WORKER_BATCH_SIZE = 100


@dataclass(frozen=True)
class TransactionalEmailJobResult:
    job_id: int
    status: str
    attempts: int = 0
    error_code: str | None = None
    provider_message_id: str | None = None


def process_transactional_email_jobs(*, limit=10):
    results = []
    for _ in range(limit):
        result = process_next_transactional_email_job()
        if result is None:
            break
        results.append(result)
    return results


def run_transactional_email_worker(*, poll_seconds=None, batch_size=None, stop_event=None, sleep_fn=None, on_result=None):
    poll_seconds = _validated_poll_seconds(poll_seconds)
    batch_size = _validated_batch_size(batch_size)
    stop_event = stop_event or Event()

    while not stop_event.is_set():
        results = []
        try:
            results = process_transactional_email_jobs(limit=batch_size)
        except Exception:
            logger.exception("Transactional email worker batch failed; continuing.")

        for result in results:
            logger.info(
                "Transactional email job processed. job_id=%s status=%s attempts=%s error_code=%s",
                result.job_id,
                result.status,
                result.attempts,
                result.error_code or "",
            )
            if on_result is not None:
                on_result(result)

        if not results:
            if sleep_fn is not None:
                sleep_fn(poll_seconds)
            else:
                stop_event.wait(poll_seconds)


def process_next_transactional_email_job():
    job = _claim_next_job()
    if job is None:
        return None

    try:
        prepared = _prepare_send(job.id)
    except Exception:
        logger.exception("Transactional email job preparation failed. job_id=%s", job.id)
        return _mark_delivery_uncertain(job.id, "PREPARATION_ERROR")

    if prepared is None:
        return _get_result(job.id)

    try:
        result = send_transactional_email(
            recipient_email=prepared["recipient_email"],
            recipient_name=prepared["recipient_name"],
            template_id=prepared["template_id"],
            template_params={
                "first_name": prepared["first_name"],
                "activation_url": prepared["activation_url"],
                "expires_in_hours": prepared["expires_in_hours"],
            },
        )
    except TransactionalEmailConfigurationError:
        return _record_definitive_failure(job.id, "TRANSACTIONAL_EMAIL_CONFIGURATION")
    except TransactionalEmailError:
        # The provider abstraction deliberately does not expose whether a
        # request reached Brevo. A generic provider error is therefore unsafe
        # to retry because the member may already have received the link.
        return _mark_delivery_uncertain(job.id, "TRANSACTIONAL_EMAIL_OUTCOME_UNCERTAIN")
    except Exception:
        return _mark_delivery_uncertain(job.id, "TRANSACTIONAL_EMAIL_OUTCOME_UNCERTAIN")

    return _mark_sent(job.id, getattr(result, "provider_message_id", None))


@transaction.atomic
def _claim_next_job():
    now = timezone.now()
    stale_before = now - timedelta(seconds=settings.TRANSACTIONAL_EMAIL_JOB_LEASE_SECONDS)
    TransactionalEmailJob.objects.filter(
        job_type=TransactionalEmailJob.JobType.COMMUNITY_ACTIVATION,
        status=TransactionalEmailJob.Status.PROCESSING,
        locked_at__lt=stale_before,
    ).update(
        status=TransactionalEmailJob.Status.DELIVERY_UNCERTAIN,
        completed_at=now,
        locked_at=None,
        last_error_code="STALE_PROCESSING",
        last_error_message="Processing lease expired; delivery outcome is uncertain.",
    )
    job = (
        TransactionalEmailJob.objects.select_for_update(skip_locked=True)
        .filter(
            job_type=TransactionalEmailJob.JobType.COMMUNITY_ACTIVATION,
            status=TransactionalEmailJob.Status.PENDING,
            available_at__lte=now,
        )
        .order_by("available_at", "id")
        .first()
    )
    if job is None:
        return None
    job.status = TransactionalEmailJob.Status.PROCESSING
    job.attempts += 1
    job.locked_at = now
    job.last_error_code = ""
    job.last_error_message = ""
    job.save(update_fields=["status", "attempts", "locked_at", "last_error_code", "last_error_message", "updated_at"])
    return job


@transaction.atomic
def _prepare_send(job_id):
    invitation_id = TransactionalEmailJob.objects.values_list("invitation_id", flat=True).get(pk=job_id)
    invitation = CommunityAccountInvitation.objects.select_for_update().get(pk=invitation_id)
    job = TransactionalEmailJob.objects.select_for_update().select_related("invitation__person").get(pk=job_id)
    person = Person.objects.select_for_update().get(pk=invitation.person_id)
    user = get_user_model().objects.select_for_update().filter(person_id=person.id).first()

    if not _eligible_for_activation(person=person, invitation=invitation, user=user):
        _cancel_job(job, "INVITATION_NOT_ELIGIBLE")
        return None

    now = timezone.now()
    raw_token = secrets.token_urlsafe(32)
    invitation.token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    invitation.expires_at = now + timedelta(hours=settings.COMMUNITY_ACTIVATION_EXPIRY_HOURS)
    invitation.save(update_fields=["token_hash", "expires_at", "updated_at"])
    return {
        "recipient_email": job.recipient_email,
        "recipient_name": job.recipient_name,
        "first_name": job.first_name,
        "template_id": job.template_id,
        "expires_in_hours": job.expires_in_hours,
        "activation_url": build_community_activation_url(invitation=invitation, token=raw_token),
    }


def _eligible_for_activation(*, person, invitation, user):
    if not invitation.is_current:
        return False
    if invitation.token_hash is not None and invitation.expires_at <= timezone.now():
        return False
    if person.record_type != Person.RecordType.BUSINESS or person.archived_at is not None:
        return False
    if not Membership.objects.filter(person=person, status=Membership.Status.ACTIVE).exists():
        return False
    if user is not None:
        return False
    return True


@transaction.atomic
def _mark_sent(job_id, provider_message_id):
    job = TransactionalEmailJob.objects.select_for_update().get(pk=job_id)
    now = timezone.now()
    job.status = TransactionalEmailJob.Status.SENT
    job.sent_at = now
    job.completed_at = now
    job.locked_at = None
    job.provider_message_id = (provider_message_id or "")[:255]
    job.save(update_fields=["status", "sent_at", "completed_at", "locked_at", "provider_message_id", "updated_at"])
    return TransactionalEmailJobResult(job.id, job.status, job.attempts, provider_message_id=job.provider_message_id or None)


@transaction.atomic
def _record_definitive_failure(job_id, error_code):
    job = TransactionalEmailJob.objects.select_for_update().get(pk=job_id)
    now = timezone.now()
    if job.attempts >= job.max_attempts:
        job.status = TransactionalEmailJob.Status.FAILED
        job.completed_at = now
    else:
        delay = RETRY_BACKOFF_MINUTES[min(job.attempts - 1, len(RETRY_BACKOFF_MINUTES) - 1)]
        job.status = TransactionalEmailJob.Status.PENDING
        job.available_at = now + timedelta(minutes=delay)
    job.locked_at = None
    job.last_error_code = error_code
    job.last_error_message = "Transactional email failed before provider acceptance."
    job.save(update_fields=["status", "available_at", "completed_at", "locked_at", "last_error_code", "last_error_message", "updated_at"])
    return TransactionalEmailJobResult(job.id, job.status, job.attempts, error_code=error_code)


@transaction.atomic
def _mark_delivery_uncertain(job_id, error_code):
    job = TransactionalEmailJob.objects.select_for_update().get(pk=job_id)
    job.status = TransactionalEmailJob.Status.DELIVERY_UNCERTAIN
    job.completed_at = timezone.now()
    job.locked_at = None
    job.last_error_code = error_code
    job.last_error_message = "Delivery outcome is uncertain; automatic retry is disabled."
    job.save(update_fields=["status", "completed_at", "locked_at", "last_error_code", "last_error_message", "updated_at"])
    return TransactionalEmailJobResult(job.id, job.status, job.attempts, error_code=error_code)


@transaction.atomic
def _cancel_job(job, error_code):
    job.status = TransactionalEmailJob.Status.CANCELLED
    job.completed_at = timezone.now()
    job.locked_at = None
    job.last_error_code = error_code
    job.last_error_message = "Activation invitation is no longer eligible for delivery."
    job.save(update_fields=["status", "completed_at", "locked_at", "last_error_code", "last_error_message", "updated_at"])


def _get_result(job_id):
    job = TransactionalEmailJob.objects.get(pk=job_id)
    return TransactionalEmailJobResult(job.id, job.status, job.attempts, error_code=job.last_error_code or None)


def _validated_poll_seconds(value):
    value = settings.TRANSACTIONAL_EMAIL_WORKER_POLL_SECONDS if value is None else value
    try:
        value = float(value)
    except (TypeError, ValueError):
        value = 3.0
    return value if math.isfinite(value) and value > 0 else 3.0


def _validated_batch_size(value):
    value = settings.TRANSACTIONAL_EMAIL_WORKER_BATCH_SIZE if value is None else value
    try:
        value = int(value)
    except (TypeError, ValueError):
        value = 20
    return max(1, min(value, MAX_WORKER_BATCH_SIZE))
