from datetime import date

from django.test import TestCase
from rest_framework.test import APIClient

from audit.models import AuditEvent
from accounts.models import User
from community.models import CommunityProfile
from memberships.models import Membership
from marketing_preferences.models import MarketingPreference
from people.models import Person
from external_references.models import ExternalPersonSyncJob


class CommunityAccountSummaryTests(TestCase):
    url = "/api/v1/community/account/"

    def setUp(self):
        self.client = APIClient()
        self.person = Person.objects.create(
            first_name="Amina",
            last_name="Zulu",
            primary_email="Amina@example.com",
            mobile="(07700) 900123",
        )
        Membership.objects.create(
            person=self.person,
            status=Membership.Status.ACTIVE,
            joined_at=date(2026, 1, 1),
            membership_source=Membership.Source.COMMUNITY_PLATFORM,
        )
        self.user = User.objects.create_user(
            email="auth@example.com",
            password="Community-password-123!",
            person=self.person,
        )
        self.client.force_authenticate(user=self.user)

    def test_unauthenticated_member_is_rejected(self):
        self.client.force_authenticate(user=None)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 401)

    def test_ineligible_authenticated_account_is_rejected(self):
        self.person.archived_at = "2026-01-02T00:00:00Z"
        self.person.save(update_fields=["archived_at", "updated_at"])

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["detail"], "Community access is unavailable.")

    def test_returns_canonical_email_masked_mobile_and_configured_password(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["email"], "Amina@example.com")
        self.assertEqual(response.data["mobile"], {"present": True, "masked": "077****0123"})
        self.assertEqual(response.data["password"], {"configured": True})

    def test_no_mobile_returns_safe_empty_state(self):
        self.person.mobile = ""
        self.person.save(update_fields=["mobile", "updated_at"])

        response = self.client.get(self.url)

        self.assertEqual(response.data["mobile"], {"present": False, "masked": None})

    def test_legacy_mobile_formatting_cannot_break_serialization(self):
        self.person.mobile = "+44 (7700) 900 123"
        self.person.save(update_fields=["mobile", "updated_at"])

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["mobile"]["present"])
        self.assertEqual(response.data["mobile"]["masked"], "+44******0123")

    def test_effective_marketing_state_is_projected_without_brevo_data(self):
        for state in MarketingPreference.State.OPTED_IN, MarketingPreference.State.OPTED_OUT:
            with self.subTest(state=state):
                MarketingPreference.objects.update_or_create(
                    person=self.person,
                    channel=MarketingPreference.Channel.EMAIL,
                    defaults={"state": state, "source": MarketingPreference.Source.COMMUNITY_JOIN},
                )
                response = self.client.get(self.url)
                self.assertEqual(response.data["email_marketing"], {"state": state})

        MarketingPreference.objects.filter(person=self.person).delete()
        response = self.client.get(self.url)
        self.assertEqual(response.data["email_marketing"], {"state": MarketingPreference.State.UNKNOWN})

    def test_response_has_no_internal_identifiers_or_provider_state(self):
        response = self.client.get(self.url)

        self.assertEqual(
            set(response.data),
            {"email", "mobile", "email_marketing", "password"},
        )
        self.assertNotIn("person_id", response.data)
        self.assertNotIn("provider", response.data)
        self.assertNotIn("jobs", response.data)
        self.assertNotIn("tokens", response.data)

    def test_get_has_no_profile_privacy_or_side_effect_mutation(self):
        profile = CommunityProfile.objects.create(
            person=self.person,
            directory_visible=False,
            email_visible=True,
            mobile_visible=True,
        )
        audit_count = AuditEvent.objects.count()
        sync_count = ExternalPersonSyncJob.objects.count()

        response = self.client.get(self.url)

        profile.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            (profile.directory_visible, profile.email_visible, profile.mobile_visible),
            (False, True, True),
        )
        self.assertEqual(AuditEvent.objects.count(), audit_count)
        self.assertEqual(ExternalPersonSyncJob.objects.count(), sync_count)
