from django.conf import settings
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from audit.models import AuditEvent
from brevo_marketing.jobs import PERSON_PROFILE_SYNC
from brevo_marketing.routing import BREVO_PROVIDER
from community.models import CommunityProfile
from community.views import CommunityAccountMobileView
from external_references.models import ExternalPersonSyncJob
from memberships.models import Membership
from people.models import Person


class CommunityAccountMobileApiTests(TestCase):
    url = "/api/v1/community/account/mobile/"
    current_mobile = "+447123456789"
    replacement_mobile = "+447123456780"

    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.csrf_client = APIClient(enforce_csrf_checks=True)
        self.person = Person.objects.create(
            first_name="Amina",
            last_name="Zulu",
            primary_email="mobile-member@example.com",
            mobile="",
            record_type=Person.RecordType.BUSINESS,
        )
        Membership.objects.create(
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

    def payload(self, mobile=None, phone_region="GB"):
        return {"mobile": self.current_mobile if mobile is None else mobile, "phone_region": phone_region}

    def test_unauthenticated_and_ineligible_requests_are_rejected(self):
        response = self.client.patch(self.url, self.payload(), format="json")
        self.assertEqual(response.status_code, 401)
        self.authenticate()
        self.person.membership.status = Membership.Status.FORMER
        self.person.membership.save(update_fields=["status"])
        response = self.client.patch(self.url, self.payload(), format="json")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["code"], "COMMUNITY_ACCESS_UNAVAILABLE")

    def test_csrf_is_required(self):
        self.authenticate(self.csrf_client)
        response = self.csrf_client.patch(self.url, self.payload(), format="json")
        self.assertEqual(response.status_code, 403)

    def test_valid_national_and_international_numbers_are_stored_as_e164(self):
        self.authenticate()
        response = self.client.patch(self.url, {"mobile": "07123 456 789", "phone_region": "gb"}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.person.refresh_from_db()
        self.assertEqual(self.person.mobile, "+447123456789")
        self.assertFalse(response.data["mobile"]["masked"] == self.person.mobile)
        self.assertEqual(self.client.patch(self.url, self.payload(self.replacement_mobile), format="json").status_code, 200)
        self.person.refresh_from_db()
        self.assertEqual(self.person.mobile, self.replacement_mobile)

    def test_mobile_validation_rejects_missing_or_invalid_region_and_number(self):
        self.authenticate()
        cases = [
            ({"mobile": "07123456789", "phone_region": ""}, "phone_region"),
            ({"mobile": "07123456789", "phone_region": "ZZ"}, "phone_region"),
            ({"mobile": "07123", "phone_region": "GB"}, "mobile"),
            ({"mobile": "+233241234567", "phone_region": "GB"}, "mobile"),
            ({"mobile": "", "phone_region": "GB"}, "phone_region"),
        ]
        for payload, field in cases:
            response = self.client.patch(self.url, payload, format="json")
            self.assertEqual(response.status_code, 400)
            self.assertIn(field, response.data["fields"])

    def test_phone_region_is_validation_only_and_state_is_preserved(self):
        self.authenticate()
        response = self.client.patch(self.url, self.payload(), format="json")
        self.assertEqual(response.status_code, 200)
        self.person.refresh_from_db()
        self.profile.refresh_from_db()
        self.assertEqual(self.person.mobile, self.current_mobile)
        self.assertFalse(hasattr(self.person, "phone_region"))
        self.assertFalse(self.profile.directory_visible)
        self.assertTrue(self.profile.email_visible)
        self.assertTrue(self.profile.mobile_visible)

    def test_collision_with_e164_or_legacy_equivalent_mobile_is_blocked_safely(self):
        self.authenticate()
        Person.objects.create(first_name="Other", last_name="Member", mobile=self.current_mobile)
        response = self.client.patch(self.url, self.payload(), format="json")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["code"], "MOBILE_UPDATE_UNAVAILABLE")
        self.assertNotIn("Other", repr(response.data))
        self.assertNotIn("mobile-member@example.com", repr(response.data))
        self.person.refresh_from_db()
        self.assertEqual(self.person.mobile, "")

        Person.objects.all().exclude(pk=self.person.pk).delete()
        Person.objects.create(first_name="Legacy", last_name="Member", mobile="07123 456 789")
        response = self.client.patch(self.url, self.payload(), format="json")
        self.assertEqual(response.status_code, 409)

    def test_invalid_legacy_mobile_does_not_crash_collision_check(self):
        self.authenticate()
        Person.objects.create(first_name="Legacy", last_name="Invalid", mobile="not-a-phone")
        response = self.client.patch(self.url, self.payload(), format="json")
        self.assertEqual(response.status_code, 200)
        self.person.refresh_from_db()
        self.assertEqual(self.person.mobile, self.current_mobile)

    def test_same_effective_mobile_is_a_noop_without_audit_or_sync(self):
        self.person.mobile = "07123 456 789"
        self.person.save(update_fields=["mobile"])
        self.authenticate()
        before_audit = AuditEvent.objects.filter(entity_type="Person", entity_id=str(self.person.id)).count()
        before_jobs = ExternalPersonSyncJob.objects.filter(person=self.person).count()
        response = self.client.patch(self.url, {"mobile": "+447123456789", "phone_region": "GB"}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.person.refresh_from_db()
        self.assertEqual(self.person.mobile, "07123 456 789")
        self.assertEqual(AuditEvent.objects.filter(entity_type="Person", entity_id=str(self.person.id)).count(), before_audit)
        self.assertEqual(ExternalPersonSyncJob.objects.filter(person=self.person).count(), before_jobs)

    def test_removing_empty_mobile_is_an_idempotent_noop(self):
        self.authenticate()
        before_audit = AuditEvent.objects.filter(entity_type="Person", entity_id=str(self.person.id)).count()
        response = self.client.patch(self.url, {"mobile": "", "phone_region": ""}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["mobile"]["present"])
        self.assertEqual(AuditEvent.objects.filter(entity_type="Person", entity_id=str(self.person.id)).count(), before_audit)
        self.assertFalse(ExternalPersonSyncJob.objects.filter(person=self.person).exists())

    def test_genuine_add_change_and_removal_audit_and_enqueue_profile_sync(self):
        self.authenticate()
        add = self.client.patch(self.url, self.payload(), format="json")
        self.assertEqual(add.status_code, 200)
        self.assertEqual(ExternalPersonSyncJob.objects.filter(person=self.person, job_type=PERSON_PROFILE_SYNC).count(), 1)
        event = AuditEvent.objects.filter(entity_type="Person", entity_id=str(self.person.id)).latest("id")
        self.assertEqual(event.action, AuditEvent.Action.PERSON_UPDATED)
        self.assertEqual(event.metadata["source"], "COMMUNITY_SELF_SERVICE")
        self.assertNotIn(self.current_mobile, repr(event.changes))
        self.assertNotIn(self.current_mobile, repr(event.metadata))

        change = self.client.patch(self.url, self.payload(self.replacement_mobile), format="json")
        self.assertEqual(change.status_code, 200)
        self.assertEqual(ExternalPersonSyncJob.objects.filter(person=self.person, job_type=PERSON_PROFILE_SYNC).count(), 1)

        remove = self.client.patch(self.url, {"mobile": "", "phone_region": ""}, format="json")
        self.assertEqual(remove.status_code, 200)
        self.person.refresh_from_db()
        self.assertEqual(self.person.mobile, "")
        self.assertFalse(remove.data["mobile"]["present"])
        self.assertEqual(ExternalPersonSyncJob.objects.filter(person=self.person, job_type=PERSON_PROFILE_SYNC).count(), 1)
        self.assertEqual(self.profile.mobile_visible, True)

    def test_email_password_marketing_and_throttle_contract_remain_unchanged(self):
        self.authenticate()
        password_hash = self.user.password
        response = self.client.patch(self.url, self.payload(), format="json")
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "mobile-member@example.com")
        self.assertEqual(self.user.password, password_hash)
        self.assertEqual(CommunityAccountMobileView.throttle_scope, "community_account_mobile")
        self.assertEqual(settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]["community_account_mobile"], "10/hour")
