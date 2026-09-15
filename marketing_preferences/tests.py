from datetime import timedelta

from django.utils import timezone
from rest_framework.test import APIClient
from django.test import TestCase

from audit.models import AuditEvent
from marketing_preferences.models import MarketingPreference, MarketingPreferenceHistory
from marketing_preferences.services import get_effective_marketing_preference, record_opt_in, record_opt_out
from external_references.models import ExternalPersonSyncJob
from memberships.models import Membership
from people.models import Person
from staff_access.models import StaffRole, StaffRoleAssignment
from accounts.models import User


class MarketingPreferenceServiceTests(TestCase):
    def setUp(self):
        self.person = Person.objects.create(first_name="Ava", last_name="Example", primary_email="ava@example.com")
        self.recorded_at = timezone.now() - timedelta(days=1)

    def test_existing_person_membership_and_email_effectively_resolve_to_unknown(self):
        self.assertEqual(get_effective_marketing_preference(person=self.person).state, MarketingPreference.State.UNKNOWN)
        Membership.objects.create(
            person=self.person,
            status=Membership.Status.ACTIVE,
            joined_at=timezone.now().date(),
            membership_source=Membership.Source.MEMBERSHIP_FORM,
        )
        self.assertEqual(get_effective_marketing_preference(person=self.person).state, MarketingPreference.State.UNKNOWN)
        self.assertFalse(MarketingPreference.objects.exists())

    def test_explicit_opt_in_persists_source_time_and_audit(self):
        result = record_opt_in(
            person=self.person,
            source=MarketingPreference.Source.WEBSITE_SIGNUP,
            recorded_at=self.recorded_at,
        )
        self.assertEqual(result.preference.state, MarketingPreference.State.OPTED_IN)
        self.assertEqual(result.preference.source, MarketingPreference.Source.WEBSITE_SIGNUP)
        self.assertEqual(result.preference.recorded_at, self.recorded_at)
        self.assertEqual(MarketingPreferenceHistory.objects.count(), 1)
        audit = AuditEvent.objects.get(action=AuditEvent.Action.MARKETING_PREFERENCE_OPTED_IN)
        self.assertEqual(audit.metadata, {"person_id": str(self.person.id), "channel": "EMAIL"})
        self.assertEqual(ExternalPersonSyncJob.objects.count(), 1)
        self.assertEqual(ExternalPersonSyncJob.objects.get().source_event_id, MarketingPreferenceHistory.objects.get().id)
        self.assertEqual(ExternalPersonSyncJob.objects.get().provider, "BREVO")

    def test_opt_out_then_later_opt_in_preserves_history(self):
        record_opt_in(person=self.person)
        record_opt_out(person=self.person)
        record_opt_in(person=self.person, source=MarketingPreference.Source.STAFF_RECORDED)
        self.assertEqual(
            list(MarketingPreferenceHistory.objects.values_list("state", flat=True)),
            ["OPTED_IN", "OPTED_OUT", "OPTED_IN"],
        )
        self.assertEqual(get_effective_marketing_preference(person=self.person).state, MarketingPreference.State.OPTED_IN)

    def test_repeated_identical_operation_is_idempotent(self):
        first = record_opt_out(person=self.person)
        second = record_opt_out(person=self.person)
        self.assertTrue(first.changed)
        self.assertFalse(second.changed)
        self.assertEqual(MarketingPreferenceHistory.objects.count(), 1)
        self.assertEqual(AuditEvent.objects.filter(action=AuditEvent.Action.MARKETING_PREFERENCE_OPTED_OUT).count(), 1)
        self.assertEqual(ExternalPersonSyncJob.objects.count(), 1)

    def test_meaningful_opt_out_and_later_opt_in_queue_one_job_per_history_event(self):
        record_opt_out(person=self.person)
        record_opt_in(person=self.person)

        self.assertEqual(MarketingPreferenceHistory.objects.count(), 2)
        self.assertEqual(ExternalPersonSyncJob.objects.count(), 2)
        self.assertEqual(set(ExternalPersonSyncJob.objects.values_list("provider", flat=True)), {"BREVO"})


class MarketingPreferenceApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.person = Person.objects.create(first_name="Ava", last_name="Example")
        self.viewer = User.objects.create_user(email="viewer@example.com", password="password123", person_first_name="View", person_last_name="User")
        self.manager = User.objects.create_user(email="manager@example.com", password="password123", person_first_name="Manage", person_last_name="User")
        StaffRoleAssignment.objects.assign_role(user=self.viewer, role=StaffRole.objects.get(code=StaffRole.CRM_VIEWER))
        StaffRoleAssignment.objects.assign_role(user=self.manager, role=StaffRole.objects.get(code=StaffRole.CRM_MANAGER))
        self.url = f"/api/v1/people/{self.person.id}/marketing-preference/"

    def test_viewer_can_read_unknown_but_cannot_mutate(self):
        self.client.force_authenticate(self.viewer)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["state"], "UNKNOWN")
        self.assertEqual(self.client.post(self.url, {"state": "OPTED_IN"}, format="json").status_code, 403)

    def test_manager_can_record_staff_preference(self):
        self.client.force_authenticate(self.manager)
        response = self.client.post(self.url, {"state": "OPTED_IN"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["preference"]["source"], "STAFF_RECORDED")
