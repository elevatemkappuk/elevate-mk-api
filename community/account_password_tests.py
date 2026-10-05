from django.core.cache import cache
from django.conf import settings
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from audit.models import AuditEvent
from external_references.models import ExternalPersonSyncJob
from memberships.models import Membership
from people.models import Person
from community.views import CommunityAccountPasswordView


class CommunityAccountPasswordApiTests(TestCase):
    url = "/api/v1/community/account/password/"
    old_password = "Current-password-123!"
    new_password = "New-password-456!"

    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.csrf_client = APIClient(enforce_csrf_checks=True)
        self.person = Person.objects.create(
            first_name="Amina",
            last_name="Zulu",
            primary_email="password-member@example.com",
            location="Milton Keynes",
            record_type=Person.RecordType.BUSINESS,
        )
        Membership.objects.create(
            person=self.person,
            status=Membership.Status.ACTIVE,
            joined_at=timezone.localdate(),
            membership_source=Membership.Source.COMMUNITY_PLATFORM,
        )
        self.user = User.objects.create_user(
            email=self.person.primary_email,
            password=self.old_password,
            person=self.person,
        )

    def authenticate(self, client=None):
        target = client or self.client
        target.force_login(self.user)
        return target

    def payload(self, **overrides):
        payload = {
            "current_password": self.old_password,
            "new_password": self.new_password,
            "confirm_password": self.new_password,
        }
        payload.update(overrides)
        return payload

    def test_unauthenticated_request_is_rejected(self):
        response = self.client.post(self.url, self.payload(), format="json")
        self.assertEqual(response.status_code, 401)

    def test_ineligible_authenticated_user_is_rejected(self):
        self.authenticate()
        self.person.membership.status = Membership.Status.FORMER
        self.person.membership.save(update_fields=["status"])
        response = self.client.post(self.url, self.payload(), format="json")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["code"], "COMMUNITY_ACCESS_UNAVAILABLE")

    def test_csrf_is_required_for_authenticated_mutation(self):
        self.authenticate(self.csrf_client)
        response = self.csrf_client.post(self.url, self.payload(), format="json")
        self.assertEqual(response.status_code, 403)

    def test_incorrect_current_password_does_not_change_password_or_audit(self):
        self.authenticate()
        response = self.client.post(self.url, self.payload(current_password="wrong-password"), format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "CURRENT_PASSWORD_INVALID")
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.old_password))
        self.assertFalse(AuditEvent.objects.filter(action=AuditEvent.Action.PASSWORD_CHANGED).exists())

    def test_confirmation_mismatch_is_rejected(self):
        self.authenticate()
        response = self.client.post(self.url, self.payload(confirm_password="Different-password-789!"), format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["fields"]["confirm_password"], ["The passwords do not match."])

    def test_configured_password_validation_is_applied(self):
        self.authenticate()
        response = self.client.post(self.url, self.payload(new_password="password", confirm_password="password"), format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "PASSWORD_VALIDATION_ERROR")
        self.assertIn("new_password", response.data["fields"])

    def test_same_password_is_rejected(self):
        self.authenticate()
        response = self.client.post(self.url, self.payload(new_password=self.old_password, confirm_password=self.old_password), format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "PASSWORD_UNCHANGED")

    def test_valid_change_updates_hash_preserves_session_and_audits_without_secrets(self):
        self.authenticate()
        original_hash = self.user.password
        before_jobs = ExternalPersonSyncJob.objects.count()

        response = self.client.post(self.url, self.payload(), format="json")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {"detail": "Your password has been changed successfully."})
        self.user.refresh_from_db()
        self.assertNotEqual(self.user.password, original_hash)
        self.assertFalse(self.user.check_password(self.old_password))
        self.assertTrue(self.user.check_password(self.new_password))
        self.assertEqual(self.client.get("/api/v1/community/me/").status_code, 200)

        old_client = APIClient()
        self.assertEqual(old_client.post("/api/v1/community/login/", {"email": self.user.email, "password": self.old_password}, format="json").status_code, 400)
        new_client = APIClient()
        self.assertEqual(new_client.post("/api/v1/community/login/", {"email": self.user.email, "password": self.new_password}, format="json").status_code, 200)

        event = AuditEvent.objects.get(action=AuditEvent.Action.PASSWORD_CHANGED)
        self.assertEqual(event.actor_user_id, self.user.id)
        self.assertEqual(event.metadata["source"], "COMMUNITY_SELF_SERVICE")
        serialized = repr({"changes": event.changes, "metadata": event.metadata})
        for secret in (self.old_password, self.new_password, self.user.password):
            self.assertNotIn(secret, serialized)
        self.assertEqual(ExternalPersonSyncJob.objects.count(), before_jobs)

    def test_reusing_old_password_in_authenticated_session_fails(self):
        self.authenticate()
        self.client.post(self.url, self.payload(), format="json")
        response = self.client.post(self.url, self.payload(), format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "CURRENT_PASSWORD_INVALID")

    def test_dedicated_throttle_scope_is_configured(self):
        self.assertEqual(CommunityAccountPasswordView.throttle_scope, "community_account_password")
        self.assertEqual(settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]["community_account_password"], "5/hour")
