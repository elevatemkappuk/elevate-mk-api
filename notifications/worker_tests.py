from datetime import timedelta
from unittest.mock import Mock, patch

from django.test import TestCase, override_settings
from django.utils import timezone

from community.models import CommunityAccountInvitation
from memberships.models import Membership
from notifications.exceptions import TransactionalEmailConfigurationError, TransactionalEmailError
from notifications.jobs import _claim_next_job, process_next_transactional_email_job
from notifications.models import TransactionalEmailJob
from people.models import Person
from accounts.models import User


@override_settings(
    COMMUNITY_FRONTEND_URL="http://localhost:4201",
    COMMUNITY_ACTIVATION_EXPIRY_HOURS=72,
    TRANSACTIONAL_EMAIL_JOB_LEASE_SECONDS=900,
)
class TransactionalEmailWorkerTests(TestCase):
    def create_job(self, **overrides):
        person = Person.objects.create(
            first_name="Amina",
            last_name="Zulu",
            primary_email="amina@example.com",
        )
        Membership.objects.create(
            person=person,
            status=Membership.Status.ACTIVE,
            joined_at=timezone.localdate(),
            membership_source=Membership.Source.COMMUNITY_PLATFORM,
        )
        invitation = CommunityAccountInvitation.objects.create(
            person=person,
            intended_email=person.primary_email,
            expires_at=timezone.now() - timedelta(hours=1),
        )
        values = {
            "invitation": invitation,
            "template_id": "42",
            "recipient_email": person.primary_email,
            "recipient_name": "Amina Zulu",
            "first_name": "Amina",
            "expires_in_hours": 72,
        }
        values.update(overrides)
        return TransactionalEmailJob.objects.create(**values), invitation

    def test_pending_job_can_be_claimed_once(self):
        job, _ = self.create_job()

        claimed = _claim_next_job()
        second = _claim_next_job()

        self.assertEqual(claimed.id, job.id)
        self.assertIsNone(second)
        claimed.refresh_from_db()
        self.assertEqual(claimed.status, TransactionalEmailJob.Status.PROCESSING)
        self.assertEqual(claimed.attempts, 1)

    @patch("notifications.jobs.send_transactional_email")
    def test_send_mints_token_and_passes_exact_contract_without_persisting_url(self, send_email):
        send_email.return_value = Mock(provider_message_id="brevo-id")
        job, invitation = self.create_job(template_id="42")

        result = process_next_transactional_email_job()

        self.assertEqual(result.status, TransactionalEmailJob.Status.SENT)
        invitation.refresh_from_db()
        job.refresh_from_db()
        kwargs = send_email.call_args.kwargs
        self.assertEqual(kwargs["template_id"], "42")
        self.assertEqual(kwargs["recipient_email"], "amina@example.com")
        self.assertEqual(kwargs["recipient_name"], "Amina Zulu")
        self.assertEqual(set(kwargs["template_params"]), {"first_name", "activation_url", "expires_in_hours"})
        activation_url = kwargs["template_params"]["activation_url"]
        self.assertTrue(activation_url.startswith("http://localhost:4201/activate/"))
        self.assertNotIn("#", activation_url)
        self.assertNotIn(" ", activation_url)
        self.assertIsNotNone(invitation.token_hash)
        self.assertNotIn(kwargs["template_params"]["activation_url"], str(invitation.__dict__))
        self.assertEqual(job.provider_message_id, "brevo-id")

    @patch("notifications.jobs.send_transactional_email")
    def test_send_time_expiry_is_refreshed(self, send_email):
        send_email.return_value = Mock(provider_message_id="brevo-id")
        job, invitation = self.create_job()
        before = timezone.now() + timedelta(hours=71)

        process_next_transactional_email_job()

        invitation.refresh_from_db()
        self.assertGreater(invitation.expires_at, before)

    @patch("notifications.jobs.send_transactional_email")
    def test_generic_provider_failure_becomes_delivery_uncertain(self, send_email):
        send_email.side_effect = TransactionalEmailError("provider outcome unavailable")
        job, _ = self.create_job()

        process_next_transactional_email_job()

        job.refresh_from_db()
        self.assertEqual(job.status, TransactionalEmailJob.Status.DELIVERY_UNCERTAIN)

    @patch("notifications.jobs.send_transactional_email")
    def test_configuration_failure_is_retryable_with_backoff(self, send_email):
        send_email.side_effect = TransactionalEmailConfigurationError("missing config")
        job, invitation = self.create_job()

        process_next_transactional_email_job()

        job.refresh_from_db()
        invitation.refresh_from_db()
        self.assertEqual(job.status, TransactionalEmailJob.Status.PENDING)
        self.assertGreater(job.available_at, timezone.now())
        self.assertIsNotNone(invitation.token_hash)

    def test_stale_processing_becomes_delivery_uncertain(self):
        job, _ = self.create_job(status=TransactionalEmailJob.Status.PROCESSING, locked_at=timezone.now() - timedelta(hours=1))

        self.assertIsNone(_claim_next_job())
        job.refresh_from_db()
        self.assertEqual(job.status, TransactionalEmailJob.Status.DELIVERY_UNCERTAIN)

    @patch("notifications.jobs.send_transactional_email")
    def test_ineligible_invitation_is_cancelled_without_provider_call(self, send_email):
        job, invitation = self.create_job()
        invitation.superseded_at = timezone.now()
        invitation.save(update_fields=["superseded_at", "updated_at"])

        process_next_transactional_email_job()

        job.refresh_from_db()
        self.assertEqual(job.status, TransactionalEmailJob.Status.CANCELLED)
        send_email.assert_not_called()

    @patch("notifications.jobs.send_transactional_email")
    def test_active_usable_user_cancels_obsolete_activation_without_sending(self, send_email):
        job, invitation = self.create_job()
        User.objects.create_user(
            email=invitation.intended_email,
            password="Strong-password-123!",
            person=invitation.person,
        )

        process_next_transactional_email_job()

        job.refresh_from_db()
        self.assertEqual(job.status, TransactionalEmailJob.Status.CANCELLED)
        send_email.assert_not_called()

    def test_sent_and_uncertain_jobs_are_not_claimed(self):
        sent, _ = self.create_job(status=TransactionalEmailJob.Status.SENT)
        uncertain, _ = self.create_job(status=TransactionalEmailJob.Status.DELIVERY_UNCERTAIN)

        self.assertIsNone(_claim_next_job())
        sent.refresh_from_db()
        uncertain.refresh_from_db()
        self.assertEqual(sent.status, TransactionalEmailJob.Status.SENT)
        self.assertEqual(uncertain.status, TransactionalEmailJob.Status.DELIVERY_UNCERTAIN)
