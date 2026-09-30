import logging
import signal
from threading import Event

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections

from brevo_marketing.jobs import (
    _validated_batch_size as _validated_marketing_batch_size,
    process_brevo_sync_jobs,
)
from notifications.jobs import (
    _validated_batch_size as _validated_transactional_batch_size,
    process_transactional_email_jobs,
)


logger = logging.getLogger(__name__)
WORK_SLEEP_SECONDS = 0.1


def run_background_worker(*, poll_seconds=None, transactional_batch_size=None, marketing_batch_size=None, stop_event=None, sleep_fn=None):
    poll_seconds = _validated_poll_seconds(poll_seconds)
    transactional_batch_size = _validated_transactional_batch_size(transactional_batch_size)
    marketing_batch_size = _validated_marketing_batch_size(marketing_batch_size)
    stop_event = stop_event or Event()

    while not stop_event.is_set():
        did_work = False

        try:
            transactional_results = process_transactional_email_jobs(limit=transactional_batch_size)
            did_work = did_work or bool(transactional_results)
            logger.info("Background worker transactional batch complete. jobs=%s", len(transactional_results))
        except Exception:
            logger.exception("Background worker transactional batch failed; continuing with marketing batch.")
        finally:
            close_old_connections()

        try:
            marketing_results = process_brevo_sync_jobs(limit=marketing_batch_size)
            did_work = did_work or bool(marketing_results)
            logger.info("Background worker Brevo marketing batch complete. jobs=%s", len(marketing_results))
        except Exception:
            logger.exception("Background worker Brevo marketing batch failed; continuing.")
        finally:
            close_old_connections()

        if sleep_fn is not None:
            sleep_fn(WORK_SLEEP_SECONDS if did_work else poll_seconds)
        elif did_work:
            stop_event.wait(WORK_SLEEP_SECONDS)
        else:
            stop_event.wait(poll_seconds)


def _validated_poll_seconds(value):
    if value is None:
        value = settings.BACKGROUND_WORKER_POLL_SECONDS
    try:
        value = float(value)
    except (TypeError, ValueError):
        value = 3.0
    return value if value > 0 else 3.0


class Command(BaseCommand):
    help = "Process transactional-email and Brevo marketing queues in one background worker."

    def add_arguments(self, parser):
        parser.add_argument("--watch", action="store_true")
        parser.add_argument("--poll-seconds", type=float, default=None)
        parser.add_argument("--transactional-batch-size", type=int, default=None)
        parser.add_argument("--marketing-batch-size", type=int, default=None)

    def handle(self, *args, **options):
        if not options["watch"]:
            raise CommandError("The combined background worker requires --watch.")
        if options["poll_seconds"] is not None and options["poll_seconds"] <= 0:
            raise CommandError("--poll-seconds must be greater than zero.")
        if options["transactional_batch_size"] is not None and options["transactional_batch_size"] <= 0:
            raise CommandError("--transactional-batch-size must be greater than zero.")
        if options["marketing_batch_size"] is not None and options["marketing_batch_size"] <= 0:
            raise CommandError("--marketing-batch-size must be greater than zero.")

        stop_event = Event()
        previous_handlers = {}

        def request_shutdown(signum, frame):
            if not stop_event.is_set():
                self.stdout.write("Combined background worker shutdown requested.")
                stop_event.set()

        for signal_name in ("SIGINT", "SIGTERM"):
            signal_value = getattr(signal, signal_name, None)
            if signal_value is not None:
                previous_handlers[signal_value] = signal.getsignal(signal_value)
                signal.signal(signal_value, request_shutdown)

        try:
            run_background_worker(
                poll_seconds=options["poll_seconds"],
                transactional_batch_size=options["transactional_batch_size"],
                marketing_batch_size=options["marketing_batch_size"],
                stop_event=stop_event,
            )
        except KeyboardInterrupt:
            stop_event.set()
        finally:
            close_old_connections()
            for signal_value, handler in previous_handlers.items():
                signal.signal(signal_value, handler)
        self.stdout.write("Combined background worker stopped.")
