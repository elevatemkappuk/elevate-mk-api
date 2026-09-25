from unittest.mock import patch

from django.test import TestCase
from rest_framework.test import APIClient

from audit.models import AuditEvent
from marketing_preferences.models import MarketingPreference
from people.models import Person
from staff_access.models import StaffRole, StaffRoleAssignment
from accounts.models import User

from .models import Campaign, CampaignRecipientSnapshot


class CampaignFoundationApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = self._staff("admin@example.com", StaffRole.CRM_ADMIN)
        self.manager = self._staff("manager@example.com", StaffRole.CRM_MANAGER)
        self.viewer = self._staff("viewer@example.com", StaffRole.CRM_VIEWER)
        self.nonstaff = User.objects.create_user(email="nonstaff@example.com", password="password123", person_first_name="Non", person_last_name="Staff", person_record_type=Person.RecordType.TECHNICAL)
        self.create_url = "/api/v1/marketing/campaigns/"

        self.included = Person.objects.create(first_name="Included", last_name="Person", primary_email="INCLUDED@example.com")
        self.opted_out = Person.objects.create(first_name="Opted", last_name="Out", primary_email="out@example.com")
        self.unknown = Person.objects.create(first_name="Unknown", last_name="Consent", primary_email="unknown@example.com")
        self.no_email = Person.objects.create(first_name="No", last_name="Email", primary_email="")
        self._preference(self.included, MarketingPreference.State.OPTED_IN)
        self._preference(self.opted_out, MarketingPreference.State.OPTED_OUT)

    def _staff(self, email, role):
        user = User.objects.create_user(email=email, password="password123", person_first_name="Campaign", person_last_name="Staff", person_record_type=Person.RecordType.TECHNICAL)
        StaffRoleAssignment.objects.assign_role(user=user, role=StaffRole.objects.get(code=role))
        return user

    def _preference(self, person, state):
        MarketingPreference.objects.create(
            person=person,
            channel=MarketingPreference.Channel.EMAIL,
            state=state,
            source=MarketingPreference.Source.STAFF_RECORDED,
        )

    def authenticate(self, user):
        self.client.force_authenticate(user=user)

    def create_campaign(self, user=None, payload=None):
        self.authenticate(user or self.admin)
        return self.client.post(self.create_url, payload or {"name": "Quarterly update", "audience_selection": {}, "audience_ordering": "last_name"}, format="json")

    def test_admin_and_manager_can_create_but_viewer_cannot(self):
        self.assertEqual(self.create_campaign(self.admin).status_code, 201)
        self.assertEqual(self.create_campaign(self.manager).status_code, 201)
        self.assertEqual(self.create_campaign(self.viewer).status_code, 403)

    def test_creation_stores_only_normalized_selection_and_rejects_browser_results(self):
        response = self.create_campaign(payload={"name": "Normalized", "audience_selection": {"q": "  mentor ", "relationship": ["ACTIVE_MEMBER"]}, "selected_count": 99, "results": []})
        self.assertEqual(response.status_code, 400)
        response = self.create_campaign(payload={"name": "Normalized", "audience_selection": {"q": "  mentor ", "relationship": ["ACTIVE_MEMBER"]}})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["audience_selection"], {"q": "mentor", "relationship": ["ACTIVE_MEMBER"], "location": [], "industry": [], "career_stage": [], "interest": [], "skill": [], "tag": []})

    def test_prepare_reruns_stored_criteria_and_creates_snapshot_without_brevo(self):
        campaign = Campaign.objects.create(name="All People", audience_selection={"q": "", "relationship": [], "location": [], "industry": [], "career_stage": [], "interest": [], "skill": [], "tag": []}, audience_ordering="last_name", created_by=self.admin)
        # Simulate consent changing after the browser preview but before preparation.
        preference = MarketingPreference.objects.get(person=self.included)
        preference.state = MarketingPreference.State.OPTED_OUT
        preference.save(update_fields=["state", "updated_at"])
        with patch("brevo_marketing.client.BrevoMarketingClient") as brevo:
            self.authenticate(self.admin)
            response = self.client.post(f"{self.create_url}{campaign.id}/prepare/", {}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], Campaign.Status.SNAPSHOT_READY)
        self.assertFalse(brevo.called)
        preparation = campaign.preparations.get()
        rows = list(preparation.recipient_snapshots.order_by("person_id"))
        self.assertEqual(preparation.selected_count, len(rows))
        self.assertEqual(preparation.included_count, sum(row.decision == "INCLUDED" for row in rows))
        self.assertEqual(preparation.excluded_count, sum(row.decision == "EXCLUDED" for row in rows))
        reasons = {row.person_id: row.exclusion_reason for row in rows}
        self.assertEqual(reasons[self.opted_out.id], "EXCLUDED_OPTED_OUT")
        self.assertEqual(reasons[self.unknown.id], "EXCLUDED_CONSENT_UNKNOWN")
        self.assertEqual(reasons[self.no_email.id], "EXCLUDED_NO_EMAIL")
        self.assertEqual(rows[0].email_snapshot, rows[0].person.primary_email.strip().lower())
        self.assertTrue(AuditEvent.objects.filter(action=AuditEvent.Action.CAMPAIGN_RECIPIENT_SNAPSHOT_CREATED).exists())

    def test_snapshot_evidence_is_immutable_and_person_changes_do_not_mutate_it(self):
        response = self.create_campaign()
        campaign_id = response.data["id"]
        self.authenticate(self.admin)
        self.assertEqual(self.client.post(f"{self.create_url}{campaign_id}/prepare/", {}, format="json").status_code, 200)
        snapshot = CampaignRecipientSnapshot.objects.get(person=self.included)
        original_name = snapshot.first_name_snapshot
        self.included.first_name = "Changed"
        self.included.save(update_fields=["first_name", "updated_at"])
        snapshot.refresh_from_db()
        self.assertEqual(snapshot.first_name_snapshot, original_name)
        snapshot.first_name_snapshot = "Tampered"
        with self.assertRaises(Exception):
            snapshot.save()

    def test_double_prepare_is_rejected(self):
        response = self.create_campaign()
        self.authenticate(self.admin)
        url = f"{self.create_url}{response.data['id']}/prepare/"
        self.assertEqual(self.client.post(url, {}, format="json").status_code, 200)
        self.assertEqual(self.client.post(url, {}, format="json").status_code, 409)

    def test_campaign_and_recipients_are_readable_by_viewer(self):
        response = self.create_campaign()
        self.authenticate(self.viewer)
        self.assertEqual(self.client.get(f"{self.create_url}{response.data['id']}/").status_code, 200)
        self.assertEqual(self.client.get(self.create_url).status_code, 200)
