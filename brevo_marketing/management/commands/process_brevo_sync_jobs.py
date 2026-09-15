from django.core.management.base import BaseCommand

from brevo_marketing.jobs import process_brevo_sync_jobs


class Command(BaseCommand):
    help = "Process pending durable one-Person Brevo Marketing synchronization jobs."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=10, help="Maximum number of jobs to process.")

    def handle(self, *args, **options):
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
