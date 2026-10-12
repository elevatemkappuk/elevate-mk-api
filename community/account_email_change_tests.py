from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from audit.models import AuditEvent
from community.models import CommunityEmailChangeRequest
from memberships.models import Membership
from notifications.jobs import process_next_transactional_email_job
from notifications.models import TransactionalEmailJob
from people.models import Person


class CommunityAccountEmailChangeApiTests(TestCase):
    url = "/api/v1/community/account/email-change/"
    password = "Current-password-123!"

    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.csrf_client = APIClient(enforce_csrf_checks=True)
        self.person = Person.objects.create(
            first_name="Amina", last_name="Zulu", primary_email="current@example.com",
            location="Milton Keynes", record_type=Person.RecordType.BUSINESS,
        )
        Membership.objects.create(
            person=self.person, status=Membership.Status.ACTIVE, joined_at=timezone.localdate(),
            membership_source=Membership.Source.COMMUNITY_PLATFORM,
        )
        self.user = User.objects.create_user(email=self.person.primary_email, password=self.password, person=self.person)

    def authenticate(self, client=None):
        target = client or self.client
        target.force_login(self.user)
        return target

    def payload(self, **overrides):
        value = {"new_email": "new@example.com", "current_password": self.password}
        value.update(overrides)
        return value

    def test_authentication_eligibility_csrf_and_password_guards(self):
        self.assertEqual(self.client.post(self.url, self.payload(), format="json").status_code, 401)
        self.authenticate(self.csrf_client)
        self.assertEqual(self.csrf_client.post(self.url, self.payload(), format="json").status_code, 403)
        self.authenticate()
        response = self.client.post(self.url, self.payload(current_password="wrong"), format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "INVALID_CURRENT_PASSWORD")
        self.person.membership.status = Membership.Status.FORMER
        self.person.membership.save(update_fields=["status"])
        response = self.client.post(self.url, self.payload(), format="json")
        self.assertEqual(response.status_code, 403)

    def test_current_email_is_a_true_noop(self):
        self.authenticate()
        response = self.client.post(self.url, self.payload(new_email=" CURRENT@EXAMPLE.COM "), format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "UNCHANGED")
        self.assertFalse(CommunityEmailChangeRequest.objects.exists())
        self.assertFalse(TransactionalEmailJob.objects.exists())
        self.assertFalse(AuditEvent.objects.filter(action=AuditEvent.Action.COMMUNITY_EMAIL_CHANGE_REQUESTED).exists())

    def test_inconsistent_identity_and_all_person_user_collisions_are_generic(self):
        self.authenticate()
        self.person.primary_email = "different@example.com"
        self.person.save(update_fields=["primary_email"])
        response = self.client.post(self.url, self.payload(), format="json")
        self.assertEqual(response.data["code"], "EMAIL_CHANGE_SUPPORT_REQUIRED")
        self.person.primary_email = self.user.email
        self.person.save(update_fields=["primary_email"])
        other_person = Person.objects.create(first_name="Other", last_name="User", primary_email="collision@example.com", record_type=Person.RecordType.BUSINESS)
        response = self.client.post(self.url, self.payload(new_email=other_person.primary_email), format="json")
        self.assertEqual(response.data["code"], "EMAIL_CHANGE_UNAVAILABLE")
        self.assertNotIn("Person", response.data["detail"])
        User.objects.create_user(email="user-collision@example.com", password=self.password, person=other_person)
        response = self.client.post(self.url, self.payload(new_email="user-collision@example.com"), format="json")
        self.assertEqual(response.data["code"], "EMAIL_CHANGE_UNAVAILABLE")

    def test_valid_request_is_normalized_durable_and_does_not_change_identity(self):
        self.authenticate()
        response = self.client.post(self.url, self.payload(new_email=" New@Example.COM "), format="json")
        self.assertEqual(response.status_code, 202)
        request = CommunityEmailChangeRequest.objects.get()
        self.assertEqual(request.previous_email, "current@example.com")
        self.assertEqual(request.requested_email, "new@example.com")
        self.assertIsNone(request.token_hash)
        self.assertAlmostEqual((request.expires_at - timezone.now()).total_seconds(), 3600, delta=10)
        job = TransactionalEmailJob.objects.get(email_change_request=request)
        self.assertEqual(job.recipient_email, "new@example.com")
        self.assertEqual(job.template_id, "29")
        self.assertEqual(self.user.email, "current@example.com")
        self.assertEqual(self.person.primary_email, "current@example.com")
        audit = AuditEvent.objects.get(action=AuditEvent.Action.COMMUNITY_EMAIL_CHANGE_REQUESTED)
        self.assertEqual(audit.actor_user_id, self.user.id)
        self.assertEqual(audit.metadata["source"], "COMMUNITY_SELF_SERVICE")
        self.assertNotIn("password", str(audit.metadata).lower())

    def test_new_request_supersedes_previous_and_worker_sends_only_to_new_email(self):
        self.authenticate()
        self.client.post(self.url, self.payload(new_email="first@example.com"), format="json")
        first = CommunityEmailChangeRequest.objects.get()
        self.client.post(self.url, self.payload(new_email="second@example.com"), format="json")
        first.refresh_from_db()
        second = CommunityEmailChangeRequest.objects.exclude(pk=first.pk).get()
        self.assertIsNotNone(first.superseded_at)
        self.assertTrue(second.is_current)
        self.assertEqual(TransactionalEmailJob.objects.filter(email_change_request=first).count(), 1)
        with patch("notifications.jobs.send_transactional_email") as send:
            send.return_value = type("Result", (), {"provider_message_id": "message-1"})()
            self.assertEqual(process_next_transactional_email_job().status, TransactionalEmailJob.Status.CANCELLED)
            result = process_next_transactional_email_job()
        self.assertEqual(result.status, TransactionalEmailJob.Status.SENT)
        kwargs = send.call_args.kwargs
        self.assertEqual(kwargs["recipient_email"], "second@example.com")
        self.assertEqual(set(kwargs["template_params"]), {"first_name", "verification_url", "expires_in_minutes"})
        self.assertTrue(kwargs["template_params"]["verification_url"].startswith("http://localhost:4201/community/account/verify-email/"))
        second.refresh_from_db()
        self.assertIsNotNone(second.token_hash)
        self.assertNotIn(kwargs["template_params"]["verification_url"], str(second.__dict__))
