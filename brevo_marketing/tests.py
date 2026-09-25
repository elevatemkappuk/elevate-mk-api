from io import StringIO
import base64
from datetime import datetime, timezone as datetime_timezone
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx
from brevo import BadRequestError, UnauthorizedError
from django.core.management import call_command
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework.test import APIClient

from audit.models import AuditEvent
from brevo_marketing.client import BrevoContact, BrevoMarketingClient
from brevo_marketing.exceptions import (
    BrevoMarketingAuthenticationError,
    BrevoMarketingConfigurationError,
    BrevoMarketingIdentityConflictError,
    BrevoMarketingRateLimitError,
    BrevoMarketingPropagationDelay,
    BrevoMarketingTemporaryError,
    BrevoMarketingValidationError,
)
from brevo_marketing.services import inspect_brevo_marketing_configuration
from brevo_marketing.inspection import inspect_person_brevo_integration
from brevo_marketing.jobs import (
    BrevoJobProcessResult,
    PERSON_EMAIL_MIGRATION_SYNC,
    process_brevo_sync_jobs,
    process_next_brevo_sync_job,
    run_brevo_sync_worker,
)
from brevo_marketing.routing import get_active_marketing_sync_provider
from brevo_marketing.sync import (
    BrevoPersonSyncOutcome,
    synchronize_person_email_to_brevo,
    synchronize_person_profile_to_brevo,
    synchronize_person_to_brevo,
)
from external_references.models import ExternalPersonReference, ExternalPersonSyncJob
from marketing_preferences.models import MarketingPreference, MarketingPreferenceHistory, MarketingWebhookReceipt
from marketing_preferences.services import record_opt_in, record_opt_out
from people.models import Person


class PersonBrevoInspectionTests(TestCase):
    def setUp(self):
        self.person = Person.objects.create(
            first_name="Inspection",
            last_name="Subject",
            primary_email="inspection@example.com",
        )

    def reference(self, external_id="42"):
        return ExternalPersonReference.objects.create(
            person=self.person,
            provider="BREVO",
            reference_type=ExternalPersonReference.ReferenceType.MARKETING_CONTACT,
            external_id=external_id,
        )

    def contact(self, **kwargs):
        values = {
            "contact_id": 42,
            "email": "inspection@example.com",
            "attributes": {},
            "list_ids": (2,),
            "list_unsubscribed": (),
            "email_blacklisted": False,
            "sms_blacklisted": False,
        }
        values.update(kwargs)
        return BrevoContact(**values)

    def fake_client(self, contact):
        client = Mock()
        client.get_contact_by_id.return_value = contact
        client.get_marketing_list_id.return_value = 2
        return client

    def test_no_active_reference_is_not_connected_without_provider_call(self):
        client = self.fake_client(None)
        result = inspect_person_brevo_integration(person=self.person, client=client)

        self.assertEqual(result.integration.status, "NOT_CONNECTED")
        client.get_contact_by_id.assert_not_called()

    def test_missing_contact_is_admin_reconcilable(self):
        result = inspect_person_brevo_integration(person=self.person, can_reconcile=True, client=self.fake_client(None))
        self.assertEqual(result.integration.status, "NOT_CONNECTED")

        self.reference()
        result = inspect_person_brevo_integration(person=self.person, can_reconcile=True, client=self.fake_client(None))
        self.assertEqual(result.integration.status, "CONTACT_MISSING")
        self.assertTrue(result.integration.can_reconcile)
        self.assertEqual(result.integration.reason_code, "BREVO_CONTACT_NOT_FOUND_FOR_EXISTING_REFERENCE")

    def test_missing_contact_is_not_reconcilable_for_non_admin(self):
        self.reference()
        result = inspect_person_brevo_integration(person=self.person, client=self.fake_client(None))
        self.assertEqual(result.integration.status, "CONTACT_MISSING")
        self.assertFalse(result.integration.can_reconcile)

    def test_connected_contact_requires_matching_identity(self):
        self.reference()
        result = inspect_person_brevo_integration(person=self.person, client=self.fake_client(self.contact()))
        self.assertEqual(result.integration.status, "CONNECTED")
        self.assertIsNone(result.integration.reason_code)

    def test_restricted_email_contact_is_read_only(self):
        self.reference()
        result = inspect_person_brevo_integration(
            person=self.person,
            client=self.fake_client(self.contact(email_blacklisted=True)),
        )
        self.assertEqual(result.integration.status, "RESTRICTED")
        self.assertFalse(result.integration.can_reconcile)
        self.assertIn("will not automatically unblock", result.integration.explanation)

    def test_restricted_list_contact_is_read_only(self):
        self.reference()
        result = inspect_person_brevo_integration(
            person=self.person,
            client=self.fake_client(self.contact(list_unsubscribed=(2,))),
        )
        self.assertEqual(result.integration.status, "RESTRICTED")

    def test_email_mismatch_is_identity_conflict(self):
        self.reference()
        result = inspect_person_brevo_integration(
            person=self.person,
            client=self.fake_client(self.contact(email="other@example.com")),
        )
        self.assertEqual(result.integration.status, "IDENTITY_CONFLICT")
        self.assertEqual(result.integration.reason_code, "BREVO_CONTACT_IDENTITY_CONFLICT")

    def test_missing_current_email_is_identity_conflict(self):
        self.reference()
        self.person.primary_email = ""
        self.person.save(update_fields=["primary_email", "updated_at"])
        result = inspect_person_brevo_integration(person=self.person, client=self.fake_client(self.contact()))
        self.assertEqual(result.integration.status, "IDENTITY_CONFLICT")

    def test_provider_error_is_safe_unknown(self):
        self.reference()
        client = self.fake_client(None)
        from brevo_marketing.exceptions import BrevoMarketingTemporaryError
        client.get_contact_by_id.side_effect = BrevoMarketingTemporaryError("secret raw error")
        result = inspect_person_brevo_integration(person=self.person, client=client)
        self.assertEqual(result.integration.status, "UNKNOWN")
        self.assertNotIn("secret", result.integration.explanation)

    def test_inspection_does_not_create_or_modify_references(self):
        self.reference()
        before = list(ExternalPersonReference.objects.values_list("id", "status", "external_id"))
        inspect_person_brevo_integration(person=self.person, client=self.fake_client(self.contact()))
        self.assertEqual(before, list(ExternalPersonReference.objects.values_list("id", "status", "external_id")))


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

    @override_settings(BREVO_SENDER_EMAIL="sender@example.com", BREVO_SENDER_NAME="Elevate MK", BREVO_MARKETING_STARTER_TEMPLATE_ID="16")
    def test_campaign_operations_use_dedicated_list_and_template(self):
        sdk = Mock()
        sdk.contacts.create_list.return_value = SimpleNamespace(id=7)
        sdk.email_campaigns.get_email_campaigns.return_value = SimpleNamespace(count=0, campaigns=[])
        sdk.email_campaigns.create_email_campaign.return_value = SimpleNamespace(id=42)
        client = BrevoMarketingClient(api_key="secret", campaign_folder_id="9", sdk_factory=Mock(return_value=sdk))

        campaign_list = client.create_campaign_list(name="Elevate Campaign 1 | Update | Prep 1")
        client.add_contact_to_list(list_id=7, contact_id=123)
        campaign = client.create_email_campaign_draft(name="Elevate Campaign 1 | Update | Prep 1", subject="Campaign V1 Controlled Test", template_id="16", list_id=7)

        self.assertEqual(campaign_list.list_id, 7)
        self.assertEqual(campaign.campaign_id, 42)
        sdk.contacts.create_list.assert_called_once_with(folder_id=9, name="Elevate Campaign 1 | Update | Prep 1")
        request = sdk.contacts.add_contact_to_list.call_args.kwargs["request"]
        self.assertEqual(request.ids, [123])
        request = sdk.email_campaigns.create_email_campaign.call_args.kwargs
        self.assertEqual(request["subject"], "Campaign V1 Controlled Test")
        self.assertEqual(request["template_id"], 16)
        self.assertEqual(request["recipients"].list_ids, [7])
        self.assertNotIn("scheduled_at", request)
        self.assertNotIn("send_at_best_time", request)

    @override_settings(BREVO_SENDER_EMAIL="sender@example.com", BREVO_SENDER_NAME="Elevate MK", BREVO_MARKETING_STARTER_TEMPLATE_ID="16")
    def test_campaign_draft_creation_without_campaign_folder_omits_folder(self):
        sdk = Mock()
        sdk.email_campaigns.create_email_campaign.return_value = SimpleNamespace(id=42)
        client = BrevoMarketingClient(api_key="secret", campaign_folder_id="", sdk_factory=Mock(return_value=sdk))

        campaign = client.create_email_campaign_draft(name="Elevate Campaign 1 | Update | Prep 1", subject="Campaign V1 Controlled Test", template_id="16", list_id=7)

        self.assertEqual(campaign.campaign_id, 42)
        request = sdk.email_campaigns.create_email_campaign.call_args.kwargs
        self.assertNotIn("folder_id", request)
        self.assertNotIn("folderId", request)
        self.assertEqual(request["subject"], "Campaign V1 Controlled Test")

    def test_campaign_draft_rejects_blank_subject(self):
        sdk = Mock()
        client = BrevoMarketingClient(api_key="secret", sdk_factory=Mock(return_value=sdk))

        with self.assertRaises(BrevoMarketingConfigurationError):
            client.create_email_campaign_draft(name="Campaign", subject="  ", template_id="16", list_id=7)

        sdk.email_campaigns.create_email_campaign.assert_not_called()

    def test_blank_campaign_folder_uses_the_configured_marketing_lists_actual_folder_for_list_creation(self):
        sdk = Mock()
        sdk.contacts.get_list.return_value = SimpleNamespace(id=4, folder_id=12)
        sdk.contacts.create_list.return_value = SimpleNamespace(id=7)
        client = BrevoMarketingClient(api_key="secret", marketing_list_id="4", campaign_folder_id="", sdk_factory=Mock(return_value=sdk))

        client.create_campaign_list(name="Elevate Campaign 1 | Update | Prep 1")

        sdk.contacts.get_list.assert_called_once_with(4)
        sdk.contacts.create_list.assert_called_once_with(folder_id=12, name="Elevate Campaign 1 | Update | Prep 1")

    def test_only_known_empty_recipients_response_is_propagation_delay(self):
        sdk = Mock()
        sdk.email_campaigns.get_email_campaigns.side_effect = BadRequestError(body={"code": "invalid_parameter", "message": "There are no contacts associated with the given recipients info"})
        client = BrevoMarketingClient(api_key="secret", sdk_factory=Mock(return_value=sdk))
        with self.assertRaises(BrevoMarketingPropagationDelay):
            client.find_draft_campaign_by_name(name="campaign")

        sdk.email_campaigns.get_email_campaigns.side_effect = BadRequestError(body={"code": "invalid_parameter", "message": "The campaign name is invalid"})
        with self.assertRaises(BrevoMarketingValidationError):
            client.find_draft_campaign_by_name(name="campaign")

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
    def __init__(self, contact=None, list_id=2, create_errors=None, update_errors=None):
        self.contact = contact
        self.list_id = list_id
        self.created = []
        self.updated = []
        self.create_errors = list(create_errors or [])
        self.update_errors = list(update_errors or [])

    def get_marketing_list_id(self):
        return self.list_id

    def get_contact(self, email):
        return self.contact

    def get_contact_by_id(self, contact_id):
        return self.contact if self.contact and self.contact.contact_id == int(contact_id) else None

    def create_contact(self, *, email, attributes, list_id):
        self.created.append({"email": email, "attributes": attributes, "list_id": list_id})
        if self.create_errors:
            raise self.create_errors.pop(0)
        self.contact = BrevoContact(9, email, attributes, (list_id,), (), False, False)
        return self.contact

    def update_contact(self, **kwargs):
        self.updated.append(kwargs)
        if self.update_errors:
            raise self.update_errors.pop(0)


class FakeBrevoEmailMigrationClient:
    def __init__(self, contact, target_contact=None, *, update_conflict=False):
        self.contact = contact
        self.target_contact = target_contact
        self.update_conflict = update_conflict
        self.updated = []

    def get_contact_by_id(self, contact_id):
        return self.contact if self.contact and self.contact.contact_id == int(contact_id) else None

    def get_contact(self, email):
        normalized = email.strip().casefold()
        if self.contact and self.contact.email == normalized:
            return self.contact
        if self.target_contact and self.target_contact.email == normalized:
            return self.target_contact
        return None

    def update_contact(self, **kwargs):
        self.updated.append(kwargs)
        if self.update_conflict:
            raise BrevoMarketingValidationError("email already exists")
        email = kwargs.get("attributes", {}).get("EMAIL")
        if email:
            self.contact = BrevoContact(
                self.contact.contact_id,
                email,
                self.contact.attributes,
                self.contact.list_ids,
                self.contact.list_unsubscribed,
                self.contact.email_blacklisted,
                self.contact.sms_blacklisted,
            )


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

    def test_invalid_optional_sms_on_create_retries_once_without_sms_and_links_reference(self):
        person = self.person(mobile="+265991234567")
        record_opt_in(person=person)
        preference_before = MarketingPreference.objects.get(person=person).state
        client = FakeBrevoSyncClient(create_errors=[BrevoMarketingValidationError("invalid_parameter | Invalid phone number")])

        result = synchronize_person_to_brevo(person_id=person.id, client=client)

        self.assertEqual(result.outcome, BrevoPersonSyncOutcome.CREATED_MARKETING_CONTACT)
        self.assertEqual(len(client.created), 2)
        self.assertEqual(client.created[0]["attributes"]["SMS"], "+265991234567")
        self.assertNotIn("SMS", client.created[1]["attributes"])
        self.assertEqual(client.created[1]["attributes"]["FIRSTNAME"], "Ava")
        self.assertEqual(client.created[1]["attributes"]["LASTNAME"], "Example")
        self.assertEqual(person.refresh_from_db(), None)
        self.assertEqual(person.mobile, "+265991234567")
        self.assertEqual(MarketingPreference.objects.get(person=person).state, preference_before)
        self.assertEqual(ExternalPersonReference.objects.get(person=person).external_id, "9")

    def test_unrelated_validation_error_does_not_trigger_sms_fallback(self):
        person = self.person(mobile="+265991234567")
        record_opt_in(person=person)
        client = FakeBrevoSyncClient(create_errors=[BrevoMarketingValidationError("invalid_parameter | Email is invalid")])

        with self.assertRaises(BrevoMarketingValidationError):
            synchronize_person_to_brevo(person_id=person.id, client=client)

        self.assertEqual(len(client.created), 1)
        self.assertIn("SMS", client.created[0]["attributes"])
        self.assertEqual(ExternalPersonReference.objects.count(), 0)

    def test_sms_fallback_failure_preserves_normal_error_and_does_not_loop(self):
        person = self.person(mobile="+265991234567")
        record_opt_in(person=person)
        error = BrevoMarketingValidationError("invalid_parameter | Invalid phone number")
        client = FakeBrevoSyncClient(create_errors=[error, error])

        with self.assertRaises(BrevoMarketingValidationError):
            synchronize_person_to_brevo(person_id=person.id, client=client)

        self.assertEqual(len(client.created), 2)
        self.assertNotIn("SMS", client.created[1]["attributes"])
        self.assertFalse(client.create_errors)

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

    def test_existing_contact_update_retries_rejected_optional_sms_without_sms(self):
        person = self.person(mobile="+265991234567")
        record_opt_in(person=person)
        contact = BrevoContact(9, person.primary_email, {"FIRSTNAME": "Old", "LASTNAME": "Name", "SMS": "+265991000000"}, (), (), False, False)
        client = FakeBrevoSyncClient(contact=contact, update_errors=[BrevoMarketingValidationError("invalid_parameter | Invalid phone number")])

        result = synchronize_person_to_brevo(person_id=person.id, client=client)

        self.assertEqual(result.outcome, BrevoPersonSyncOutcome.UPDATED_MARKETING_CONTACT)
        self.assertEqual(len(client.updated), 2)
        self.assertIn("SMS", client.updated[0]["attributes"])
        self.assertNotIn("SMS", client.updated[1]["attributes"])
        self.assertEqual(ExternalPersonReference.objects.get(person=person).external_id, "9")

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

    def test_profile_sync_retries_rejected_optional_sms_without_sms(self):
        person = self.person(first_name="Sofia", last_name="Smith", mobile="+265991234567")
        reference = self.profile_reference(person)
        contact = BrevoContact(9, person.primary_email, {"FIRSTNAME": "Old", "LASTNAME": "Name"}, (), (), False, False)
        client = FakeBrevoSyncClient(contact=contact, update_errors=[BrevoMarketingValidationError("invalid_parameter | Invalid phone number")])

        result = synchronize_person_profile_to_brevo(person_id=person.id, client=client)

        self.assertEqual(result.outcome, BrevoPersonSyncOutcome.UPDATED_PERSON_PROFILE)
        self.assertEqual(len(client.updated), 2)
        self.assertIn("SMS", client.updated[0]["attributes"])
        self.assertNotIn("SMS", client.updated[1]["attributes"])
        self.assertEqual(result.reference_id, reference.id)

    def test_profile_sync_blank_mobile_still_sends_empty_sms_to_clear_stale_value(self):
        person = self.person(mobile="")
        self.profile_reference(person)
        contact = BrevoContact(9, person.primary_email, {"FIRSTNAME": "Ava", "LASTNAME": "Example", "SMS": "+265991000000"}, (), (), False, False)
        client = FakeBrevoSyncClient(contact=contact)

        synchronize_person_profile_to_brevo(person_id=person.id, client=client)

        self.assertEqual(client.updated[0]["attributes"]["SMS"], "")
        self.assertEqual(len(client.updated), 1)

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

    def migration_job(self, person, previous_email="ava@example.com", requested_email="new@example.com"):
        return ExternalPersonSyncJob.objects.create(
            person=person,
            provider="BREVO",
            job_type=PERSON_EMAIL_MIGRATION_SYNC,
            source_event_id=ExternalPersonSyncJob.objects.count() + 1000,
            previous_email=previous_email,
            requested_email=requested_email,
        )

    def migration_reference(self, person, contact_id=9):
        return ExternalPersonReference.objects.create(
            person=person,
            provider="BREVO",
            reference_type=ExternalPersonReference.ReferenceType.MARKETING_CONTACT,
            external_id=str(contact_id),
        )

    def test_opted_in_email_migration_updates_same_contact_and_reference(self):
        person = self.person()
        record_opt_in(person=person)
        reference = self.migration_reference(person)
        person.primary_email = "new@example.com"
        person.save(update_fields=["primary_email", "updated_at"])
        job = self.migration_job(person)
        client = FakeBrevoEmailMigrationClient(BrevoContact(9, "ava@example.com", {}, (), (), False, False))

        result = synchronize_person_email_to_brevo(job=job, client=client)

        self.assertEqual(result.outcome, BrevoPersonSyncOutcome.EMAIL_MIGRATION_UPDATED)
        self.assertEqual(client.updated, [{"contact_id": 9, "attributes": {"EMAIL": "new@example.com"}}])
        reference.refresh_from_db()
        self.assertEqual(reference.external_id, "9")

    def test_email_migration_rejects_target_owned_by_another_contact(self):
        person = self.person()
        record_opt_in(person=person)
        self.migration_reference(person)
        person.primary_email = "new@example.com"
        person.save(update_fields=["primary_email", "updated_at"])
        job = self.migration_job(person)
        client = FakeBrevoEmailMigrationClient(
            BrevoContact(9, "ava@example.com", {}, (), (), False, False),
            BrevoContact(10, "new@example.com", {}, (), (), False, False),
        )

        result = synchronize_person_email_to_brevo(job=job, client=client)

        self.assertEqual(result.outcome, BrevoPersonSyncOutcome.RECONCILIATION_REQUIRED)
        self.assertEqual(result.reason, "BREVO_TARGET_EMAIL_ALREADY_OWNED")
        self.assertFalse(client.updated)

    def test_email_migration_protects_restricted_provider_contact(self):
        for contact_id, contact in enumerate((
            BrevoContact(9, "ava@example.com", {}, (), (), True, False),
            BrevoContact(10, "ava@example.com", {}, (), (2,), False, False),
        ), start=9):
            with self.subTest(contact=contact):
                person = self.person(primary_email=f"new-{contact.email_blacklisted}-{bool(contact.list_unsubscribed)}@example.com")
                record_opt_in(person=person)
                self.migration_reference(person, contact_id=contact_id)
                requested_email = person.primary_email
                job = self.migration_job(person, requested_email=requested_email)
                client = FakeBrevoEmailMigrationClient(contact)

                result = synchronize_person_email_to_brevo(job=job, client=client)

                self.assertEqual(result.outcome, BrevoPersonSyncOutcome.RECONCILIATION_REQUIRED)
                self.assertEqual(result.reason, "BREVO_CONTACT_RESTRICTED")
                self.assertFalse(client.updated)

    def test_email_migration_rejects_opted_out_and_unknown_consent(self):
        for contact_id, state in enumerate((MarketingPreference.State.OPTED_OUT, MarketingPreference.State.UNKNOWN), start=9):
            with self.subTest(state=state):
                person = self.person(primary_email=f"{state.lower()}@example.com")
                if state == MarketingPreference.State.OPTED_OUT:
                    record_opt_out(person=person)
                self.migration_reference(person, contact_id=contact_id)
                job = self.migration_job(person, requested_email=person.primary_email)
                client = FakeBrevoEmailMigrationClient(BrevoContact(contact_id, "ava@example.com", {}, (), (), False, False))

                result = synchronize_person_email_to_brevo(job=job, client=client)

                self.assertEqual(result.outcome, BrevoPersonSyncOutcome.RECONCILIATION_REQUIRED)
                self.assertFalse(client.updated)

    def test_email_migration_requires_reference_and_existing_contact(self):
        person = self.person(primary_email="new@example.com")
        record_opt_in(person=person)
        missing_reference_job = self.migration_job(person)
        client = FakeBrevoEmailMigrationClient(BrevoContact(9, "ava@example.com", {}, (), (), False, False))

        result = synchronize_person_email_to_brevo(job=missing_reference_job, client=client)

        self.assertEqual(result.reason, "NO_ACTIVE_BREVO_REFERENCE")
        self.migration_reference(person)
        deleted_result = synchronize_person_email_to_brevo(job=self.migration_job(person), client=FakeBrevoEmailMigrationClient(None))
        self.assertEqual(deleted_result.reason, "BREVO_CONTACT_NOT_FOUND_FOR_REFERENCE")

    def test_email_migration_does_not_send_blank_email(self):
        person = self.person(primary_email="")
        record_opt_in(person=person)
        self.migration_reference(person)
        job = self.migration_job(person, requested_email="")
        client = FakeBrevoEmailMigrationClient(BrevoContact(9, "ava@example.com", {}, (), (), False, False))

        result = synchronize_person_email_to_brevo(job=job, client=client)

        self.assertEqual(result.reason, "CRM_EMAIL_INVALID")
        self.assertFalse(client.updated)

    def test_stale_email_migration_is_superseded_and_latest_target_converges(self):
        person = self.person(primary_email="c@example.com")
        record_opt_in(person=person)
        self.migration_reference(person)
        AuditEvent.objects.create(
            action=AuditEvent.Action.PERSON_UPDATED,
            entity_type="Person",
            entity_id=str(person.id),
            changes={"primary_email": {"from": "ava@example.com", "to": "b@example.com"}},
        )
        AuditEvent.objects.create(
            action=AuditEvent.Action.PERSON_UPDATED,
            entity_type="Person",
            entity_id=str(person.id),
            changes={"primary_email": {"from": "b@example.com", "to": "c@example.com"}},
        )
        stale = self.migration_job(person, requested_email="b@example.com")
        latest = self.migration_job(person, previous_email="b@example.com", requested_email="c@example.com")
        client = FakeBrevoEmailMigrationClient(BrevoContact(9, "ava@example.com", {}, (), (), False, False))

        stale_result = synchronize_person_email_to_brevo(job=stale, client=client)
        latest_result = synchronize_person_email_to_brevo(job=latest, client=client)

        self.assertEqual(stale_result.outcome, BrevoPersonSyncOutcome.EMAIL_MIGRATION_SUPERSEDED)
        self.assertEqual(latest_result.outcome, BrevoPersonSyncOutcome.EMAIL_MIGRATION_UPDATED)
        self.assertEqual(client.updated[-1]["attributes"], {"EMAIL": "c@example.com"})

    def test_email_migration_retry_after_provider_acceptance_is_idempotent(self):
        person = self.person(primary_email="new@example.com")
        record_opt_in(person=person)
        self.migration_reference(person)
        job = self.migration_job(person)
        client = FakeBrevoEmailMigrationClient(BrevoContact(9, "new@example.com", {}, (), (), False, False))

        result = synchronize_person_email_to_brevo(job=job, client=client)

        self.assertEqual(result.outcome, BrevoPersonSyncOutcome.EMAIL_MIGRATION_ALREADY_SYNCHRONIZED)
        self.assertFalse(client.updated)

    def test_email_migration_provider_collision_is_identity_conflict(self):
        person = self.person(primary_email="new@example.com")
        record_opt_in(person=person)
        self.migration_reference(person)
        job = self.migration_job(person)
        client = FakeBrevoEmailMigrationClient(BrevoContact(9, "ava@example.com", {}, (), (), False, False), update_conflict=True)

        with self.assertRaises(BrevoMarketingIdentityConflictError):
            synchronize_person_email_to_brevo(job=job, client=client)

    def test_email_migration_snapshots_are_immutable(self):
        person = self.person(primary_email="new@example.com")
        job = self.migration_job(person)
        job.requested_email = "another@example.com"

        with self.assertRaises(ValidationError):
            job.save(update_fields=["requested_email", "updated_at"])


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

    @patch("brevo_marketing.jobs.synchronize_person_email_to_brevo")
    def test_worker_processes_person_email_migration_job(self, synchronize_email):
        self.job.status = ExternalPersonSyncJob.Status.SUCCEEDED
        self.job.save(update_fields=["status", "updated_at"])
        migration_job = ExternalPersonSyncJob.objects.create(
            person=self.person,
            provider="BREVO",
            job_type=PERSON_EMAIL_MIGRATION_SYNC,
            source_event_id=999,
            previous_email="ava@example.com",
            requested_email="new@example.com",
        )
        synchronize_email.return_value = type(
            "Result", (), {"outcome": BrevoPersonSyncOutcome.EMAIL_MIGRATION_UPDATED}
        )()

        result = process_next_brevo_sync_job(client=Mock())

        migration_job.refresh_from_db()
        self.assertEqual(result.status, ExternalPersonSyncJob.Status.SUCCEEDED)
        self.assertEqual(migration_job.status, ExternalPersonSyncJob.Status.SUCCEEDED)
        synchronize_email.assert_called_once_with(
            job=migration_job,
            client=synchronize_email.call_args.kwargs["client"],
        )

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
