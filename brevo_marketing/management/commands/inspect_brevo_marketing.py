from django.core.management.base import BaseCommand, CommandError

from brevo_marketing.exceptions import BrevoMarketingError
from brevo_marketing.services import inspect_brevo_marketing_configuration


class Command(BaseCommand):
    help = "Verify read-only Brevo contacts access and inspect marketing metadata."

    def handle(self, *args, **options):
        try:
            result = inspect_brevo_marketing_configuration()
        except BrevoMarketingError as error:
            raise CommandError(str(error)) from error

        self.stdout.write(self.style.SUCCESS("Brevo marketing read-only verification succeeded."))
        self.stdout.write(f"Contact attributes: {len(result.attributes)}")
        for attribute in result.attributes:
            options = f" options={','.join(attribute.options)}" if attribute.options else ""
            self.stdout.write(
                f"- {attribute.name}: type={attribute.attribute_type or 'unknown'} "
                f"category={attribute.category or 'unknown'}{options}"
            )
        self.stdout.write(f"Contact lists: {len(result.lists)}")
        for contact_list in result.lists:
            subscribers = contact_list.total_subscribers if contact_list.total_subscribers is not None else "unavailable"
            blacklisted = contact_list.total_blacklisted if contact_list.total_blacklisted is not None else "unavailable"
            self.stdout.write(
                f"- ID {contact_list.list_id}: {contact_list.name or '[unnamed]'} "
                f"subscribers={subscribers} blacklisted={blacklisted}"
            )
