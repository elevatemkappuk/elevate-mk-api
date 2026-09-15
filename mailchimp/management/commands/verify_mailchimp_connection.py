from django.core.management.base import BaseCommand, CommandError

from mailchimp.exceptions import MailchimpVerificationError
from mailchimp.services import verify_mailchimp_connection


class Command(BaseCommand):
    help = "Verify read-only access to the configured Mailchimp audience."

    def handle(self, *args, **options):
        try:
            result = verify_mailchimp_connection()
        except MailchimpVerificationError as error:
            raise CommandError(str(error)) from error

        self.stdout.write(self.style.SUCCESS("Mailchimp audience verification succeeded."))
        self.stdout.write(f"Audience ID: {result.audience_id}")
        self.stdout.write(f"Audience name: {result.audience_name}")
        self.stdout.write(f"Member count: {result.member_count if result.member_count is not None else 'unavailable'}")
        self.stdout.write(
            f"Unsubscribe count: {result.unsubscribe_count if result.unsubscribe_count is not None else 'unavailable'}"
        )
        self.stdout.write(f"Cleaned count: {result.cleaned_count if result.cleaned_count is not None else 'unavailable'}")
