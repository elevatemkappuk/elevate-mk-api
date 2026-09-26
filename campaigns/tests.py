from unittest.mock import Mock, patch
from types import SimpleNamespace

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from audit.models import AuditEvent
from marketing_preferences.models import MarketingPreference
from people.models import Person
from staff_access.models import StaffRole, StaffRoleAssignment
from accounts.models import User

from .models import Campaign, CampaignPreparation, CampaignRecipientSnapshot
from .services import prepare_campaign_provider
from brevo_marketing.exceptions import BrevoMarketingPropagationDelay
from brevo_marketing.sync import BrevoPersonSyncOutcome, BrevoPersonSyncResult


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

    def _snapshot_ready_campaign(self):
        response = self.create_campaign()
        self.authenticate(self.admin)
        self.assertEqual(self.client.post(f"{self.create_url}{response.data['id']}/prepare/", {}, format="json").status_code, 200)
        return Campaign.objects.get(pk=response.data["id"])

    def _provider_client(self):
        client = Mock()
        client.create_campaign_list.return_value = SimpleNamespace(list_id=55)
        client.find_draft_campaign_by_name.return_value = None
        client.create_email_campaign_draft.return_value = SimpleNamespace(campaign_id=77)
        return client

    @patch("campaigns.services.synchronize_person_to_brevo")
    def test_provider_preparation_rechecks_consent_and_does_not_add_post_snapshot_opt_out(self, sync):
        campaign = self._snapshot_ready_campaign()
        preference = MarketingPreference.objects.get(person=self.included)
        preference.state = MarketingPreference.State.OPTED_OUT
        preference.save(update_fields=["state", "updated_at"])
        client = self._provider_client()
        result = prepare_campaign_provider(campaign_id=campaign.id, actor_user=self.admin, client=client, sleep_fn=lambda _seconds: None)
        result.refresh_from_db()
        self.assertEqual(result.status, Campaign.Status.NO_READY_RECIPIENTS)
        self.assertEqual(result.current_preparation.status, "NO_READY_RECIPIENTS")
        self.assertFalse(client.create_campaign_list.called)
        self.assertFalse(sync.called)
        snapshot = result.current_preparation.recipient_snapshots.get(person=self.included)
        self.assertEqual(snapshot.decision, CampaignRecipientSnapshot.Decision.INCLUDED)
        self.assertEqual(snapshot.provider_outcome, "SKIPPED_CURRENT_CONSENT")

    @patch("campaigns.services.synchronize_person_to_brevo")
    def test_provider_preparation_uses_dedicated_list_and_creates_prepared_draft(self, sync):
        campaign = self._snapshot_ready_campaign()
        sync.return_value = BrevoPersonSyncResult(person_id=self.included.id, outcome=BrevoPersonSyncOutcome.ALREADY_SYNCHRONIZED, contact_id=123)
        client = self._provider_client()
        result = prepare_campaign_provider(campaign_id=campaign.id, actor_user=self.admin, client=client, sleep_fn=lambda _seconds: None)
        result.refresh_from_db()
        preparation = result.current_preparation
        self.assertEqual(result.status, Campaign.Status.PREPARED)
        self.assertEqual(preparation.status, "PREPARED")
        self.assertEqual(preparation.brevo_list_id, 55)
        self.assertEqual(preparation.brevo_campaign_id, 77)
        client.add_contact_to_list.assert_called_once_with(list_id=55, contact_id=123)
        self.assertNotEqual(client.add_contact_to_list.call_args.kwargs["list_id"], 2)
        client.create_email_campaign_draft.assert_called_once()
        self.assertEqual(client.create_email_campaign_draft.call_args.kwargs["list_id"], 55)
        self.assertEqual(client.create_email_campaign_draft.call_args.kwargs["subject"], campaign.name)
        self.assertEqual(client.create_email_campaign_draft.call_args.kwargs["template_id"], "16")
        self.assertEqual(preparation.recipient_snapshots.get(person=self.included).provider_outcome, "ADDED_TO_CAMPAIGN_LIST")

    @patch("campaigns.services.synchronize_person_to_brevo")
    def test_provider_retry_reuses_list_and_successful_contact(self, sync):
        campaign = self._snapshot_ready_campaign()
        preparation = campaign.current_preparation
        preparation.brevo_list_id = 55
        preparation.status = "PROVIDER_FAILED"
        preparation.save(update_fields=["brevo_list_id", "status", "updated_at"])
        campaign.status = Campaign.Status.PROVIDER_FAILED
        campaign.save(update_fields=["status", "updated_at"])
        snapshot = preparation.recipient_snapshots.get(person=self.included)
        snapshot.brevo_contact_id = 123
        snapshot.provider_outcome = "CONTACT_READY"
        snapshot.save(update_fields=["brevo_contact_id", "provider_outcome"])
        client = self._provider_client()
        result = prepare_campaign_provider(campaign_id=campaign.id, actor_user=self.admin, client=client, sleep_fn=lambda _seconds: None)
        self.assertEqual(result.status, Campaign.Status.PREPARED)
        client.create_campaign_list.assert_not_called()
        sync.assert_not_called()
        client.add_contact_to_list.assert_called_once_with(list_id=55, contact_id=123)

    @patch("campaigns.services.synchronize_person_to_brevo")
    def test_provider_retry_reuses_dedicated_list_and_added_recipient_after_partial_failure(self, sync):
        campaign = self._snapshot_ready_campaign()
        preparation = campaign.current_preparation
        preparation.brevo_list_id = 55
        preparation.status = "PROVIDER_FAILED"
        preparation.save(update_fields=["brevo_list_id", "status", "updated_at"])
        campaign.status = Campaign.Status.PROVIDER_FAILED
        campaign.save(update_fields=["status", "updated_at"])
        snapshot = preparation.recipient_snapshots.get(person=self.included)
        snapshot.brevo_contact_id = 123
        snapshot.provider_outcome = "ADDED_TO_CAMPAIGN_LIST"
        snapshot.save(update_fields=["brevo_contact_id", "provider_outcome"])
        client = self._provider_client()

        result = prepare_campaign_provider(campaign_id=campaign.id, actor_user=self.admin, client=client, sleep_fn=lambda _seconds: None)

        self.assertEqual(result.status, Campaign.Status.PREPARED)
        client.create_campaign_list.assert_not_called()
        client.add_contact_to_list.assert_not_called()
        client.create_email_campaign_draft.assert_called_once()
        self.assertEqual(client.create_email_campaign_draft.call_args.kwargs["list_id"], 55)
        sync.assert_not_called()

    @patch("campaigns.services.synchronize_person_to_brevo")
    def test_provider_preparation_reuses_existing_draft_without_creating_another(self, sync):
        campaign = self._snapshot_ready_campaign()
        sync.return_value = BrevoPersonSyncResult(person_id=self.included.id, outcome=BrevoPersonSyncOutcome.ALREADY_SYNCHRONIZED, contact_id=123)
        client = self._provider_client()
        client.find_draft_campaign_by_name.return_value = SimpleNamespace(campaign_id=88, status="draft")

        result = prepare_campaign_provider(campaign_id=campaign.id, actor_user=self.admin, client=client, sleep_fn=lambda _seconds: None)

        self.assertEqual(result.status, Campaign.Status.PREPARED)
        self.assertEqual(result.current_preparation.brevo_campaign_id, 88)
        client.create_email_campaign_draft.assert_not_called()
        client.create_campaign_list.assert_called_once()

    @patch("campaigns.services.synchronize_person_to_brevo")
    def test_propagation_delay_retries_campaign_creation_and_unrelated_provider_failure_does_not_loop(self, sync):
        campaign = self._snapshot_ready_campaign()
        sync.return_value = BrevoPersonSyncResult(person_id=self.included.id, outcome=BrevoPersonSyncOutcome.ALREADY_SYNCHRONIZED, contact_id=123)
        client = self._provider_client()
        client.create_email_campaign_draft.side_effect = [BrevoMarketingPropagationDelay("There are no contacts associated with the given recipients info"), SimpleNamespace(campaign_id=77)]
        sleeps = []
        result = prepare_campaign_provider(campaign_id=campaign.id, actor_user=self.admin, client=client, sleep_fn=sleeps.append, backoff_seconds=(0, 1))
        self.assertEqual(result.status, Campaign.Status.PREPARED)
        self.assertEqual(client.create_email_campaign_draft.call_count, 2)
        self.assertEqual(sleeps, [1])

    @patch("campaigns.services.synchronize_person_to_brevo")
    def test_reconciliation_does_not_create_campaign_draft(self, sync):
        campaign = self._snapshot_ready_campaign()
        sync.return_value = BrevoPersonSyncResult(person_id=self.included.id, outcome=BrevoPersonSyncOutcome.RECONCILIATION_REQUIRED, reason="BREVO_CONTACT_RESTRICTED")
        client = self._provider_client()
        result = prepare_campaign_provider(campaign_id=campaign.id, actor_user=self.admin, client=client, sleep_fn=lambda _seconds: None)
        self.assertEqual(result.status, Campaign.Status.RECONCILIATION_REQUIRED)
        self.assertEqual(campaign.current_preparation.recipient_snapshots.get(person=self.included).provider_error_code, "BREVO_CONTACT_RESTRICTED")
        client.create_email_campaign_draft.assert_not_called()

    @patch("campaigns.services.synchronize_person_to_brevo")
    def test_missing_referenced_contact_persists_safe_reconciliation_code(self, sync):
        campaign = self._snapshot_ready_campaign()
        self._mark_reconciliation_required(campaign)
        sync.return_value = BrevoPersonSyncResult(
            person_id=self.included.id,
            outcome=BrevoPersonSyncOutcome.RECONCILIATION_REQUIRED,
            reason="BREVO_CONTACT_NOT_FOUND_FOR_EXISTING_REFERENCE",
        )
        client = self._provider_client()

        prepare_campaign_provider(campaign_id=campaign.id, actor_user=self.admin, client=client, sleep_fn=lambda _seconds: None)

        snapshot = campaign.current_preparation.recipient_snapshots.get(person=self.included)
        self.assertEqual(snapshot.provider_error_code, "BREVO_CONTACT_NOT_FOUND_FOR_EXISTING_REFERENCE")
        self.assertIsNone(snapshot.provider_error_message)

    def _mark_reconciliation_required(self, campaign, *, list_id=55):
        preparation = campaign.current_preparation
        preparation.brevo_list_id = list_id
        preparation.status = CampaignPreparation.Status.RECONCILIATION_REQUIRED
        preparation.save(update_fields=["brevo_list_id", "status", "updated_at"])
        campaign.status = Campaign.Status.RECONCILIATION_REQUIRED
        campaign.save(update_fields=["status", "updated_at"])
        snapshot = preparation.recipient_snapshots.get(person=self.included)
        snapshot.brevo_contact_id = 999
        snapshot.provider_outcome = "RECONCILIATION_REQUIRED"
        snapshot.provider_error_code = "BREVO_CONTACT_RESTRICTED"
        snapshot.save(update_fields=["brevo_contact_id", "provider_outcome", "provider_error_code"])
        return preparation, snapshot

    @patch("campaigns.services.synchronize_person_to_brevo")
    def test_reconciliation_retry_reuses_preparation_list_and_re_evaluates_recipient(self, sync):
        campaign = self._snapshot_ready_campaign()
        preparation, snapshot = self._mark_reconciliation_required(campaign)
        sync.return_value = BrevoPersonSyncResult(person_id=self.included.id, outcome=BrevoPersonSyncOutcome.ALREADY_SYNCHRONIZED, contact_id=123)
        client = self._provider_client()

        result = prepare_campaign_provider(campaign_id=campaign.id, actor_user=self.admin, client=client, sleep_fn=lambda _seconds: None)

        result.refresh_from_db()
        preparation.refresh_from_db()
        snapshot.refresh_from_db()
        self.assertEqual(result.status, Campaign.Status.PREPARED)
        self.assertEqual(preparation.status, CampaignPreparation.Status.PREPARED)
        self.assertEqual(preparation.id, campaign.current_preparation_id)
        self.assertEqual(preparation.brevo_list_id, 55)
        self.assertEqual(snapshot.provider_outcome, "ADDED_TO_CAMPAIGN_LIST")
        client.create_campaign_list.assert_not_called()
        client.add_contact_to_list.assert_called_once_with(list_id=55, contact_id=123)
        client.create_email_campaign_draft.assert_called_once()
        sync.assert_called_once_with(person_id=self.included.id, client=client, actor_user=self.admin)
        retry_event = AuditEvent.objects.filter(action=AuditEvent.Action.CAMPAIGN_PROVIDER_RETRY).latest("occurred_at")
        self.assertEqual(retry_event.metadata["retry_kind"], "RECONCILIATION")

    @patch("campaigns.services.synchronize_person_to_brevo")
    def test_unresolved_reconciliation_is_safe_and_blocks_draft(self, sync):
        campaign = self._snapshot_ready_campaign()
        preparation, snapshot = self._mark_reconciliation_required(campaign)
        sync.return_value = BrevoPersonSyncResult(person_id=self.included.id, outcome=BrevoPersonSyncOutcome.RECONCILIATION_REQUIRED, reason="BREVO_CONTACT_RESTRICTED")
        client = self._provider_client()

        result = prepare_campaign_provider(campaign_id=campaign.id, actor_user=self.admin, client=client, sleep_fn=lambda _seconds: None)

        result.refresh_from_db()
        preparation.refresh_from_db()
        snapshot.refresh_from_db()
        self.assertEqual(result.status, Campaign.Status.RECONCILIATION_REQUIRED)
        self.assertEqual(preparation.recipient_snapshots.filter(provider_outcome="ADDED_TO_CAMPAIGN_LIST").count(), 0)
        self.assertEqual(snapshot.provider_outcome, "RECONCILIATION_REQUIRED")
        self.assertEqual(snapshot.provider_error_code, "BREVO_CONTACT_RESTRICTED")
        client.create_campaign_list.assert_not_called()
        client.create_email_campaign_draft.assert_not_called()

    def test_reconciliation_retry_is_available_to_manager_but_not_viewer(self):
        campaign = self._snapshot_ready_campaign()
        self._mark_reconciliation_required(campaign)
        url = f"{self.create_url}{campaign.id}/prepare-provider/"
        with patch("campaigns.views.prepare_campaign_provider", return_value=campaign):
            self.authenticate(self.manager)
            self.assertEqual(self.client.post(url, {}, format="json").status_code, 200)
        self.authenticate(self.viewer)
        self.assertEqual(self.client.post(url, {}, format="json").status_code, 403)

    def test_recipient_api_exposes_safe_code_without_provider_identity_or_raw_message(self):
        campaign = self._snapshot_ready_campaign()
        preparation = campaign.current_preparation
        snapshot = preparation.recipient_snapshots.get(person=self.included)
        snapshot.provider_outcome = "RECONCILIATION_REQUIRED"
        snapshot.provider_error_code = "BREVO_CONTACT_NOT_FOUND_FOR_EXISTING_REFERENCE"
        snapshot.provider_error_message = "raw provider detail must not be exposed"
        snapshot.brevo_contact_id = 123
        snapshot.save(update_fields=["provider_outcome", "provider_error_code", "provider_error_message", "brevo_contact_id"])
        self.authenticate(self.viewer)

        response = self.client.get(f"{self.create_url}{campaign.id}/recipients/?page_size=100")

        self.assertEqual(response.status_code, 200)
        row = next(item for item in response.data["results"] if item["id"] == snapshot.id)
        self.assertEqual(row["provider_error_code"], "BREVO_CONTACT_NOT_FOUND_FOR_EXISTING_REFERENCE")
        self.assertNotIn("brevo_contact_id", row)
        self.assertNotIn("provider_error_message", row)

    @patch("campaigns.services.synchronize_person_to_brevo")
    def test_reconciliation_retry_respects_current_consent_without_provider_sync(self, sync):
        campaign = self._snapshot_ready_campaign()
        preparation, snapshot = self._mark_reconciliation_required(campaign)
        preference = MarketingPreference.objects.get(person=self.included)
        preference.state = MarketingPreference.State.OPTED_OUT
        preference.save(update_fields=["state", "updated_at"])
        client = self._provider_client()

        result = prepare_campaign_provider(campaign_id=campaign.id, actor_user=self.admin, client=client, sleep_fn=lambda _seconds: None)

        result.refresh_from_db()
        preparation.refresh_from_db()
        snapshot.refresh_from_db()
        self.assertEqual(result.status, Campaign.Status.NO_READY_RECIPIENTS)
        self.assertEqual(snapshot.decision, CampaignRecipientSnapshot.Decision.INCLUDED)
        self.assertEqual(snapshot.provider_outcome, "SKIPPED_CURRENT_CONSENT")
        self.assertFalse(sync.called)
        client.add_contact_to_list.assert_not_called()
        client.create_email_campaign_draft.assert_not_called()

    @patch("campaigns.services.synchronize_person_to_brevo")
    def test_reconciliation_retry_reuses_completed_recipients_and_is_idempotent(self, sync):
        other = Person.objects.create(first_name="Other", last_name="Included", primary_email="other-included@example.com")
        self._preference(other, MarketingPreference.State.OPTED_IN)
        campaign = self._snapshot_ready_campaign()
        preparation, _snapshot = self._mark_reconciliation_required(campaign)
        completed = preparation.recipient_snapshots.get(person=other)
        completed.brevo_contact_id = 321
        completed.provider_outcome = "ADDED_TO_CAMPAIGN_LIST"
        completed.save(update_fields=["brevo_contact_id", "provider_outcome"])
        sync.return_value = BrevoPersonSyncResult(person_id=self.included.id, outcome=BrevoPersonSyncOutcome.ALREADY_SYNCHRONIZED, contact_id=123)
        client = self._provider_client()

        first = prepare_campaign_provider(campaign_id=campaign.id, actor_user=self.admin, client=client, sleep_fn=lambda _seconds: None)
        self.assertEqual(first.status, Campaign.Status.PREPARED)
        self.assertEqual(sync.call_count, 1)
        client.add_contact_to_list.assert_called_once_with(list_id=55, contact_id=123)

        self.assertRaises(RuntimeError, prepare_campaign_provider, campaign_id=campaign.id, actor_user=self.admin, client=client, sleep_fn=lambda _seconds: None)

    @patch("campaigns.services.synchronize_person_to_brevo")
    def test_repeated_unresolved_reconciliation_retry_reuses_list_without_draft(self, sync):
        campaign = self._snapshot_ready_campaign()
        self._mark_reconciliation_required(campaign)
        sync.return_value = BrevoPersonSyncResult(person_id=self.included.id, outcome=BrevoPersonSyncOutcome.RECONCILIATION_REQUIRED, reason="BREVO_CONTACT_RESTRICTED")
        client = self._provider_client()

        first = prepare_campaign_provider(campaign_id=campaign.id, actor_user=self.admin, client=client, sleep_fn=lambda _seconds: None)
        second = prepare_campaign_provider(campaign_id=campaign.id, actor_user=self.admin, client=client, sleep_fn=lambda _seconds: None)

        self.assertEqual(first.status, Campaign.Status.RECONCILIATION_REQUIRED)
        self.assertEqual(second.status, Campaign.Status.RECONCILIATION_REQUIRED)
        self.assertEqual(sync.call_count, 2)
        client.create_campaign_list.assert_not_called()
        client.create_email_campaign_draft.assert_not_called()

    def test_admin_and_manager_can_archive_and_archive_is_idempotent(self):
        campaign = Campaign.objects.create(name="Archive me", created_by=self.admin)
        self.authenticate(self.admin)
        url = f"{self.create_url}{campaign.id}/archive/"
        response = self.client.post(url, {}, format="json")
        self.assertEqual(response.status_code, 200)
        campaign.refresh_from_db()
        archived_at = campaign.archived_at
        self.assertIsNotNone(archived_at)
        self.assertTrue(response.data["is_archived"])
        self.assertEqual(response.data["status"], Campaign.Status.DRAFT)
        self.assertFalse(response.data["can_archive"])
        self.assertTrue(response.data["can_restore"])
        self.assertTrue(response.data["can_delete"])
        self.assertEqual(self.client.post(url, {}, format="json").status_code, 200)
        campaign.refresh_from_db()
        self.assertEqual(campaign.archived_at, archived_at)

        campaign = Campaign.objects.create(name="Manager archive", created_by=self.manager)
        self.authenticate(self.manager)
        self.assertEqual(self.client.post(f"{self.create_url}{campaign.id}/archive/", {}, format="json").status_code, 200)

    def test_viewer_cannot_archive_or_restore_and_unauthenticated_is_rejected(self):
        campaign = Campaign.objects.create(name="Protected lifecycle", created_by=self.admin)
        self.authenticate(self.viewer)
        self.assertEqual(self.client.post(f"{self.create_url}{campaign.id}/archive/", {}, format="json").status_code, 403)
        self.assertEqual(self.client.post(f"{self.create_url}{campaign.id}/restore/", {}, format="json").status_code, 403)
        self.authenticate(self.nonstaff)
        self.assertEqual(self.client.post(f"{self.create_url}{campaign.id}/archive/", {}, format="json").status_code, 403)
        self.assertEqual(self.client.delete(f"{self.create_url}{campaign.id}/").status_code, 403)
        self.client.force_authenticate(user=None)
        self.assertEqual(self.client.post(f"{self.create_url}{campaign.id}/archive/", {}, format="json").status_code, 401)

    def test_archive_filters_default_archived_and_all_and_direct_read_preserve_state(self):
        active = Campaign.objects.create(name="Active campaign", created_by=self.admin)
        archived = Campaign.objects.create(name="Archived campaign", created_by=self.admin, archived_at=timezone.now())
        self.authenticate(self.viewer)
        self.assertEqual(self.client.get(self.create_url).data["count"], 1)
        self.assertEqual(self.client.get(f"{self.create_url}?lifecycle=archived").data["results"][0]["id"], archived.id)
        self.assertEqual(self.client.get(f"{self.create_url}?lifecycle=all").data["count"], 2)
        self.assertEqual(self.client.get(f"{self.create_url}{archived.id}/").status_code, 200)
        self.assertEqual(self.client.get(f"{self.create_url}{archived.id}/").data["status"], Campaign.Status.DRAFT)
        self.assertEqual(self.client.get(f"{self.create_url}?lifecycle=invalid").status_code, 400)
        self.assertNotEqual(active.id, archived.id)

    def test_archive_preserves_preparation_snapshot_and_provider_references_without_provider_work(self):
        campaign = self._snapshot_ready_campaign()
        preparation = campaign.current_preparation
        preparation.brevo_list_id = 55
        preparation.brevo_campaign_id = 77
        preparation.save(update_fields=["brevo_list_id", "brevo_campaign_id", "updated_at"])
        snapshot_id = preparation.recipient_snapshots.order_by("id").first().id
        self.authenticate(self.admin)
        with patch("campaigns.services.BrevoMarketingClient") as client:
            response = self.client.post(f"{self.create_url}{campaign.id}/archive/", {}, format="json")
        self.assertEqual(response.status_code, 200)
        campaign.refresh_from_db()
        self.assertTrue(campaign.is_archived)
        self.assertEqual(campaign.status, Campaign.Status.SNAPSHOT_READY)
        self.assertEqual(campaign.current_preparation_id, preparation.id)
        preparation.refresh_from_db()
        self.assertEqual(preparation.recipient_snapshots.order_by("id").first().id, snapshot_id)
        self.assertEqual(preparation.brevo_list_id, 55)
        self.assertEqual(preparation.brevo_campaign_id, 77)
        self.assertFalse(client.called)

    def test_archived_campaign_blocks_snapshot_and_provider_preparation(self):
        campaign = Campaign.objects.create(name="Archived workflow", created_by=self.admin, archived_at=timezone.now())
        self.authenticate(self.admin)
        response = self.client.post(f"{self.create_url}{campaign.id}/prepare/", {}, format="json")
        self.assertEqual(response.status_code, 409)
        with patch("campaigns.services.BrevoMarketingClient") as client:
            response = self.client.post(f"{self.create_url}{campaign.id}/prepare-provider/", {}, format="json")
        self.assertEqual(response.status_code, 409)
        self.assertFalse(client.called)

    def test_admin_and_manager_restore_idempotently_and_preserve_workflow_state(self):
        campaign = Campaign.objects.create(name="Restore me", status=Campaign.Status.SNAPSHOT_READY, created_by=self.admin, archived_at=timezone.now())
        self.authenticate(self.manager)
        url = f"{self.create_url}{campaign.id}/restore/"
        response = self.client.post(url, {}, format="json")
        self.assertEqual(response.status_code, 200)
        campaign.refresh_from_db()
        self.assertIsNone(campaign.archived_at)
        self.assertEqual(campaign.status, Campaign.Status.SNAPSHOT_READY)
        self.assertTrue(response.data["can_archive"])
        self.assertFalse(response.data["can_restore"])
        self.assertEqual(self.client.post(url, {}, format="json").status_code, 200)
        self.assertEqual(AuditEvent.objects.filter(action=AuditEvent.Action.CAMPAIGN_RESTORED, entity_id=str(campaign.id)).count(), 1)
        self.assertEqual(AuditEvent.objects.filter(action=AuditEvent.Action.CAMPAIGN_ARCHIVED, entity_id=str(campaign.id)).count(), 0)

    def test_archive_and_restore_audit_transitions_are_recorded(self):
        campaign = Campaign.objects.create(name="Audited lifecycle", created_by=self.admin)
        self.authenticate(self.admin)
        self.client.post(f"{self.create_url}{campaign.id}/archive/", {}, format="json")
        self.client.post(f"{self.create_url}{campaign.id}/restore/", {}, format="json")
        self.assertEqual(AuditEvent.objects.filter(action=AuditEvent.Action.CAMPAIGN_ARCHIVED, entity_id=str(campaign.id)).count(), 1)
        self.assertEqual(AuditEvent.objects.filter(action=AuditEvent.Action.CAMPAIGN_RESTORED, entity_id=str(campaign.id)).count(), 1)

    def test_unused_draft_can_be_deleted_and_records_audit_without_provider_work(self):
        campaign = Campaign.objects.create(name="Delete me", created_by=self.admin)
        self.authenticate(self.admin)
        with patch("campaigns.services.BrevoMarketingClient") as client:
            response = self.client.delete(f"{self.create_url}{campaign.id}/")
        self.assertEqual(response.status_code, 204)
        self.assertFalse(Campaign.objects.filter(pk=campaign.id).exists())
        self.assertTrue(AuditEvent.objects.filter(action=AuditEvent.Action.CAMPAIGN_DELETED, entity_id=str(campaign.id)).exists())
        self.assertFalse(client.called)

    def test_snapshot_preparation_provider_evidence_and_archived_prepared_campaign_cannot_be_deleted(self):
        campaign = self._snapshot_ready_campaign()
        self.authenticate(self.admin)
        self.assertEqual(self.client.delete(f"{self.create_url}{campaign.id}/").status_code, 409)

        evidence = Campaign.objects.create(name="Provider evidence", created_by=self.admin)
        AuditEvent.objects.create(action=AuditEvent.Action.CAMPAIGN_PROVIDER_FAILURE, entity_type="Campaign", entity_id=str(evidence.id), actor_user=self.admin)
        self.assertEqual(self.client.delete(f"{self.create_url}{evidence.id}/").status_code, 409)

        archived = Campaign.objects.create(name="Archived unused", created_by=self.admin, archived_at=timezone.now())
        self.assertEqual(self.client.delete(f"{self.create_url}{archived.id}/").status_code, 204)

        prepared = Campaign.objects.create(name="Archived prepared", status=Campaign.Status.PREPARED, created_by=self.admin, archived_at=timezone.now())
        self.assertEqual(self.client.delete(f"{self.create_url}{prepared.id}/").status_code, 409)

    def test_capability_flags_follow_permission_and_historical_evidence(self):
        campaign = Campaign.objects.create(name="Capabilities", created_by=self.admin)
        self.authenticate(self.admin)
        response = self.client.get(f"{self.create_url}{campaign.id}/")
        self.assertTrue(response.data["can_archive"])
        self.assertFalse(response.data["can_restore"])
        self.assertTrue(response.data["can_delete"])
        self.authenticate(self.viewer)
        response = self.client.get(f"{self.create_url}{campaign.id}/")
        self.assertFalse(response.data["can_archive"])
        self.assertFalse(response.data["can_restore"])
        self.assertFalse(response.data["can_delete"])

    def test_prepare_archive_restore_keeps_same_evidence_and_workflow_state(self):
        campaign = self._snapshot_ready_campaign()
        preparation_id = campaign.current_preparation_id
        snapshot_ids = list(campaign.current_preparation.recipient_snapshots.values_list("id", flat=True))
        self.authenticate(self.admin)
        self.client.post(f"{self.create_url}{campaign.id}/archive/", {}, format="json")
        self.client.post(f"{self.create_url}{campaign.id}/restore/", {}, format="json")
        campaign.refresh_from_db()
        self.assertEqual(campaign.status, Campaign.Status.SNAPSHOT_READY)
        self.assertEqual(campaign.current_preparation_id, preparation_id)
        self.assertEqual(list(campaign.current_preparation.recipient_snapshots.values_list("id", flat=True)), snapshot_ids)
