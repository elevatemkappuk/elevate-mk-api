import signal
from threading import Event

from django.core.management.base import BaseCommand, CommandError

from brevo_marketing.jobs import (
    _validated_batch_size,
    _validated_poll_seconds,
    process_brevo_sync_jobs,
    run_brevo_sync_worker,
)


class Command(BaseCommand):
    help = "Process pending durable one-Person Brevo Marketing synchronization jobs."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=10, help="Maximum number of jobs to process.")
        parser.add_argument("--watch", action="store_true", help="Keep polling and processing Brevo jobs until stopped.")
        parser.add_argument("--poll-seconds", type=float, default=None, help="Watch-mode idle polling interval.")
        parser.add_argument("--batch-size", type=int, default=None, help="Watch-mode maximum jobs per polling batch.")

    def handle(self, *args, **options):
        if options["watch"]:
            return self._handle_watch(options)

        limit = max(1, min(options["limit"], 100))
        results = process_brevo_sync_jobs(limit=limit)
        for result in results:
            line = f"Job ID: {result.job_id}; Status: {result.status}; Attempts: {result.attempts}"
            if result.outcome:
                line += f"; Outcome: {result.outcome}"
            if result.error_code:
                line += f"; Error: {result.error_code}"
            self.stdout.write(line)
        self.stdout.write(f"Processed jobs: {len(results)}")

    def _handle_watch(self, options):
        poll_seconds = options["poll_seconds"]
        batch_size = options["batch_size"]
        if poll_seconds is not None and poll_seconds <= 0:
            raise CommandError("--poll-seconds must be greater than zero.")
        if batch_size is not None and batch_size <= 0:
            raise CommandError("--batch-size must be greater than zero.")

        poll_seconds = _validated_poll_seconds(poll_seconds)
        batch_size = _validated_batch_size(batch_size)

        stop_event = Event()
        previous_handlers = {}

        def request_shutdown(signum, frame):
            if not stop_event.is_set():
                self.stdout.write("Brevo sync worker shutdown requested.")
                stop_event.set()

        for signal_name in ("SIGINT", "SIGTERM"):
            signal_value = getattr(signal, signal_name, None)
            if signal_value is not None:
                previous_handlers[signal_value] = signal.getsignal(signal_value)
                signal.signal(signal_value, request_shutdown)

        self.stdout.write(
            "Starting Brevo sync worker; provider=BREVO; "
            f"poll_seconds={poll_seconds}; batch_size={batch_size}"
        )
        try:
            run_brevo_sync_worker(
                poll_seconds=poll_seconds,
                batch_size=batch_size,
                stop_event=stop_event,
                on_result=self._write_result,
            )
        except KeyboardInterrupt:
            stop_event.set()
        finally:
            for signal_value, handler in previous_handlers.items():
                signal.signal(signal_value, handler)
        self.stdout.write("Brevo sync worker stopped.")

    def _write_result(self, result):
        line = f"Job ID: {result.job_id}; Status: {result.status}; Attempts: {result.attempts}"
        if result.outcome:
            line += f"; Outcome: {result.outcome}"
        if result.error_code:
            line += f"; Error: {result.error_code}"
        self.stdout.write(line)
