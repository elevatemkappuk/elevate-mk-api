from django.conf import settings
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from audit.models import AuditEvent
from brevo_marketing.jobs import PERSON_PROFILE_SYNC
from community.models import CommunityProfile
from community.views import CommunityAccountMarketingPreferenceView
from external_references.models import ExternalPersonSyncJob
from marketing_preferences.models import MarketingPreference, MarketingPreferenceHistory
from marketing_preferences.services import record_opt_in
from memberships.models import Membership
from people.models import Person


class CommunityAccountMarketingPreferenceApiTests(TestCase):
    url = "/api/v1/community/account/marketing-preference/"

    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.csrf_client = APIClient(enforce_csrf_checks=True)
        self.person = Person.objects.create(
            first_name="Amina",
            last_name="Zulu",
            primary_email="marketing-member@example.com",
            mobile="+447123456789",
            record_type=Person.RecordType.BUSINESS,
        )
        self.membership = Membership.objects.create(
            person=self.person,
            status=Membership.Status.ACTIVE,
            joined_at=timezone.localdate(),
            membership_source=Membership.Source.COMMUNITY_PLATFORM,
        )
        self.profile = CommunityProfile.objects.create(
            person=self.person,
            directory_visible=False,
            email_visible=True,
            mobile_visible=True,
        )
        self.user = User.objects.create_user(
            email=self.person.primary_email,
            password="Current-password-123!",
            person=self.person,
        )

    def authenticate(self, client=None):
        target = client or self.client
        target.force_login(self.user)
        return target

    def test_unauthenticated_and_ineligible_requests_are_rejected(self):
        response = self.client.patch(self.url, {"email_marketing": True}, format="json")
        self.assertEqual(response.status_code, 401)
        self.authenticate()
        self.membership.status = Membership.Status.FORMER
        self.membership.save(update_fields=["status"])
        response = self.client.patch(self.url, {"email_marketing": True}, format="json")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["code"], "COMMUNITY_ACCESS_UNAVAILABLE")

    def test_csrf_is_required(self):
        self.authenticate(self.csrf_client)
        response = self.csrf_client.patch(self.url, {"email_marketing": True}, format="json")
        self.assertEqual(response.status_code, 403)

    def test_missing_null_string_numeric_and_unknown_values_are_rejected(self):
        self.authenticate()
        for payload in ({}, {"email_marketing": None}, {"email_marketing": "true"}, {"email_marketing": 1}, {"email_marketing": True, "state": "OPTED_IN"}):
            response = self.client.patch(self.url, payload, format="json")
            self.assertEqual(response.status_code, 400, payload)
            self.assertEqual(response.data["code"], "MARKETING_PREFERENCE_VALIDATION_ERROR")
        self.assertFalse(MarketingPreference.objects.exists())
        self.assertFalse(MarketingPreferenceHistory.objects.exists())

    def test_unknown_to_true_is_opted_in_with_authoritative_summary_and_side_effects(self):
        self.authenticate()
        response = self.client.patch(self.url, {"email_marketing": True}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["email_marketing"]["state"], MarketingPreference.State.OPTED_IN)
        preference = MarketingPreference.objects.get(person=self.person)
        history = MarketingPreferenceHistory.objects.get(preference=preference)
        self.assertEqual(preference.state, MarketingPreference.State.OPTED_IN)
        self.assertEqual(preference.source, MarketingPreference.Source.COMMUNITY_SELF_SERVICE)
        self.assertEqual(preference.actor_user_id, self.user.id)
        self.assertEqual(history.source, MarketingPreference.Source.COMMUNITY_SELF_SERVICE)
        self.assertEqual(history.actor_user_id, self.user.id)
        self.assertEqual(ExternalPersonSyncJob.objects.filter(person=self.person, job_type="EMAIL_MARKETING_PREFERENCE").count(), 1)
        self.assertFalse(ExternalPersonSyncJob.objects.filter(person=self.person, job_type=PERSON_PROFILE_SYNC).exists())
        self.assertEqual(AuditEvent.objects.filter(entity_type="MarketingPreference", entity_id=str(preference.id)).latest("id").action, AuditEvent.Action.MARKETING_PREFERENCE_OPTED_IN)

    def test_unknown_to_false_is_opted_out_without_page_load_mutation(self):
        self.authenticate()
        self.client.get("/api/v1/community/account/")
        self.assertFalse(MarketingPreference.objects.exists())
        response = self.client.patch(self.url, {"email_marketing": False}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["email_marketing"]["state"], MarketingPreference.State.OPTED_OUT)
        self.assertEqual(MarketingPreference.objects.get(person=self.person).source, MarketingPreference.Source.COMMUNITY_SELF_SERVICE)
        self.assertEqual(MarketingPreferenceHistory.objects.count(), 1)
        self.assertEqual(AuditEvent.objects.filter(action=AuditEvent.Action.MARKETING_PREFERENCE_OPTED_OUT).count(), 1)

    def test_transitions_preserve_append_only_history_and_previous_sources(self):
        self.authenticate()
        record_opt_in(person=self.person, source=MarketingPreference.Source.COMMUNITY_JOIN)
        response = self.client.patch(self.url, {"email_marketing": False}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(MarketingPreferenceHistory.objects.count(), 2)
        self.assertEqual(list(MarketingPreferenceHistory.objects.values_list("source", flat=True)), [MarketingPreference.Source.COMMUNITY_JOIN, MarketingPreference.Source.COMMUNITY_SELF_SERVICE])
        self.assertEqual(self.client.patch(self.url, {"email_marketing": True}, format="json").data["email_marketing"]["state"], MarketingPreference.State.OPTED_IN)
        self.assertEqual(MarketingPreferenceHistory.objects.count(), 3)

    def test_repeated_same_state_is_idempotent_for_same_self_service_actor(self):
        self.authenticate()
        first = self.client.patch(self.url, {"email_marketing": True}, format="json")
        second = self.client.patch(self.url, {"email_marketing": True}, format="json")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(MarketingPreferenceHistory.objects.count(), 1)
        self.assertEqual(ExternalPersonSyncJob.objects.filter(person=self.person, job_type="EMAIL_MARKETING_PREFERENCE").count(), 1)

    def test_staff_history_is_preserved_when_member_makes_a_decision(self):
        self.authenticate()
        record_opt_in(person=self.person, source=MarketingPreference.Source.STAFF_RECORDED)
        self.client.patch(self.url, {"email_marketing": False}, format="json")
        self.assertEqual(MarketingPreferenceHistory.objects.values_list("source", flat=True).first(), MarketingPreference.Source.STAFF_RECORDED)
        self.assertEqual(MarketingPreferenceHistory.objects.count(), 2)

    def test_summary_states_and_domain_boundaries_remain_unchanged(self):
        self.authenticate()
        original_person = (self.person.primary_email, self.person.mobile)
        original_membership = (self.membership.status, self.membership.joined_at, self.membership.membership_source)
        original_privacy = (self.profile.directory_visible, self.profile.email_visible, self.profile.mobile_visible)
        password_hash = self.user.password
        self.client.patch(self.url, {"email_marketing": True}, format="json")
        self.person.refresh_from_db()
        self.membership.refresh_from_db()
        self.profile.refresh_from_db()
        self.user.refresh_from_db()
        self.assertEqual((self.person.primary_email, self.person.mobile), original_person)
        self.assertEqual((self.membership.status, self.membership.joined_at, self.membership.membership_source), original_membership)
        self.assertEqual((self.profile.directory_visible, self.profile.email_visible, self.profile.mobile_visible), original_privacy)
        self.assertEqual(self.user.password, password_hash)
        self.assertNotIn("marketing", {field.name for field in CommunityProfile._meta.get_fields()})

    def test_throttle_scope_and_default_rate_are_configured(self):
        self.assertEqual(CommunityAccountMarketingPreferenceView.throttle_scope, "community_account_marketing")
        self.assertEqual(settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]["community_account_marketing"], "10/hour")
