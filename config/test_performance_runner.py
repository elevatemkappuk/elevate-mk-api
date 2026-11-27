from unittest.mock import patch

from django.conf import settings
from django.contrib.auth.hashers import check_password, identify_hasher, make_password
from django.test import SimpleTestCase, override_settings
from django.test.runner import DiscoverRunner

from config.test_runner import (
    FAST_TEST_PASSWORD_HASHERS,
    ElevateTestRunner,
    _init_elevate_worker,
)


PRODUCTION_PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]


class ProductionPasswordHasherCompatibilityTests(SimpleTestCase):
    @override_settings(PASSWORD_HASHERS=PRODUCTION_PASSWORD_HASHERS)
    def test_production_hasher_remains_usable_when_explicitly_requested(self):
        encoded = make_password("Test-password-123!")

        self.assertEqual(identify_hasher(encoded).algorithm, "pbkdf2_sha256")
        self.assertTrue(check_password("Test-password-123!", encoded))

    @override_settings(PASSWORD_HASHERS=PRODUCTION_PASSWORD_HASHERS)
    @patch("config.test_runner.ParallelTestSuite.init_worker")
    def test_spawned_worker_applies_fast_test_hasher_after_django_setup(self, base_init_worker):
        _init_elevate_worker("counter")

        base_init_worker.assert_called_once_with("counter")
        self.assertEqual(settings.PASSWORD_HASHERS, FAST_TEST_PASSWORD_HASHERS)
        self.assertEqual(identify_hasher(make_password("Test-password-123!")).algorithm, "md5")

    @override_settings(PASSWORD_HASHERS=PRODUCTION_PASSWORD_HASHERS)
    @patch.object(DiscoverRunner, "teardown_test_environment")
    @patch.object(DiscoverRunner, "setup_test_environment")
    def test_main_runner_restores_original_hashers(self, base_setup, base_teardown):
        runner = ElevateTestRunner()

        runner.setup_test_environment()
        self.assertEqual(settings.PASSWORD_HASHERS, FAST_TEST_PASSWORD_HASHERS)
        runner.teardown_test_environment()

        base_setup.assert_called_once_with()
        base_teardown.assert_called_once_with()
        self.assertEqual(settings.PASSWORD_HASHERS, PRODUCTION_PASSWORD_HASHERS)

    def test_parallel_keepdb_is_rejected_before_database_setup(self):
        runner = ElevateTestRunner(keepdb=True, parallel=2)

        with self.assertRaisesRegex(RuntimeError, "--keepdb cannot be combined"):
            runner.setup_databases(aliases={})
