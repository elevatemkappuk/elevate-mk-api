import hashlib
from datetime import timedelta
from unittest.mock import Mock, patch
from urllib.parse import urlsplit
from uuid import UUID

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from audit.models import AuditEvent
from community.models import CommunityAccountInvitation
from memberships.models import Membership
from notifications.models import TransactionalEmailJob
from notifications.jobs import process_next_transactional_email_job
from people.models import Person


@override_settings(COMMUNITY_ACTIVATION_THROTTLE_RATE="20/hour")
class CommunityActivationApiTests(TestCase):
    activation_url_template = "/api/v1/community/activate/{}/{}/"

    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.token = "activation-token-value"
        self.person = Person.objects.create(
            first_name="Amina",
            last_name="Zulu",
            primary_email="amina@example.com",
        )
        Membership.objects.create(
            person=self.person,
            status=Membership.Status.ACTIVE,
            joined_at=timezone.localdate(),
            membership_source=Membership.Source.COMMUNITY_PLATFORM,
        )
        self.invitation = CommunityAccountInvitation.objects.create(
            person=self.person,
            intended_email="amina@example.com",
            token_hash=hashlib.sha256(self.token.encode()).hexdigest(),
            expires_at=timezone.now() + timedelta(hours=72),
        )
        self.job = TransactionalEmailJob.objects.create(
            invitation=self.invitation,
            template_id="42",
            recipient_email="amina@example.com",
            recipient_name="Amina Zulu",
            first_name="Amina",
            expires_in_hours=72,
        )

    def activation_url(self, token=None):
        return self.activation_url_template.format(self.invitation.public_id, token or self.token)

    def test_valid_activation_creates_linked_user_consumes_invitation_logs_in_and_stops_pending_job(self):
        response = self.client.post(
            self.activation_url(),
            {"password": "Amina-strong-password-123!", "confirm_password": "Amina-strong-password-123!"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.data), {"id", "first_name", "last_name"})
        user = User.objects.get(person=self.person)
        self.assertTrue(user.is_active)
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertTrue(user.has_usable_password())
        self.assertTrue(user.check_password("Amina-strong-password-123!"))
        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(Person.objects.count(), 1)
        self.assertEqual(Membership.objects.count(), 1)
        self.invitation.refresh_from_db()
        self.job.refresh_from_db()
        self.assertIsNotNone(self.invitation.used_at)
        self.assertEqual(self.job.status, TransactionalEmailJob.Status.CANCELLED)
        self.assertTrue(AuditEvent.objects.filter(action=AuditEvent.Action.COMMUNITY_ACCOUNT_ACTIVATED).exists())

        me = self.client.get("/api/v1/community/me/")
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.data["first_name"], "Amina")

        logout = self.client.post("/api/v1/auth/logout/")
        self.assertEqual(logout.status_code, 204)
        self.assertEqual(self.client.get("/api/v1/community/me/").status_code, 401)

    def test_wrong_token_is_generic_and_does_not_consume_invitation(self):
        response = self.client.post(
            self.activation_url("wrong-token"),
            {"password": "Amina-strong-password-123!", "confirm_password": "Amina-strong-password-123!"},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "INVALID_OR_EXPIRED_ACTIVATION")
        self.assertFalse(User.objects.exists())
        self.invitation.refresh_from_db()
        self.assertIsNone(self.invitation.used_at)

    def test_expired_used_revoked_and_superseded_invitations_are_invalid(self):
        for field in ("expired", "used", "revoked", "superseded"):
            invitation = CommunityAccountInvitation.objects.create(
                person=Person.objects.create(
                    first_name="Test",
                    last_name=field,
                    primary_email=f"{field}@example.com",
                ),
                intended_email=f"{field}@example.com",
                token_hash=hashlib.sha256(self.token.encode()).hexdigest(),
                expires_at=timezone.now() + timedelta(hours=1),
            )
            Membership.objects.create(
                person=invitation.person,
                status=Membership.Status.ACTIVE,
                joined_at=timezone.localdate(),
                membership_source=Membership.Source.COMMUNITY_PLATFORM,
            )
            setattr(invitation, {"expired": "expires_at", "used": "used_at", "revoked": "revoked_at", "superseded": "superseded_at"}[field], timezone.now() - timedelta(minutes=1))
            invitation.save()
            response = self.client.post(
                self.activation_url_template.format(invitation.public_id, self.token),
                {"password": "Amina-strong-password-123!", "confirm_password": "Amina-strong-password-123!"},
                format="json",
            )
            self.assertEqual(response.data["code"], "INVALID_OR_EXPIRED_ACTIVATION")

    def test_password_validation_does_not_create_user_or_consume_invitation(self):
        response = self.client.post(
            self.activation_url(),
            {"password": "123", "confirm_password": "123"},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "PASSWORD_VALIDATION_ERROR")
        self.assertFalse(User.objects.exists())
        self.invitation.refresh_from_db()
        self.assertIsNone(self.invitation.used_at)

    def test_existing_user_states_are_unavailable_without_mutation(self):
        for password, active in (("Existing-password-123!", True), ("", False), (None, True)):
            person = Person.objects.create(
                first_name="Existing",
                last_name=str(User.objects.count()),
                primary_email=f"existing-{User.objects.count()}@example.com",
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
                token_hash=hashlib.sha256(self.token.encode()).hexdigest(),
                expires_at=timezone.now() + timedelta(hours=72),
            )
            user = User.objects.create_user(email=person.primary_email, password=password, person=person)
            user.is_active = active
            user.save(update_fields=["is_active"])
            response = self.client.post(
                self.activation_url_template.format(invitation.public_id, self.token),
                {"password": "Replacement-password-123!", "confirm_password": "Replacement-password-123!"},
                format="json",
            )
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.data["code"], "ACCOUNT_SETUP_UNAVAILABLE")
            user.refresh_from_db()
            self.assertEqual(user.is_active, active)

    def test_community_user_has_no_staff_access(self):
        self.client.post(
            self.activation_url(),
            {"password": "Amina-strong-password-123!", "confirm_password": "Amina-strong-password-123!"},
            format="json",
        )

        self.assertEqual(self.client.get("/api/v1/people/").status_code, 403)
        self.assertEqual(self.client.get(f"/api/v1/people/{self.person.id}/membership/").status_code, 403)
        self.assertEqual(self.client.get(f"/api/v1/people/{self.person.id}/marketing-preference/").status_code, 403)

    def test_activation_requires_csrf(self):
        csrf_client = APIClient(enforce_csrf_checks=True)
        response = csrf_client.post(
            self.activation_url(),
            {"password": "Amina-strong-password-123!", "confirm_password": "Amina-strong-password-123!"},
            format="json",
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(User.objects.exists())


@override_settings(
    COMMUNITY_FRONTEND_URL="http://localhost:4201",
    COMMUNITY_ACTIVATION_EXPIRY_HOURS=72,
)
class CommunityActivationWorkerIntegrationTests(TestCase):
    def test_worker_generated_activation_url_redeems_through_api(self):
        cache.clear()
        client = APIClient()
        person = Person.objects.create(
            first_name="Amina",
            last_name="Zulu",
            primary_email="worker-activation@example.com",
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
        job = TransactionalEmailJob.objects.create(
            invitation=invitation,
            template_id="activation-template",
            recipient_email=person.primary_email,
            recipient_name="Amina Zulu",
            first_name="Amina",
            expires_in_hours=72,
        )

        with patch("notifications.jobs.send_transactional_email") as send_email:
            send_email.return_value = Mock(provider_message_id="worker-integration")
            result = process_next_transactional_email_job()

        self.assertEqual(result.status, TransactionalEmailJob.Status.SENT)
        activation_url = send_email.call_args.kwargs["template_params"]["activation_url"]
        parsed = urlsplit(activation_url)
        path_parts = parsed.path.strip("/").split("/")
        self.assertEqual(path_parts[0], "activate")
        invitation_id = UUID(path_parts[1])
        raw_token = path_parts[2]
        self.assertEqual(invitation_id, invitation.public_id)
        self.assertTrue(raw_token)

        invitation.refresh_from_db()
        job.refresh_from_db()
        self.assertIsNotNone(invitation.token_hash)
        self.assertEqual(len(invitation.token_hash), 64)
        self.assertEqual(hashlib.sha256(raw_token.encode("utf-8")).hexdigest(), invitation.token_hash)
        self.assertGreater(invitation.expires_at, timezone.now())
        self.assertIsNone(invitation.used_at)
        self.assertTrue(invitation.is_current)
        self.assertNotIn(raw_token, str(invitation.__dict__))
        self.assertNotIn(raw_token, str(job.__dict__))
        self.assertEqual(job.status, TransactionalEmailJob.Status.SENT)

        response = client.post(
            f"/api/v1/community/activate/{invitation_id}/{raw_token}/",
            {"password": "Amina-worker-password-123!", "confirm_password": "Amina-worker-password-123!"},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data.get("code"))
        self.assertEqual(User.objects.filter(person=person).count(), 1)
        self.assertEqual(Person.objects.count(), 1)
        self.assertEqual(Membership.objects.filter(person=person).count(), 1)
        user = User.objects.get(person=person)
        self.assertTrue(user.is_active)
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertTrue(user.has_usable_password())
        self.assertTrue(user.check_password("Amina-worker-password-123!"))

        invitation.refresh_from_db()
        job.refresh_from_db()
        self.assertIsNotNone(invitation.used_at)
        self.assertEqual(job.status, TransactionalEmailJob.Status.SENT)
        self.assertEqual(TransactionalEmailJob.objects.filter(invitation=invitation).count(), 1)
        self.assertEqual(client.get("/api/v1/community/me/").status_code, 200)
