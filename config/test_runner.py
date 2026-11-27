from importlib import import_module

from django.apps import apps
from django.db import connections
from django.test.runner import DiscoverRunner, ParallelTestSuite
from django.conf import settings
from django.contrib.auth.hashers import reset_hashers


FAST_TEST_PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]


def _configure_fast_test_password_hasher():
    settings.PASSWORD_HASHERS = FAST_TEST_PASSWORD_HASHERS
    reset_hashers(setting="PASSWORD_HASHERS")


def _init_elevate_worker(*args, **kwargs):
    """Initialize a parallel worker, then apply test-only speed settings."""
    ParallelTestSuite.init_worker(*args, **kwargs)
    _configure_fast_test_password_hasher()


class ElevateParallelTestSuite(ParallelTestSuite):
    """Keep test-only settings consistent in forked and spawned workers."""

    init_worker = _init_elevate_worker


class ElevateTestRunner(DiscoverRunner):
    """Discover all project test-module naming conventions and restore seed data."""

    parallel_test_suite = ElevateParallelTestSuite

    def setup_test_environment(self, **kwargs):
        super().setup_test_environment(**kwargs)
        self._production_password_hashers = settings.PASSWORD_HASHERS
        _configure_fast_test_password_hasher()

    def teardown_test_environment(self, **kwargs):
        settings.PASSWORD_HASHERS = self._production_password_hashers
        reset_hashers(setting="PASSWORD_HASHERS")
        super().teardown_test_environment(**kwargs)

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
        aliases = kwargs.get("aliases", {})
        if self.keepdb and self.parallel > 1:
            raise RuntimeError(
                "--keepdb cannot be combined with parallel test workers because "
                "reused worker clones cannot be repaired safely."
            )
        reused_default_database = (
            self.keepdb
            and "default" in aliases
            and self._test_database_exists("default")
        )
        config = super().setup_databases(**kwargs)
        if not reused_default_database:
            return config

        from staff_access.models import (
            CapabilityDefinition,
            CapabilityPolicyState,
            RoleCapabilityDefault,
            StaffRole,
        )
        from professional_profiles.models import Industry
        from professional_profiles.taxonomy import CANONICAL_INDUSTRIES
        from tags.models import Tag
        from skills.models import Skill
        from interests.models import Interest
        from audit.models import AuditEvent

        canonical_tags = import_module("tags.migrations.0002_seed_tags").CANONICAL_TAGS
        canonical_skills = import_module("skills.migrations.0002_seed_skills").CANONICAL_SKILLS
        canonical_interests = import_module("interests.migrations.0002_seed_interests").CANONICAL_INTERESTS
        capability_seed = import_module("staff_access.migrations.0006_capability_foundation")
        governance_seed = import_module("staff_access.migrations.0007_capability_governance")
        legal_hold_seed = import_module("staff_access.migrations.0008_legal_hold_admin")

        for code, name in StaffRole.CANONICAL_ROLES:
            StaffRole.objects.update_or_create(
                code=code,
                defaults={"name": name, "is_active": True},
            )
        canonical_role_codes = {code for code, _name in StaffRole.CANONICAL_ROLES}
        unexpected_role_codes = set(StaffRole.objects.values_list("code", flat=True)) - canonical_role_codes
        if unexpected_role_codes:
            raise RuntimeError(
                "Reused test database contains unexpected staff roles: "
                + ", ".join(sorted(unexpected_role_codes))
            )
        capabilities = {}
        for code, name, description in capability_seed.CAPABILITIES:
            capability, _created = CapabilityDefinition.objects.update_or_create(
                code=code,
                defaults={"name": name, "description": description, "is_active": True},
            )
            capabilities[code] = capability
        canonical_default_pairs = set()
        for role_code, capability_codes in capability_seed.ROLE_DEFAULTS.items():
            role = StaffRole.objects.get(code=role_code)
            for capability_code in capability_codes:
                canonical_default_pairs.add((role_code, capability_code))
                RoleCapabilityDefault.objects.update_or_create(
                    role=role,
                    capability=capabilities[capability_code],
                    defaults={"is_enabled": True},
                )
        for role_default in RoleCapabilityDefault.objects.select_related("role", "capability"):
            if (role_default.role.code, role_default.capability.code) not in canonical_default_pairs:
                role_default.delete()
        baseline_exists = AuditEvent.objects.filter(
            action=AuditEvent.Action.CAPABILITY_POLICY_BASELINED
        ).exists()
        extension_exists = AuditEvent.objects.filter(
            action=AuditEvent.Action.CAPABILITY_CATALOG_EXTENDED
        ).exists()
        if not baseline_exists:
            CapabilityPolicyState.objects.all().delete()
            governance_seed.seed_capability_policy_state(apps, schema_editor=None)
        else:
            CapabilityPolicyState.objects.get_or_create(
                singleton_key=1,
                defaults={
                    "key": "capability-governance",
                    "version": 2 if extension_exists else 1,
                    "reason": "Post-migration test baseline",
                    "baseline_snapshot": {},
                },
            )
        if extension_exists:
            CapabilityPolicyState.objects.filter(singleton_key=1).update(version=2)
        else:
            CapabilityPolicyState.objects.filter(singleton_key=1).update(version=1)
        legal_hold_seed.seed_legal_hold_admin(apps, schema_editor=None)
        canonical_capability_codes = {
            code for code, _name, _description in capability_seed.CAPABILITIES
        } | {CapabilityDefinition.LEGAL_HOLD_ADMIN}
        unexpected_capability_codes = (
            set(CapabilityDefinition.objects.values_list("code", flat=True))
            - canonical_capability_codes
        )
        if unexpected_capability_codes:
            raise RuntimeError(
                "Reused test database contains unexpected privacy capabilities: "
                + ", ".join(sorted(unexpected_capability_codes))
            )
        for action in (
            AuditEvent.Action.CAPABILITY_POLICY_BASELINED,
            AuditEvent.Action.CAPABILITY_CATALOG_EXTENDED,
        ):
            if AuditEvent.objects.filter(action=action).count() != 1:
                raise RuntimeError(
                    f"Reused test database has an invalid {action} migration-audit baseline."
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

    @staticmethod
    def _test_database_exists(alias):
        connection = connections[alias]
        if connection.vendor != "postgresql":
            return False
        test_database_name = connection.creation._get_test_db_name()
        with connection._nodb_cursor() as cursor:
            cursor.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s",
                [test_database_name],
            )
            return cursor.fetchone() is not None
