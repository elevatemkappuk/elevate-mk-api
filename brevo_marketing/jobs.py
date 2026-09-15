from dataclasses import dataclass
from datetime import timedelta
import logging
import math
from threading import Event
import re

from django.db import transaction
from django.utils import timezone

from brevo_marketing.exceptions import (
    BrevoMarketingAccessError,
    BrevoMarketingAPIError,
    BrevoMarketingAuthenticationError,
    BrevoMarketingConfigurationError,
    BrevoMarketingError,
    BrevoMarketingIdentityConflictError,
    BrevoMarketingRateLimitError,
    BrevoMarketingTemporaryError,
    BrevoMarketingValidationError,
)
from brevo_marketing.routing import BREVO_PROVIDER
from brevo_marketing.sync import BrevoPersonSyncOutcome, synchronize_person_to_brevo
from external_references.models import ExternalPersonSyncJob


EMAIL_MARKETING_PREFERENCE_SYNC = "EMAIL_MARKETING_PREFERENCE"
STALE_PROCESSING_AFTER = timedelta(minutes=15)
RETRY_BACKOFF_MINUTES = (1, 5, 15, 60)
MAX_STORED_ERROR_LENGTH = 500
MAX_WORKER_BATCH_SIZE = 100

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BrevoJobProcessResult:
    job_id: int
    status: str
    outcome: str | None = None
    attempts: int = 0
    error_code: str | None = None


def process_brevo_sync_jobs(*, limit=10, client=None):
    results = []
    for _ in range(limit):
        result = process_next_brevo_sync_job(client=client)
        if result is None:
            break
        results.append(result)
    return results


def run_brevo_sync_worker(*, poll_seconds=None, batch_size=None, client=None, stop_event=None, sleep_fn=None, on_result=None):
    """Continuously process the durable BREVO queue until stop_event is set."""
    poll_seconds = _validated_poll_seconds(poll_seconds)
    batch_size = _validated_batch_size(batch_size)
    stop_event = stop_event or Event()

    while not stop_event.is_set():
        results = []
        try:
            results = process_brevo_sync_jobs(limit=batch_size, client=client)
        except Exception:
            logger.exception("Brevo sync worker batch failed; continuing.")

        for result in results:
            logger.info(
                "Brevo sync job processed. job_id=%s status=%s attempts=%s outcome=%s error_code=%s",
                result.job_id,
                result.status,
                result.attempts,
                result.outcome or "",
                result.error_code or "",
            )
            if on_result is not None:
                on_result(result)

        if not results:
            if sleep_fn is not None:
                sleep_fn(poll_seconds)
            else:
                stop_event.wait(poll_seconds)


def _validated_poll_seconds(value):
    if value is None:
        from django.conf import settings

        value = getattr(settings, "BREVO_SYNC_WORKER_POLL_SECONDS", 3.0)
    try:
        value = float(value)
    except (TypeError, ValueError):
        value = 3.0
    return value if math.isfinite(value) and value > 0 else 3.0


def _validated_batch_size(value):
    if value is None:
        from django.conf import settings

        value = getattr(settings, "BREVO_SYNC_WORKER_BATCH_SIZE", 20)
    try:
        value = int(value)
    except (TypeError, ValueError):
        value = 20
    return max(1, min(value, MAX_WORKER_BATCH_SIZE))


def process_next_brevo_sync_job(*, client=None):
    job = _claim_next_job()
    if job is None:
        return None

    try:
        result = synchronize_person_to_brevo(person_id=job.person_id, client=client)
    except BrevoMarketingTemporaryError as error:
        return _record_retry(job, _error_code(error), error)
    except BrevoMarketingError as error:
        return _record_failure(job, _error_code(error), error)
    except Exception as error:  # Keep an unexpected worker failure durable and safe.
        return _record_retry(job, "UNEXPECTED_SYNC_ERROR", error)

    if result.outcome == BrevoPersonSyncOutcome.RECONCILIATION_REQUIRED:
        return _record_failure(job, "BREVO_RECONCILIATION_REQUIRED", None)

    _mark_succeeded(job)
    return BrevoJobProcessResult(
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
        provider=BREVO_PROVIDER,
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
        provider=BREVO_PROVIDER,
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
    return BrevoJobProcessResult(job_id=job.id, status=ExternalPersonSyncJob.Status.PENDING, attempts=job.attempts, error_code=error_code)


@transaction.atomic
def _record_failure(job, error_code, error):
    job.status = ExternalPersonSyncJob.Status.FAILED
    job.completed_at = timezone.now()
    job.locked_at = None
    job.last_error_code = error_code
    job.last_error_message = _safe_error_message(error_code, error)
    job.save(update_fields=["status", "completed_at", "locked_at", "last_error_code", "last_error_message", "updated_at"])
    return BrevoJobProcessResult(job_id=job.id, status=ExternalPersonSyncJob.Status.FAILED, attempts=job.attempts, error_code=error_code)


def _error_code(error):
    if isinstance(error, BrevoMarketingConfigurationError):
        return "BREVO_CONFIGURATION"
    if isinstance(error, BrevoMarketingAuthenticationError):
        return "BREVO_AUTHENTICATION"
    if isinstance(error, BrevoMarketingAccessError):
        return "BREVO_ACCESS"
    if isinstance(error, BrevoMarketingValidationError):
        return "BREVO_VALIDATION"
    if isinstance(error, BrevoMarketingIdentityConflictError):
        return "BREVO_IDENTITY_CONFLICT"
    if isinstance(error, BrevoMarketingRateLimitError):
        return "BREVO_RATE_LIMIT"
    if isinstance(error, BrevoMarketingAPIError):
        return "BREVO_API"
    if isinstance(error, BrevoMarketingTemporaryError):
        return "BREVO_TEMPORARY"
    return "BREVO_FAILURE"


def _safe_error_message(error_code, error=None):
    messages = {
        "BREVO_CONFIGURATION": "Brevo marketing configuration is invalid.",
        "BREVO_AUTHENTICATION": "Brevo authentication failed.",
        "BREVO_ACCESS": "The configured Brevo marketing resource is inaccessible.",
        "BREVO_VALIDATION": "Brevo rejected the synchronization request.",
        "BREVO_IDENTITY_CONFLICT": "The Brevo contact identity conflicts with an existing CRM reference.",
        "BREVO_RECONCILIATION_REQUIRED": "Brevo contact identity requires manual reconciliation.",
        "BREVO_API": "Brevo rejected the marketing API request.",
        "BREVO_TEMPORARY": "Brevo was temporarily unavailable.",
        "BREVO_RATE_LIMIT": "Brevo rate-limited the synchronization request.",
        "BREVO_FAILURE": "Brevo synchronization failed.",
        "UNEXPECTED_SYNC_ERROR": "The synchronization worker encountered an unexpected failure.",
    }
    message = messages.get(error_code, "Brevo synchronization failed.")
    if error_code == "BREVO_VALIDATION" and isinstance(error, BrevoMarketingValidationError):
        message = str(error) or message
    message = re.sub(r"(?i)(authorization|api[\s_-]?key|access[\s_-]?token|secret|bearer)\s*[:=]\s*[^\s|;]+", r"\1=[redacted]", message)
    message = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[redacted-email]", message)
    return " ".join(message.split())[:MAX_STORED_ERROR_LENGTH]
