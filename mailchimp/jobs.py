from dataclasses import dataclass
from datetime import timedelta
import re

from django.db import transaction
from django.utils import timezone

from external_references.models import ExternalPersonSyncJob
from mailchimp.exceptions import (
    MailchimpAPIError,
    MailchimpAuthenticationError,
    MailchimpAudienceAccessError,
    MailchimpConfigurationError,
    MailchimpPersonSyncError,
    MailchimpTemporaryError,
    MailchimpValidationError,
    MailchimpVerificationError,
)
from mailchimp.sync import synchronize_person_to_mailchimp


MAILCHIMP_PROVIDER = "MAILCHIMP"
EMAIL_MARKETING_PREFERENCE_SYNC = "EMAIL_MARKETING_PREFERENCE"
STALE_PROCESSING_AFTER = timedelta(minutes=15)
RETRY_BACKOFF_MINUTES = (1, 5, 15, 60)
MAX_STORED_ERROR_LENGTH = 500


@dataclass(frozen=True)
class MailchimpJobProcessResult:
    job_id: int
    status: str
    outcome: str | None = None
    attempts: int = 0
    error_code: str | None = None


def process_mailchimp_sync_jobs(*, limit=10, client=None):
    results = []
    for _ in range(limit):
        result = process_next_mailchimp_sync_job(client=client)
        if result is None:
            break
        results.append(result)
    return results


def process_next_mailchimp_sync_job(*, client=None):
    job = _claim_next_job()
    if job is None:
        return None

    try:
        result = synchronize_person_to_mailchimp(
            person_id=job.person_id,
            client=client,
        )
    except MailchimpTemporaryError as error:
        return _record_retry(job, "MAILCHIMP_TEMPORARY", error)
    except MailchimpVerificationError as error:
        return _record_failure(job, _error_code(error), error)
    except Exception as error:  # Keep an unexpected worker failure durable and safe.
        return _record_retry(job, "UNEXPECTED_SYNC_ERROR", error)

    _mark_succeeded(job)
    return MailchimpJobProcessResult(
        job_id=job.id,
        status=ExternalPersonSyncJob.Status.SUCCEEDED,
        outcome=result.outcome,
        attempts=job.attempts,
    )


@transaction.atomic
def _claim_next_job():
    now = timezone.now()
    stale_before = now - STALE_PROCESSING_AFTER
    ExternalPersonSyncJob.objects.filter(
        provider=MAILCHIMP_PROVIDER,
        job_type=EMAIL_MARKETING_PREFERENCE_SYNC,
        status=ExternalPersonSyncJob.Status.PROCESSING,
        locked_at__lt=stale_before,
    ).update(
        status=ExternalPersonSyncJob.Status.PENDING,
        locked_at=None,
        available_at=now,
        last_error_code="STALE_PROCESSING_RECOVERED",
        last_error_message="Recovered after an interrupted worker attempt.",
    )
    job = ExternalPersonSyncJob.objects.select_for_update().filter(
        provider=MAILCHIMP_PROVIDER,
        job_type=EMAIL_MARKETING_PREFERENCE_SYNC,
        status=ExternalPersonSyncJob.Status.PENDING,
        available_at__lte=now,
    ).order_by("available_at", "id").first()
    if job is None:
        return None
    job.status = ExternalPersonSyncJob.Status.PROCESSING
    job.attempts += 1
    job.locked_at = now
    job.last_error_code = None
    job.last_error_message = None
    job.save(update_fields=["status", "attempts", "locked_at", "last_error_code", "last_error_message", "updated_at"])
    return job


@transaction.atomic
def _mark_succeeded(job):
    job.status = ExternalPersonSyncJob.Status.SUCCEEDED
    job.completed_at = timezone.now()
    job.locked_at = None
    job.save(update_fields=["status", "completed_at", "locked_at", "updated_at"])


@transaction.atomic
def _record_retry(job, error_code, error):
    now = timezone.now()
    if job.attempts >= job.max_attempts:
        return _record_failure(job, error_code, error)
    delay = RETRY_BACKOFF_MINUTES[min(job.attempts - 1, len(RETRY_BACKOFF_MINUTES) - 1)]
    job.status = ExternalPersonSyncJob.Status.PENDING
    job.available_at = now + timedelta(minutes=delay)
    job.locked_at = None
    job.last_error_code = error_code
    job.last_error_message = _safe_error_message(error_code, error)
    job.save(update_fields=["status", "available_at", "locked_at", "last_error_code", "last_error_message", "updated_at"])
    return MailchimpJobProcessResult(
        job_id=job.id,
        status=ExternalPersonSyncJob.Status.PENDING,
        attempts=job.attempts,
        error_code=error_code,
    )


@transaction.atomic
def _record_failure(job, error_code, error):
    job.status = ExternalPersonSyncJob.Status.FAILED
    job.completed_at = timezone.now()
    job.locked_at = None
    job.last_error_code = error_code
    job.last_error_message = _safe_error_message(error_code, error)
    job.save(update_fields=["status", "completed_at", "locked_at", "last_error_code", "last_error_message", "updated_at"])
    return MailchimpJobProcessResult(
        job_id=job.id,
        status=ExternalPersonSyncJob.Status.FAILED,
        attempts=job.attempts,
        error_code=error_code,
    )


def _error_code(error):
    if isinstance(error, MailchimpConfigurationError):
        return "MAILCHIMP_CONFIGURATION"
    if isinstance(error, MailchimpAuthenticationError):
        return "MAILCHIMP_AUTHENTICATION"
    if isinstance(error, MailchimpAudienceAccessError):
        return "MAILCHIMP_AUDIENCE_ACCESS"
    if isinstance(error, MailchimpValidationError):
        return "MAILCHIMP_VALIDATION"
    if isinstance(error, MailchimpAPIError):
        return "MAILCHIMP_API"
    if isinstance(error, MailchimpPersonSyncError):
        return "PERSON_SYNC_CONFLICT"
    return "MAILCHIMP_FAILURE"


def _safe_error_message(error_code, error=None):
    messages = {
        "MAILCHIMP_CONFIGURATION": "Mailchimp integration configuration is invalid.",
        "MAILCHIMP_AUTHENTICATION": "Mailchimp authentication failed.",
        "MAILCHIMP_AUDIENCE_ACCESS": "The configured Mailchimp audience is inaccessible.",
        "MAILCHIMP_VALIDATION": "Mailchimp rejected the synchronization request.",
        "MAILCHIMP_API": "Mailchimp rejected the synchronization request.",
        "PERSON_SYNC_CONFLICT": "The provider identity conflicts with an existing CRM reference.",
        "MAILCHIMP_TEMPORARY": "Mailchimp was temporarily unavailable.",
        "UNEXPECTED_SYNC_ERROR": "The synchronization worker encountered an unexpected failure.",
        "STALE_PROCESSING_RECOVERED": "Recovered after an interrupted worker attempt.",
    }
    message = messages.get(error_code, "Mailchimp synchronization failed.")
    if error_code == "MAILCHIMP_VALIDATION" and isinstance(error, MailchimpValidationError):
        message = str(error) or message

    # MailchimpValidationError is already reduced to title/detail/field summaries by
    # the client. Keep this worker boundary defensive in case an exception is raised
    # by another caller or a provider message contains a secret-looking value.
    message = re.sub(
        r"(?i)(authorization|api[\s_-]?key|access[\s_-]?token|secret)\s*[:=]\s*[^\s|;]+",
        r"\1=[redacted]",
        message,
    )
    message = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[redacted-email]", message)
    return " ".join(message.split())[:MAX_STORED_ERROR_LENGTH]
