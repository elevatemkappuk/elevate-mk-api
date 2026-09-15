from io import StringIO
import base64
from datetime import datetime, timezone as datetime_timezone
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx
from brevo import BadRequestError, UnauthorizedError
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework.test import APIClient

from brevo_marketing.client import BrevoContact, BrevoMarketingClient
from brevo_marketing.exceptions import (
    BrevoMarketingAuthenticationError,
    BrevoMarketingConfigurationError,
    BrevoMarketingIdentityConflictError,
    BrevoMarketingRateLimitError,
    BrevoMarketingTemporaryError,
    BrevoMarketingValidationError,
)
from brevo_marketing.services import inspect_brevo_marketing_configuration
from brevo_marketing.jobs import BrevoJobProcessResult, process_next_brevo_sync_job, run_brevo_sync_worker
from brevo_marketing.routing import get_active_marketing_sync_provider
from brevo_marketing.sync import BrevoPersonSyncOutcome, synchronize_person_profile_to_brevo, synchronize_person_to_brevo
from external_references.models import ExternalPersonReference, ExternalPersonSyncJob
from marketing_preferences.models import MarketingPreference, MarketingPreferenceHistory, MarketingWebhookReceipt
from marketing_preferences.services import record_opt_in, record_opt_out
from people.models import Person


class BrevoMarketingClientTests(SimpleTestCase):
    def test_configuration_is_read_only_and_returns_safe_attributes_and_lists(self):
        sdk = Mock()
        sdk.contacts.get_attributes.return_value = SimpleNamespace(
            attributes=[SimpleNamespace(name="FIRSTNAME", type="text", category="normal", enumeration=None, multi_category_options=None)]
        )
        sdk.contacts.get_lists.return_value = SimpleNamespace(
            count=1,
            lists=[SimpleNamespace(id=7, name="Newsletter", total_subscribers=12, total_blacklisted=2, unique_subscribers=14)],
        )

        client = BrevoMarketingClient(api_key="secret", sdk_factory=Mock(return_value=sdk))
        result = inspect_brevo_marketing_configuration(client=client)

        self.assertEqual(result.attributes[0].name, "FIRSTNAME")
        self.assertEqual(result.lists[0].list_id, 7)
        sdk.contacts.get_attributes.assert_called_once_with()
        sdk.contacts.get_lists.assert_called_once_with(limit=50, offset=0)

    @override_settings(BREVO_API_KEY="")
    def test_missing_configuration_fails_before_sdk_creation(self):
        with self.assertRaises(BrevoMarketingConfigurationError):
            BrevoMarketingClient.from_settings()

    def test_authentication_failure_is_controlled(self):
        sdk = Mock()
        sdk.contacts.get_attributes.side_effect = UnauthorizedError(body={"message": "invalid key"})
        client = BrevoMarketingClient(api_key="secret", sdk_factory=Mock(return_value=sdk))

        with self.assertRaises(BrevoMarketingAuthenticationError):
            client.get_contact_attributes()

    def test_network_failure_is_classified_as_temporary(self):
        sdk = Mock()
        sdk.contacts.get_lists.side_effect = httpx.ConnectError("offline")
        client = BrevoMarketingClient(api_key="secret", sdk_factory=Mock(return_value=sdk))

        with self.assertRaises(BrevoMarketingTemporaryError):
            client.get_contact_lists()

    def test_validation_error_is_bounded_and_redacted(self):
        sdk = Mock()
        sdk.contacts.get_attributes.side_effect = BadRequestError(
            body={"code": "invalid_parameter", "message": "Email ava@example.com rejected; token=secret-value"}
        )
        client = BrevoMarketingClient(api_key="secret", sdk_factory=Mock(return_value=sdk))

        with self.assertRaises(BrevoMarketingValidationError) as raised:
            client.get_contact_attributes()

        self.assertIn("[redacted-email]", str(raised.exception))
        self.assertNotIn("secret-value", str(raised.exception))

    def test_missing_marketing_list_configuration_is_controlled(self):
        client = BrevoMarketingClient(api_key="secret", marketing_list_id="")

        with self.assertRaises(BrevoMarketingConfigurationError):
            client.get_marketing_list_id()

    @override_settings(MARKETING_SYNC_PROVIDER="MAILCHIMP")
    def test_unsupported_active_provider_is_rejected(self):
        with self.assertRaises(BrevoMarketingConfigurationError):
            get_active_marketing_sync_provider()


class BrevoMarketingCommandTests(SimpleTestCase):
    @patch("brevo_marketing.management.commands.inspect_brevo_marketing.inspect_brevo_marketing_configuration")
    def test_command_reports_metadata_without_credentials_or_contacts(self, inspect):
        inspect.return_value = SimpleNamespace(
            attributes=(SimpleNamespace(name="FIRSTNAME", attribute_type="text", category="normal", options=()),),
            lists=(SimpleNamespace(list_id=7, name="Newsletter", total_subscribers=12, total_blacklisted=2),),
        )

        output = StringIO()
        call_command("inspect_brevo_marketing", stdout=output)

        text = output.getvalue()
        self.assertIn("read-only verification succeeded", text)
        self.assertIn("FIRSTNAME", text)
        self.assertIn("ID 7", text)
        self.assertNotIn("secret", text)


class FakeBrevoSyncClient:
    def __init__(self, contact=None, list_id=2):
        self.contact = contact
        self.list_id = list_id
        self.created = []
        self.updated = []

    def get_marketing_list_id(self):
        return self.list_id

    def get_contact(self, email):
        return self.contact

    def get_contact_by_id(self, contact_id):
        return self.contact if self.contact and self.contact.contact_id == int(contact_id) else None

    def create_contact(self, *, email, attributes, list_id):
        self.created.append({"email": email, "attributes": attributes, "list_id": list_id})
        self.contact = BrevoContact(9, email, attributes, (list_id,), (), False, False)
        return self.contact

    def update_contact(self, **kwargs):
        self.updated.append(kwargs)


class BrevoPersonDatabaseSyncTests(TestCase):
    def person(self, **overrides):
        values = {"first_name": "Ava", "last_name": "Example", "primary_email": "ava@example.com"}
        values.update(overrides)
        return Person.objects.create(**values)

    def test_new_opted_in_contact_uses_only_approved_attributes_and_links_reference(self):
        person = self.person()
        record_opt_in(person=person)
        client = FakeBrevoSyncClient()

        result = synchronize_person_to_brevo(person_id=person.id, client=client)

        self.assertEqual(result.outcome, BrevoPersonSyncOutcome.CREATED_MARKETING_CONTACT)
        self.assertEqual(client.created[0]["attributes"], {"FIRSTNAME": "Ava", "LASTNAME": "Example"})
        self.assertEqual(ExternalPersonReference.objects.get().external_id, "9")

    def test_confirmed_international_mobile_maps_to_sms_only(self):
        person = self.person(mobile=" +265 991-234-567 ")
        record_opt_in(person=person)
        client = FakeBrevoSyncClient()

        synchronize_person_to_brevo(person_id=person.id, client=client)

        self.assertEqual(
            client.created[0]["attributes"],
            {"FIRSTNAME": "Ava", "LASTNAME": "Example", "SMS": "+265991234567"},
        )
        self.assertNotIn("LANDLINE_NUMBER", client.created[0]["attributes"])

    def test_blank_mobile_is_omitted_from_payload(self):
        person = self.person(mobile="")
        record_opt_in(person=person)
        client = FakeBrevoSyncClient()

        synchronize_person_to_brevo(person_id=person.id, client=client)

        self.assertEqual(client.created[0]["attributes"], {"FIRSTNAME": "Ava", "LASTNAME": "Example"})

    def test_ambiguous_local_mobile_is_omitted_without_blocking_email_sync(self):
        person = self.person(mobile="0991000001")
        record_opt_in(person=person)
        client = FakeBrevoSyncClient()

        result = synchronize_person_to_brevo(person_id=person.id, client=client)

        self.assertEqual(result.outcome, BrevoPersonSyncOutcome.CREATED_MARKETING_CONTACT)
        self.assertEqual(result.reason, "MOBILE_OMITTED_UNSAFE_FORMAT")
        self.assertEqual(client.created[0]["attributes"], {"FIRSTNAME": "Ava", "LASTNAME": "Example"})

    def test_mobile_does_not_create_email_consent(self):
        person = self.person(mobile="+265991234567")
        client = FakeBrevoSyncClient()

        result = synchronize_person_to_brevo(person_id=person.id, client=client)

        self.assertEqual(result.outcome, BrevoPersonSyncOutcome.SKIPPED_CONSENT_UNKNOWN)
        self.assertFalse(client.created)

    def test_unknown_does_not_create_contact(self):
        person = self.person()
        client = FakeBrevoSyncClient()

        result = synchronize_person_to_brevo(person_id=person.id, client=client)

        self.assertEqual(result.outcome, BrevoPersonSyncOutcome.SKIPPED_CONSENT_UNKNOWN)
        self.assertFalse(client.created)

    def test_opted_out_without_contact_does_not_create_contact(self):
        person = self.person()
        record_opt_out(person=person)
        client = FakeBrevoSyncClient()

        result = synchronize_person_to_brevo(person_id=person.id, client=client)

        self.assertEqual(result.outcome, BrevoPersonSyncOutcome.SKIPPED_CONSENT_OPTED_OUT_NO_CONTACT)
        self.assertFalse(client.created)

    def test_existing_restrictive_contact_is_not_reenabled(self):
        person = self.person()
        record_opt_in(person=person)
        contact = BrevoContact(9, person.primary_email, {"FIRSTNAME": "Old", "LASTNAME": "Name"}, (), (), True, False)
        client = FakeBrevoSyncClient(contact=contact)

        result = synchronize_person_to_brevo(person_id=person.id, client=client)

        self.assertEqual(result.outcome, BrevoPersonSyncOutcome.SKIPPED_PROTECTED_PROVIDER_STATE)
        self.assertFalse(client.updated)
        self.assertEqual(ExternalPersonReference.objects.count(), 1)

    def test_existing_contact_is_updated_then_repeated_sync_is_a_no_op(self):
        person = self.person()
        record_opt_in(person=person)
        contact = BrevoContact(9, person.primary_email, {"FIRSTNAME": "Old", "LASTNAME": "Name"}, (), (), False, False)
        client = FakeBrevoSyncClient(contact=contact)

        first = synchronize_person_to_brevo(person_id=person.id, client=client)
        client.contact = BrevoContact(9, person.primary_email, {"FIRSTNAME": "Ava", "LASTNAME": "Example"}, (2,), (), False, False)
        second = synchronize_person_to_brevo(person_id=person.id, client=client)

        self.assertEqual(first.outcome, BrevoPersonSyncOutcome.UPDATED_MARKETING_CONTACT)
        self.assertEqual(second.outcome, BrevoPersonSyncOutcome.ALREADY_SYNCHRONIZED)
        self.assertEqual(ExternalPersonReference.objects.count(), 1)

    def test_opted_out_existing_contact_uses_marketing_campaign_blocklist(self):
        person = self.person()
        record_opt_out(person=person)
        contact = BrevoContact(9, person.primary_email, {}, (2,), (), False, False)
        client = FakeBrevoSyncClient(contact=contact)

        result = synchronize_person_to_brevo(person_id=person.id, client=client)

        self.assertEqual(result.outcome, BrevoPersonSyncOutcome.MARKETING_OPTED_OUT)
        self.assertEqual(client.updated[0], {"contact_id": 9, "email_blacklisted": True})

    def test_existing_reference_with_missing_contact_requires_reconciliation(self):
        person = self.person()
        record_opt_in(person=person)
        reference = ExternalPersonReference.objects.create(
            person=person,
            provider="BREVO",
            reference_type=ExternalPersonReference.ReferenceType.MARKETING_CONTACT,
            external_id="11",
        )
        result = synchronize_person_to_brevo(person_id=person.id, client=FakeBrevoSyncClient())

        self.assertEqual(result.outcome, BrevoPersonSyncOutcome.RECONCILIATION_REQUIRED)
        self.assertEqual(result.reference_id, reference.id)

    def test_existing_contact_reference_conflict_fails_safely(self):
        person = self.person()
        other = self.person(primary_email="other@example.com")
        record_opt_in(person=person)
        ExternalPersonReference.objects.create(
            person=other,
            provider="BREVO",
            reference_type=ExternalPersonReference.ReferenceType.MARKETING_CONTACT,
            external_id="9",
        )
        contact = BrevoContact(9, person.primary_email, {}, (2,), (), False, False)

        with self.assertRaises(BrevoMarketingIdentityConflictError):
            synchronize_person_to_brevo(person_id=person.id, client=FakeBrevoSyncClient(contact=contact))

    def profile_reference(self, person, contact_id=9):
        return ExternalPersonReference.objects.create(
            person=person,
            provider="BREVO",
            reference_type=ExternalPersonReference.ReferenceType.MARKETING_CONTACT,
            external_id=str(contact_id),
        )

    def test_profile_sync_updates_name_and_safe_mobile_without_consent_dependency(self):
        person = self.person(first_name="Sofia", last_name="Smith", mobile="+265991234567")
        reference = self.profile_reference(person)
        contact = BrevoContact(9, person.primary_email, {"FIRSTNAME": "Old", "LASTNAME": "Name", "SMS": "+265991000000"}, (2,), (2,), True, False)
        client = FakeBrevoSyncClient(contact=contact)

        result = synchronize_person_profile_to_brevo(person_id=person.id, client=client)

        self.assertEqual(result.outcome, BrevoPersonSyncOutcome.UPDATED_PERSON_PROFILE)
        self.assertEqual(client.updated[0]["attributes"], {"FIRSTNAME": "Sofia", "LASTNAME": "Smith", "SMS": "+265991234567"})
        self.assertEqual(result.reference_id, reference.id)
        self.assertFalse(MarketingPreference.objects.filter(person=person).exists())

    def test_profile_sync_clears_blank_attributes_and_omits_unsafe_mobile(self):
        person = self.person(first_name="", last_name="", mobile="0991000001")
        self.profile_reference(person)
        contact = BrevoContact(9, person.primary_email, {"FIRSTNAME": "Old", "LASTNAME": "Name", "SMS": "+265991000000"}, (), (), False, False)
        client = FakeBrevoSyncClient(contact=contact)

        result = synchronize_person_profile_to_brevo(person_id=person.id, client=client)

        self.assertEqual(result.outcome, BrevoPersonSyncOutcome.UPDATED_PERSON_PROFILE)
        self.assertEqual(result.reason, "MOBILE_OMITTED_UNSAFE_FORMAT")
        self.assertEqual(client.updated[0]["attributes"], {"FIRSTNAME": "", "LASTNAME": "",})
        self.assertNotIn("SMS", client.updated[0]["attributes"])

    def test_profile_sync_clears_blank_mobile(self):
        person = self.person(mobile="")
        self.profile_reference(person)
        contact = BrevoContact(9, person.primary_email, {"FIRSTNAME": "Ava", "LASTNAME": "Example", "SMS": "+265991000000"}, (), (), False, False)
        client = FakeBrevoSyncClient(contact=contact)

        synchronize_person_profile_to_brevo(person_id=person.id, client=client)

        self.assertEqual(client.updated[0]["attributes"]["SMS"], "")

    def test_profile_sync_without_reference_skips_without_creating_contact(self):
        person = self.person()
        client = FakeBrevoSyncClient()

        result = synchronize_person_profile_to_brevo(person_id=person.id, client=client)

        self.assertEqual(result.outcome, BrevoPersonSyncOutcome.SKIPPED_NO_MARKETING_CONTACT)
        self.assertFalse(client.created)

    def test_profile_email_identity_mismatch_requires_reconciliation(self):
        person = self.person(primary_email="new@example.com")
        self.profile_reference(person)
        contact = BrevoContact(9, "old@example.com", {"FIRSTNAME": "Ava", "LASTNAME": "Example"}, (), (), False, False)
        client = FakeBrevoSyncClient(contact=contact)

        result = synchronize_person_profile_to_brevo(person_id=person.id, client=client)

        self.assertEqual(result.outcome, BrevoPersonSyncOutcome.RECONCILIATION_REQUIRED)
        self.assertFalse(client.updated)


class BrevoSyncJobTests(TestCase):
    def setUp(self):
        self.person = Person.objects.create(first_name="Ava", last_name="Example", primary_email="ava@example.com")
        record_opt_in(person=self.person)
        self.job = ExternalPersonSyncJob.objects.get(provider="BREVO")

    @patch("brevo_marketing.jobs.synchronize_person_to_brevo")
    def test_worker_processes_brevo_job_and_completes_business_outcomes(self, synchronize):
        synchronize.return_value = type("Result", (), {"outcome": BrevoPersonSyncOutcome.CREATED_MARKETING_CONTACT})()

        result = process_next_brevo_sync_job(client=Mock())
        self.job.refresh_from_db()

        self.assertEqual(result.status, ExternalPersonSyncJob.Status.SUCCEEDED)
        self.assertEqual(self.job.status, ExternalPersonSyncJob.Status.SUCCEEDED)
        synchronize.assert_called_once_with(person_id=self.person.id, client=synchronize.call_args.kwargs["client"])

    @patch("brevo_marketing.jobs.synchronize_person_profile_to_brevo")
    def test_worker_processes_person_profile_job(self, synchronize_profile):
        self.job.status = ExternalPersonSyncJob.Status.SUCCEEDED
        self.job.save(update_fields=["status", "updated_at"])
        profile_job = ExternalPersonSyncJob.objects.create(
            person=self.person,
            provider="BREVO",
            job_type="PERSON_PROFILE",
            source_event_id=999,
        )
        synchronize_profile.return_value = type("Result", (), {"outcome": BrevoPersonSyncOutcome.UPDATED_PERSON_PROFILE})()

        result = process_next_brevo_sync_job(client=Mock())

        profile_job.refresh_from_db()
        self.assertEqual(result.status, ExternalPersonSyncJob.Status.SUCCEEDED)
        self.assertEqual(profile_job.status, ExternalPersonSyncJob.Status.SUCCEEDED)
        synchronize_profile.assert_called_once_with(person_id=self.person.id, client=synchronize_profile.call_args.kwargs["client"])

    @patch("brevo_marketing.jobs.synchronize_person_to_brevo")
    def test_worker_retries_temporary_provider_failure(self, synchronize):
        synchronize.side_effect = BrevoMarketingRateLimitError("provider detail")

        result = process_next_brevo_sync_job(client=Mock())
        self.job.refresh_from_db()

        self.assertEqual(result.status, ExternalPersonSyncJob.Status.PENDING)
        self.assertEqual(result.error_code, "BREVO_RATE_LIMIT")
        self.assertEqual(self.job.status, ExternalPersonSyncJob.Status.PENDING)
        self.assertNotIn("provider detail", self.job.last_error_message or "")

    @patch("brevo_marketing.jobs.synchronize_person_to_brevo")
    def test_worker_treats_identity_conflict_as_terminal(self, synchronize):
        synchronize.side_effect = BrevoMarketingIdentityConflictError("contact conflict")

        result = process_next_brevo_sync_job(client=Mock())
        self.job.refresh_from_db()

        self.assertEqual(result.status, ExternalPersonSyncJob.Status.FAILED)
        self.assertEqual(result.error_code, "BREVO_IDENTITY_CONFLICT")
        self.assertEqual(self.job.status, ExternalPersonSyncJob.Status.FAILED)

    @patch("brevo_marketing.jobs.synchronize_person_to_brevo")
    def test_worker_treats_reconciliation_required_as_terminal(self, synchronize):
        synchronize.return_value = type("Result", (), {"outcome": BrevoPersonSyncOutcome.RECONCILIATION_REQUIRED})()

        result = process_next_brevo_sync_job(client=Mock())
        self.job.refresh_from_db()

        self.assertEqual(result.status, ExternalPersonSyncJob.Status.FAILED)
        self.assertEqual(result.error_code, "BREVO_RECONCILIATION_REQUIRED")
        self.assertEqual(self.job.status, ExternalPersonSyncJob.Status.FAILED)

    @patch("brevo_marketing.jobs.synchronize_person_to_brevo")
    def test_one_failed_job_does_not_prevent_next_job_in_batch(self, synchronize):
        other = Person.objects.create(first_name="Other", last_name="Example", primary_email="other@example.com")
        record_opt_out(person=other)
        synchronize.side_effect = [
            BrevoMarketingRateLimitError("temporary"),
            type("Result", (), {"outcome": BrevoPersonSyncOutcome.UPDATED_MARKETING_CONTACT})(),
        ]

        results = process_brevo_sync_jobs(limit=2, client=Mock())

        self.assertEqual(len(results), 2)
        self.assertEqual(results[0].status, ExternalPersonSyncJob.Status.PENDING)
        self.assertEqual(results[1].status, ExternalPersonSyncJob.Status.SUCCEEDED)
        self.assertEqual(synchronize.call_count, 2)

    def test_worker_ignores_mailchimp_jobs(self):
        ExternalPersonSyncJob.objects.create(
            person=self.person,
            provider="MAILCHIMP",
            job_type="EMAIL_MARKETING_PREFERENCE",
            source_event_id=999,
        )
        self.job.status = ExternalPersonSyncJob.Status.SUCCEEDED
        self.job.save(update_fields=["status", "updated_at"])

        self.assertIsNone(process_next_brevo_sync_job(client=Mock()))
        self.assertEqual(
            ExternalPersonSyncJob.objects.get(provider="MAILCHIMP").status,
            ExternalPersonSyncJob.Status.PENDING,
        )

    @patch("brevo_marketing.jobs.process_brevo_sync_jobs")
    def test_watch_worker_uses_bounded_batch_and_processes_results(self, process_jobs):
        stop_event = Event()
        processed = []
        process_jobs.return_value = [
            BrevoJobProcessResult(job_id=1, status="SUCCEEDED", outcome="CREATED", attempts=1),
            BrevoJobProcessResult(job_id=2, status="FAILED", error_code="BREVO_VALIDATION", attempts=1),
        ]

        run_brevo_sync_worker(
            poll_seconds=3,
            batch_size=2,
            stop_event=stop_event,
            on_result=lambda result: (processed.append(result), stop_event.set()),
        )

        process_jobs.assert_called_once_with(limit=2, client=None)
        self.assertEqual([result.job_id for result in processed], [1, 2])

    @patch("brevo_marketing.jobs.process_brevo_sync_jobs", return_value=[])
    def test_watch_worker_polls_idle_queue_without_busy_loop(self, process_jobs):
        stop_event = Event()
        waits = []

        def wait_once(seconds):
            waits.append(seconds)
            stop_event.set()

        run_brevo_sync_worker(stop_event=stop_event, sleep_fn=wait_once)

        process_jobs.assert_called_once()
        self.assertEqual(waits, [3.0])

    @patch("brevo_marketing.jobs.process_brevo_sync_jobs", side_effect=RuntimeError("database unavailable"))
    def test_watch_worker_survives_outer_batch_failure_and_can_shutdown(self, process_jobs):
        stop_event = Event()

        run_brevo_sync_worker(
            stop_event=stop_event,
            sleep_fn=lambda seconds: stop_event.set(),
        )

        process_jobs.assert_called_once()

    @patch("brevo_marketing.jobs.process_brevo_sync_jobs")
    def test_watch_worker_honors_graceful_shutdown_before_claiming(self, process_jobs):
        stop_event = Event()
        stop_event.set()

        run_brevo_sync_worker(stop_event=stop_event)

        process_jobs.assert_not_called()


@override_settings(
    BREVO_MARKETING_WEBHOOK_USERNAME="webhook-user",
    BREVO_MARKETING_WEBHOOK_PASSWORD="webhook-password",
    BREVO_MARKETING_LIST_ID="2",
)
class BrevoMarketingWebhookTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.person = Person.objects.create(first_name="Ava", last_name="Example", primary_email="ava@example.com")
        self.url = "/api/v1/webhooks/brevo/marketing/"

    def auth(self, username="webhook-user", password="webhook-password"):
        token = base64.b64encode(f"{username}:{password}".encode()).decode()
        return {"HTTP_AUTHORIZATION": f"Basic {token}"}

    def payload(self, **overrides):
        payload = {
            "event": "unsubscribe",
            "email": "AVA@example.com",
            "id": 7001,
            "ts_event": 1770000000,
            "date_event": "2026-02-02 00:00:00",
            "camp_id": 44,
            "list_id": [2],
        }
        payload.update(overrides)
        return payload

    def test_authenticated_unsubscribe_records_brevo_opt_out_without_echo_job(self):
        response = self.client.post(self.url, self.payload(), format="json", **self.auth())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["outcome"], "CRM_OPTED_OUT_RECORDED")
        preference = MarketingPreference.objects.get(person=self.person)
        self.assertEqual(preference.state, MarketingPreference.State.OPTED_OUT)
        self.assertEqual(preference.source, MarketingPreference.Source.BREVO)
        history = MarketingPreferenceHistory.objects.get()
        self.assertEqual(history.source, MarketingPreference.Source.BREVO)
        self.assertEqual(history.recorded_at, datetime.fromtimestamp(1770000000, tz=datetime_timezone.utc))
        self.assertFalse(ExternalPersonSyncJob.objects.exists())
        self.assertEqual(MarketingWebhookReceipt.objects.count(), 1)

    def test_campaign_unsubscribe_without_list_id_records_brevo_opt_out(self):
        payload = self.payload()
        payload.pop("list_id")

        response = self.client.post(self.url, payload, format="json", **self.auth())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["outcome"], "CRM_OPTED_OUT_RECORDED")
        self.assertEqual(MarketingPreference.objects.get(person=self.person).source, MarketingPreference.Source.BREVO)
        self.assertFalse(ExternalPersonSyncJob.objects.exists())
        self.assertEqual(MarketingWebhookReceipt.objects.count(), 1)

    def test_conflicting_list_id_is_rejected_without_consent_mutation(self):
        response = self.client.post(self.url, self.payload(list_id=[99]), format="json", **self.auth())

        self.assertEqual(response.status_code, 400)
        self.assertFalse(MarketingPreference.objects.exists())

    def test_unknown_unsubscribe_records_opt_out_without_creating_person_or_job(self):
        response = self.client.post(self.url, self.payload(email="missing@example.com"), format="json", **self.auth())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["outcome"], "PERSON_NOT_FOUND")
        self.assertFalse(MarketingPreference.objects.exists())
        self.assertFalse(Person.objects.filter(primary_email="missing@example.com").exists())
        self.assertFalse(ExternalPersonSyncJob.objects.exists())

    def test_replay_is_acknowledged_without_duplicate_history(self):
        payload = self.payload()
        payload.pop("list_id")
        first = self.client.post(self.url, payload, format="json", **self.auth())
        second = self.client.post(self.url, payload, format="json", **self.auth())

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.data["outcome"], "REPLAY_IGNORED")
        self.assertEqual(MarketingPreferenceHistory.objects.count(), 1)
        self.assertEqual(MarketingWebhookReceipt.objects.count(), 1)

    def test_missing_timestamp_without_list_id_is_malformed(self):
        payload = self.payload(ts_event=None, date_event=None, ts=None)
        payload.pop("list_id")

        response = self.client.post(self.url, payload, format="json", **self.auth())

        self.assertEqual(response.status_code, 400)
        self.assertFalse(MarketingPreference.objects.exists())

    def test_same_webhook_id_for_different_recipients_does_not_collide(self):
        other = Person.objects.create(first_name="Other", last_name="Example", primary_email="other@example.com")

        first = self.client.post(self.url, self.payload(), format="json", **self.auth())
        second = self.client.post(
            self.url,
            self.payload(email=other.primary_email),
            format="json",
            **self.auth(),
        )

        self.assertEqual(first.data["outcome"], "CRM_OPTED_OUT_RECORDED")
        self.assertEqual(second.data["outcome"], "CRM_OPTED_OUT_RECORDED")
        self.assertEqual(MarketingPreferenceHistory.objects.count(), 2)
        self.assertEqual(MarketingWebhookReceipt.objects.count(), 2)

    def test_same_person_can_process_later_unsubscribe_after_reconsent(self):
        first = self.client.post(self.url, self.payload(camp_id=44, ts_event=1770000000), format="json", **self.auth())
        self.assertEqual(first.data["outcome"], "CRM_OPTED_OUT_RECORDED")

        from marketing_preferences.services import record_opt_in

        record_opt_in(person=self.person, source=MarketingPreference.Source.STAFF_RECORDED)
        second = self.client.post(
            self.url,
            self.payload(camp_id=45, ts_event=1770086400),
            format="json",
            **self.auth(),
        )

        self.assertEqual(second.data["outcome"], "CRM_OPTED_OUT_RECORDED")
        self.assertEqual(MarketingPreferenceHistory.objects.count(), 3)
        self.assertEqual(MarketingWebhookReceipt.objects.count(), 2)

    def test_missing_event_timestamp_relies_on_preference_idempotency(self):
        first = self.client.post(self.url, self.payload(ts_event=None, date_event=None), format="json", **self.auth())
        second = self.client.post(self.url, self.payload(ts_event=None, date_event=None), format="json", **self.auth())

        self.assertEqual(first.data["outcome"], "CRM_OPTED_OUT_RECORDED")
        self.assertEqual(second.data["outcome"], "CRM_OPTED_OUT_ALREADY_RECORDED")
        self.assertEqual(MarketingPreferenceHistory.objects.count(), 1)
        self.assertEqual(MarketingWebhookReceipt.objects.count(), 0)

    def test_unsupported_marketing_event_is_acknowledged_without_mutation(self):
        response = self.client.post(self.url, {"event": "opened"}, format="json", **self.auth())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["outcome"], "UNSUPPORTED_EVENT")
        self.assertFalse(MarketingPreference.objects.exists())

    def test_authentication_and_malformed_payload_are_rejected_safely(self):
        unauthorized = self.client.post(self.url, self.payload(), format="json", **self.auth(password="wrong"))
        malformed = self.client.post(self.url, b"not-json", content_type="application/json", **self.auth())

        self.assertEqual(unauthorized.status_code, 401)
        self.assertEqual(malformed.status_code, 400)
        self.assertNotIn("webhook-password", unauthorized.content.decode())

    def test_ambiguous_email_does_not_mutate_consent(self):
        Person.objects.create(first_name="Another", last_name="Example", primary_email="ava@example.com")

        response = self.client.post(self.url, self.payload(), format="json", **self.auth())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["outcome"], "IDENTITY_CONFLICT")
        self.assertFalse(MarketingPreference.objects.exists())
