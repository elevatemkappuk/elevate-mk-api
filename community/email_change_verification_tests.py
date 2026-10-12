import hashlib
from datetime import timedelta
from unittest.mock import Mock, patch

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from audit.models import AuditEvent
from community.models import CommunityEmailChangeRequest
from external_references.models import ExternalPersonSyncJob
from memberships.models import Membership
from notifications.jobs import process_next_transactional_email_job
from notifications.models import TransactionalEmailJob
from people.models import Person


@override_settings(BREVO_COMMUNITY_EMAIL_CHANGE_SECURITY_TEMPLATE_ID="30")
class CommunityEmailChangeVerificationApiTests(TestCase):
    url = "/api/v1/community/account/email-change/verify/"
    password = "Current-password-123!"
    raw_token = "verification-token-value"

    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.person = Person.objects.create(
            first_name="Amina", last_name="Zulu", primary_email="current@example.com",
            location="Milton Keynes", record_type=Person.RecordType.BUSINESS,
        )
        Membership.objects.create(
            person=self.person, status=Membership.Status.ACTIVE, joined_at=timezone.localdate(),
            membership_source=Membership.Source.COMMUNITY_PLATFORM,
        )
        self.user = User.objects.create_user(
            email=self.person.primary_email, password=self.password, person=self.person,
        )
        self.change_request = CommunityEmailChangeRequest.objects.create(
            person=self.person,
            user=self.user,
            previous_email="current@example.com",
            requested_email="new@example.com",
            token_hash=hashlib.sha256(self.raw_token.encode()).hexdigest(),
            expires_at=timezone.now() + timedelta(hours=1),
        )

    def test_valid_logged_out_verification_updates_both_identities_and_queues_durable_work(self):
        verification_job = TransactionalEmailJob.objects.create(
            email_change_request=self.change_request,
            template_id="29",
            recipient_email="new@example.com",
            recipient_name="Amina Zulu",
            first_name="Amina",
            expires_in_hours=1,
            expires_in_minutes=60,
            job_type=TransactionalEmailJob.JobType.COMMUNITY_EMAIL_CHANGE,
        )
        response = self.client.post(
            self.url,
            {"request_id": str(self.change_request.public_id), "token": self.raw_token},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {"status": "EMAIL_UPDATED", "detail": "Your email address has been verified and updated."})
        self.user.refresh_from_db()
        self.person.refresh_from_db()
        self.change_request.refresh_from_db()
        self.assertEqual(self.user.email, "new@example.com")
        self.assertEqual(self.person.primary_email, "new@example.com")
        self.assertIsNotNone(self.change_request.used_at)
        migration = ExternalPersonSyncJob.objects.get(job_type="PERSON_EMAIL_MIGRATION")
        self.assertEqual(migration.previous_email, "current@example.com")
        self.assertEqual(migration.requested_email, "new@example.com")
        security_job = TransactionalEmailJob.objects.get(job_type=TransactionalEmailJob.JobType.COMMUNITY_EMAIL_CHANGE_SECURITY)
        verification_job.refresh_from_db()
        self.assertEqual(TransactionalEmailJob.objects.filter(email_change_request=self.change_request).count(), 2)
        self.assertEqual(verification_job.job_type, TransactionalEmailJob.JobType.COMMUNITY_EMAIL_CHANGE)
        self.assertEqual(security_job.recipient_email, "current@example.com")
        self.assertEqual(security_job.template_id, "30")
        audit = AuditEvent.objects.get(action=AuditEvent.Action.COMMUNITY_EMAIL_CHANGED)
        self.assertEqual(audit.metadata["source"], "COMMUNITY_SELF_SERVICE")
        self.assertEqual(audit.actor_user_id, self.user.id)
        self.assertNotIn("current@example.com", str(audit.metadata))
        self.assertNotIn("new@example.com", str(audit.metadata))

    def test_authenticated_verification_logs_out_current_session_without_auto_login(self):
        self.client.force_login(self.user)

        response = self.client.post(self.url, {"request_id": str(self.change_request.public_id), "token": self.raw_token}, format="json")

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertEqual(self.client.get("/api/v1/community/me/").status_code, 401)

    def test_verification_requires_csrf_but_not_authentication(self):
        csrf_client = APIClient(enforce_csrf_checks=True)
        response = csrf_client.post(self.url, {"request_id": str(self.change_request.public_id), "token": self.raw_token}, format="json")
        self.assertEqual(response.status_code, 403)

    def test_snapshot_change_is_rejected_without_overwriting_canonical_email(self):
        self.person.primary_email = "changed@example.com"
        self.person.save(update_fields=["primary_email", "updated_at"])

        response = self.client.post(self.url, {"request_id": str(self.change_request.public_id), "token": self.raw_token}, format="json")

        self.assertEqual(response.status_code, 400)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "current@example.com")
        self.assertFalse(ExternalPersonSyncJob.objects.exists())

    def test_later_failure_rolls_back_both_emails_request_and_jobs(self):
        self.client.raise_request_exception = False
        with patch("community.services.enqueue_person_sync_job", side_effect=RuntimeError("queue unavailable")):
            response = self.client.post(self.url, {"request_id": str(self.change_request.public_id), "token": self.raw_token}, format="json")

        self.assertEqual(response.status_code, 500)
        self.user.refresh_from_db()
        self.person.refresh_from_db()
        self.change_request.refresh_from_db()
        self.assertEqual(self.user.email, "current@example.com")
        self.assertEqual(self.person.primary_email, "current@example.com")
        self.assertIsNone(self.change_request.used_at)
        self.assertFalse(ExternalPersonSyncJob.objects.exists())
        self.assertFalse(TransactionalEmailJob.objects.exists())

    def test_repeated_verification_cannot_repeat_canonical_mutation_or_jobs(self):
        payload = {"request_id": str(self.change_request.public_id), "token": self.raw_token}
        self.assertEqual(self.client.post(self.url, payload, format="json").status_code, 200)
        self.assertEqual(self.client.post(self.url, payload, format="json").status_code, 400)
        self.assertEqual(ExternalPersonSyncJob.objects.filter(job_type="PERSON_EMAIL_MIGRATION").count(), 1)
        self.assertEqual(TransactionalEmailJob.objects.filter(job_type=TransactionalEmailJob.JobType.COMMUNITY_EMAIL_CHANGE_SECURITY).count(), 1)

    def test_invalid_states_have_one_generic_response_and_do_not_mutate_identity(self):
        cases = [
            {"request_id": "not-a-uuid", "token": self.raw_token},
            {"request_id": str(self.change_request.public_id), "token": "wrong-token"},
        ]
        for payload in cases:
            response = self.client.post(self.url, payload, format="json")
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.data["code"], "EMAIL_CHANGE_VERIFICATION_INVALID")
            self.assertEqual(response.data["detail"], "We couldn't verify this email change. The link may be invalid or expired.")
        self.user.refresh_from_db()
        self.person.refresh_from_db()
        self.assertEqual(self.user.email, "current@example.com")
        self.assertEqual(self.person.primary_email, "current@example.com")
        self.assertFalse(ExternalPersonSyncJob.objects.exists())

    def test_expired_superseded_used_and_unprepared_requests_are_rejected(self):
        for field in ("expires_at", "superseded_at", "used_at"):
            request = self.change_request
            request.token_hash = hashlib.sha256(self.raw_token.encode()).hexdigest()
            request.expires_at = timezone.now() + timedelta(hours=1)
            request.used_at = None
            request.revoked_at = None
            request.superseded_at = None
            if field == "expires_at":
                request.expires_at = timezone.now() - timedelta(minutes=1)
            else:
                setattr(request, field, timezone.now())
            request.save(update_fields=["token_hash", "expires_at", "used_at", "revoked_at", "superseded_at", "updated_at"])
            response = self.client.post(self.url, {"request_id": str(request.public_id), "token": self.raw_token}, format="json")
            self.assertEqual(response.status_code, 400)

    def test_completion_does_not_call_provider_and_security_job_uses_only_safe_params(self):
        self.client.post(self.url, {"request_id": str(self.change_request.public_id), "token": self.raw_token}, format="json")
        with patch("notifications.jobs.send_transactional_email", return_value=Mock(provider_message_id="security-id")) as send:
            result = process_next_transactional_email_job()
        self.assertEqual(result.status, TransactionalEmailJob.Status.SENT)
        kwargs = send.call_args.kwargs
        self.assertEqual(kwargs["recipient_email"], "current@example.com")
        self.assertEqual(kwargs["template_id"], "30")
        self.assertEqual(kwargs["template_params"], {"first_name": "Amina"})
