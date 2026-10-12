import signal
from threading import Event

from django.core.management.base import BaseCommand, CommandError

from notifications.jobs import (
    _validated_batch_size,
    _validated_poll_seconds,
    process_transactional_email_jobs,
    run_transactional_email_worker,
)


class Command(BaseCommand):
    help = "Process pending Community transactional email jobs."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=10)
        parser.add_argument("--watch", action="store_true")
        parser.add_argument("--poll-seconds", type=float, default=None)
        parser.add_argument("--batch-size", type=int, default=None)

    def handle(self, *args, **options):
        if options["watch"]:
            return self._handle_watch(options)
        limit = max(1, min(options["limit"], 100))
        results = process_transactional_email_jobs(limit=limit)
        for result in results:
            self.stdout.write(
                f"Job ID: {result.job_id}; Status: {result.status}; Attempts: {result.attempts}; "
                f"Error: {result.error_code or ''}"
            )
        self.stdout.write(f"Processed jobs: {len(results)}")

    def _handle_watch(self, options):
        if options["poll_seconds"] is not None and options["poll_seconds"] <= 0:
            raise CommandError("--poll-seconds must be greater than zero.")
        if options["batch_size"] is not None and options["batch_size"] <= 0:
            raise CommandError("--batch-size must be greater than zero.")
        stop_event = Event()
        previous_handlers = {}

        def request_shutdown(signum, frame):
            stop_event.set()

        for signal_name in ("SIGINT", "SIGTERM"):
            signal_value = getattr(signal, signal_name, None)
            if signal_value is not None:
                previous_handlers[signal_value] = signal.getsignal(signal_value)
                signal.signal(signal_value, request_shutdown)
        try:
            run_transactional_email_worker(
                poll_seconds=_validated_poll_seconds(options["poll_seconds"]),
                batch_size=_validated_batch_size(options["batch_size"]),
                stop_event=stop_event,
            )
        except KeyboardInterrupt:
            stop_event.set()
        finally:
            for signal_value, handler in previous_handlers.items():
                signal.signal(signal_value, handler)
