from importlib import import_module

from django.test.runner import DiscoverRunner


class ElevateTestRunner(DiscoverRunner):
    """Discover all project test-module naming conventions and restore seed data."""

    def build_suite(self, test_labels=None, extra_tests=None, **kwargs):
        # Keep Django's normal tag filtering, ordering, sharding, and parallel
        # execution. The default discovery label is marked so the loader can
        # add the legacy suffix convention before those steps are applied.
        self._include_legacy_suffix_tests = not test_labels
        try:
            return super().build_suite(test_labels=test_labels, extra_tests=extra_tests, **kwargs)
        finally:
            self._include_legacy_suffix_tests = False

    def load_tests_for_label(self, label, discover_kwargs):
        tests = super().load_tests_for_label(label, discover_kwargs)
        if not getattr(self, "_include_legacy_suffix_tests", False) or label != ".":
            return tests

        suffix_kwargs = discover_kwargs.copy()
        suffix_kwargs["pattern"] = "*_tests.py"
        with self.load_with_patterns():
            suffix_tests = self.test_loader.discover(start_dir=label, **suffix_kwargs)
        self.test_loader._top_level_dir = None
        tests.addTests(suffix_tests)
        return tests

    def setup_databases(self, **kwargs):
        config = super().setup_databases(**kwargs)
        from staff_access.models import StaffRole
        from professional_profiles.models import Industry
        from professional_profiles.taxonomy import CANONICAL_INDUSTRIES
        from tags.models import Tag
        from skills.models import Skill
        from interests.models import Interest

        canonical_tags = import_module("tags.migrations.0002_seed_tags").CANONICAL_TAGS
        canonical_skills = import_module("skills.migrations.0002_seed_skills").CANONICAL_SKILLS
        canonical_interests = import_module("interests.migrations.0002_seed_interests").CANONICAL_INTERESTS

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
        for tag in canonical_tags:
            Tag.objects.update_or_create(
                slug=tag["slug"],
                defaults={
                    "name": tag["name"],
                    "display_order": tag["display_order"],
                    "is_active": True,
                },
            )
        for skill in canonical_skills:
            Skill.objects.update_or_create(
                slug=skill["slug"],
                defaults={
                    "name": skill["name"],
                    "display_order": skill["display_order"],
                    "is_active": True,
                },
            )
        for interest in canonical_interests:
            Interest.objects.update_or_create(
                slug=interest["slug"],
                defaults={
                    "name": interest["name"],
                    "display_order": interest["display_order"],
                    "is_active": True,
                },
            )
        return config
