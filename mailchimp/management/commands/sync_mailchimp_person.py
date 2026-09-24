from django.core.management.base import BaseCommand, CommandError
from people.models import Person

from mailchimp.exceptions import MailchimpVerificationError
from mailchimp.sync import synchronize_person_to_mailchimp


class Command(BaseCommand):
    help = "Synchronize exactly one CRM BUSINESS Person to the configured Mailchimp Audience."

    def add_arguments(self, parser):
        parser.add_argument("person_id", type=int, help="CRM Person ID to synchronize.")

    def handle(self, *args, **options):
        person_id = options["person_id"]
        try:
            result = synchronize_person_to_mailchimp(person_id=person_id)
        except MailchimpVerificationError as error:
            raise CommandError(str(error)) from error
        except Person.DoesNotExist as error:
            raise CommandError(f"CRM Person {person_id} was not found.") from error

        self.stdout.write(f"Person ID: {result.person_id}")
        self.stdout.write(f"Outcome: {result.outcome}")
        if result.reason:
            self.stdout.write(f"Reason: {result.reason}")
        if result.member_id:
            self.stdout.write(f"Mailchimp member ID: {result.member_id}")
        if result.provider_status:
            self.stdout.write(f"Mailchimp status: {result.provider_status}")
        if result.reference_id:
            self.stdout.write(f"External reference ID: {result.reference_id}")
