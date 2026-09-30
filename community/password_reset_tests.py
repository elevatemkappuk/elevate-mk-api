from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from memberships.models import Membership
from people.models import Person
from notifications.exceptions import TransactionalEmailError


@override_settings(
    COMMUNITY_FRONTEND_URL="http://localhost:4201/",
    BREVO_PASSWORD_RESET_TEMPLATE_ID="42",
    BREVO_COMMUNITY_PASSWORD_RESET_TEMPLATE_ID="27",
    COMMUNITY_PASSWORD_RESET_THROTTLE_RATE="20/hour",
)
class CommunityPasswordResetRequestTests(TestCase):
    url = "/api/v1/community/password-reset/"
    detail = "If an eligible Elevate MK account exists for that email address, we've sent password reset instructions."

    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.person = Person.objects.create(
            first_name="Amina",
            last_name="Zulu",
            primary_email="member@example.com",
        )
        Membership.objects.create(
            person=self.person,
            status=Membership.Status.ACTIVE,
            joined_at=timezone.localdate(),
            membership_source=Membership.Source.COMMUNITY_PLATFORM,
        )
        self.user = User.objects.create_user(
            email=self.person.primary_email,
            password="Old-password-123!",
            person=self.person,
        )

    def post(self, email="member@example.com", client=None):
        return (client or self.client).post(self.url, {"email": email}, format="json")

    def test_eligible_member_gets_generic_response_and_community_url(self):
        with patch("community.views.send_transactional_email") as send_email:
            response = self.post(" MEMBER@EXAMPLE.COM ")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {"detail": self.detail})
        kwargs = send_email.call_args.kwargs
        self.assertEqual(kwargs["recipient_email"], self.user.email)
        self.assertEqual(kwargs["template_id"], "27")
        self.assertTrue(kwargs["template_params"]["reset_url"].startswith("http://localhost:4201/reset-password/"))
        self.assertNotIn("CRM", kwargs["template_params"]["reset_url"])

    def test_noneligible_accounts_have_identical_response_without_delivery(self):
        scenarios = []
        cache.clear()
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        scenarios.append(self.post())

        cache.clear()
        self.user.is_active = True
        self.user.set_unusable_password()
        self.user.save(update_fields=["is_active", "password"])
        scenarios.append(self.post())

        cache.clear()
        self.user.set_password("Old-password-123!")
        self.user.save(update_fields=["password"])
        self.person.membership.status = Membership.Status.FORMER
        self.person.membership.save(update_fields=["status"])
        scenarios.append(self.post())

        cache.clear()
        self.person.membership.status = Membership.Status.ACTIVE
        self.person.membership.save(update_fields=["status"])
        self.person.archived_at = timezone.now()
        self.person.save(update_fields=["archived_at"])
        scenarios.append(self.post())

        cache.clear()
        self.person.archived_at = None
        self.person.record_type = Person.RecordType.TECHNICAL
        self.person.save(update_fields=["archived_at", "record_type"])
        scenarios.append(self.post())

        cache.clear()
        self.user.delete()
        scenarios.append(self.post())
        scenarios.append(self.post("unknown@example.com"))

        self.assertTrue(all(response.status_code == 200 for response in scenarios))
        self.assertTrue(all(response.data == {"detail": self.detail} for response in scenarios))

    def test_staff_user_without_active_membership_is_not_eligible_but_staff_member_is(self):
        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])
        self.person.membership.status = Membership.Status.FORMER
        self.person.membership.save(update_fields=["status"])

        with patch("community.views.send_transactional_email") as send_email:
            response = self.post()
        self.assertEqual(response.data, {"detail": self.detail})
        send_email.assert_not_called()

        self.person.membership.status = Membership.Status.ACTIVE
        self.person.membership.save(update_fields=["status"])
        with patch("community.views.send_transactional_email") as send_email:
            response = self.post()
        self.assertEqual(response.data, {"detail": self.detail})
        send_email.assert_called_once()

    def test_provider_failure_remains_generic(self):
        with patch("community.views.send_transactional_email", side_effect=TransactionalEmailError("provider details")):
            response = self.post()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {"detail": self.detail})
        self.assertNotIn("provider", str(response.data).lower())

    @override_settings(BREVO_COMMUNITY_PASSWORD_RESET_TEMPLATE_ID="", BREVO_PASSWORD_RESET_TEMPLATE_ID="10")
    def test_missing_community_template_does_not_fall_back_to_crm_template(self):
        with patch("community.views.send_transactional_email") as send_email:
            response = self.post()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {"detail": self.detail})
        self.assertEqual(send_email.call_args.kwargs["template_id"], "")

    def test_request_requires_csrf(self):
        client = APIClient(enforce_csrf_checks=True)
        response = self.post(client=client)
        self.assertEqual(response.status_code, 403)

    def test_community_request_uses_dedicated_throttle_scope(self):
        from community.views import CommunityPasswordResetRequestView

        self.assertEqual(CommunityPasswordResetRequestView.throttle_scope, "community_password_reset")
