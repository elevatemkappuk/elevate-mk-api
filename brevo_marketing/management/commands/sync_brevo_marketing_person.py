from django.core.management.base import BaseCommand, CommandError

from brevo_marketing.exceptions import BrevoMarketingError
from brevo_marketing.sync import synchronize_person_to_brevo
from people.models import Person


class Command(BaseCommand):
    help = "Synchronize exactly one CRM Person to Brevo Marketing."

    def add_arguments(self, parser):
        parser.add_argument("person_id", type=int)

    def handle(self, *args, **options):
        person_id = options["person_id"]
        try:
            result = synchronize_person_to_brevo(person_id=person_id)
        except Person.DoesNotExist as error:
            raise CommandError(f"No CRM Person exists for ID {person_id}.") from error
        except BrevoMarketingError as error:
            raise CommandError(str(error)) from error

        self.stdout.write(f"Person ID: {result.person_id}")
        self.stdout.write(f"Outcome: {result.outcome}")
        if result.contact_id is not None:
            self.stdout.write(f"Brevo contact ID: {result.contact_id}")
        if result.reference_id is not None:
            self.stdout.write(f"External reference ID: {result.reference_id}")
        if result.provider_state:
            self.stdout.write(f"Provider state: {result.provider_state}")
        if result.reason:
            self.stdout.write(f"Reason: {result.reason}")
