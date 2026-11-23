from django.test.runner import DiscoverRunner


class ElevateTestRunner(DiscoverRunner):
    """Restore required reference rows when a preserved test DB lost seed data."""

    def setup_databases(self, **kwargs):
        config = super().setup_databases(**kwargs)
        from staff_access.models import StaffRole
        from professional_profiles.models import Industry
        from professional_profiles.taxonomy import CANONICAL_INDUSTRIES

        for code, name in StaffRole.CANONICAL_ROLES:
            StaffRole.objects.update_or_create(
                code=code,
                defaults={"name": name, "is_active": True},
            )
        for industry in CANONICAL_INDUSTRIES:
            Industry.objects.update_or_create(
                slug=industry["slug"],
                defaults={
                    "name": industry["name"],
                    "display_order": industry["display_order"],
                    "is_active": True,
                },
            )
        return config
