import json
from io import StringIO
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from audit.models import AuditEvent
from external_references.models import ExternalPersonReference
from mailchimp.client import MailchimpMarketingClient, MailchimpMember
from mailchimp.exceptions import (
    MailchimpAudienceAccessError,
    MailchimpAuthenticationError,
    MailchimpConfigurationError,
    MailchimpPersonSyncConflictError,
    MailchimpTemporaryError,
)
from mailchimp.services import verify_mailchimp_connection
from mailchimp.sync import synchronize_person_to_mailchimp
from people.models import Person


class FakeResponse:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.payload


class MailchimpClientTests(SimpleTestCase):
    def test_get_configured_audience_uses_shared_request_abstraction(self):
        client = MailchimpMarketingClient(api_key="secret-key", server_prefix="us21", audience_id="aud-123")
        payload = {
            "id": "aud-123",
            "name": "Elevate MK",
            "stats": {},
        }

        with patch.object(client, "_request", return_value=payload) as request:
            result = client.get_configured_audience()

        request.assert_called_once_with("GET", "/lists/aud-123")
        self.assertEqual(result.audience_id, "aud-123")

    def test_get_audience_uses_read_only_request_and_returns_safe_metadata(self):
        opener = Mock(return_value=FakeResponse({
            "id": "aud-123",
            "name": "Elevate MK",
            "stats": {"member_count": 42, "unsubscribe_count": 3, "cleaned_count": 1},
        }))
        client = MailchimpMarketingClient(
            api_key="secret-key",
            server_prefix="us21",
            audience_id="aud-123",
            opener=opener,
        )

        result = client.get_configured_audience()

        request = opener.call_args.args[0]
        self.assertEqual(request.method, "GET")
        self.assertIn("/3.0/lists/aud-123", request.full_url)
        self.assertNotIn("secret-key", request.full_url)
        self.assertEqual(result.member_count, 42)
        self.assertEqual(result.unsubscribe_count, 3)

    def test_member_lookup_uses_normalized_subscriber_hash_and_mutations_use_safe_payloads(self):
        opener = Mock(side_effect=[
            FakeResponse({
                "id": "mc-123",
                "email_address": "ava@example.com",
                "status": "subscribed",
                "merge_fields": {"FNAME": "Ava", "LNAME": "Example"},
            }),
            FakeResponse({"id": "mc-123", "email_address": "ava@example.com", "status": "pending"}),
            FakeResponse({"id": "mc-123", "email_address": "ava@example.com", "status": "subscribed"}),
        ])
        client = MailchimpMarketingClient(api_key="secret-key", server_prefix="us21", audience_id="aud-123", opener=opener)

        client.get_member(" Ava@Example.com ")
        client.create_member(email_address="ava@example.com", first_name="Ava", last_name="Example")
        client.update_subscribed_member(email_address="ava@example.com", first_name="Ava", last_name="Example")

        lookup, create, update = [call.args[0] for call in opener.call_args_list]
        self.assertEqual(lookup.method, "GET")
        self.assertEqual(create.method, "POST")
        self.assertEqual(update.method, "PATCH")
        self.assertNotIn("secret-key", create.data.decode())
        self.assertIn('"status_if_new": "pending"', create.data.decode())
        self.assertNotIn("status", update.data.decode())

    def test_missing_configuration_is_controlled_and_key_is_not_in_error(self):
        with self.assertRaises(MailchimpConfigurationError) as raised:
            MailchimpMarketingClient(api_key="", server_prefix="us21", audience_id="aud-123")
        self.assertIn("MAILCHIMP_API_KEY", str(raised.exception))
        self.assertNotIn("secret-key", str(raised.exception))

    def test_http_failures_are_classified_without_provider_body(self):
        for status, expected in (
            (401, MailchimpAuthenticationError),
            (404, MailchimpAudienceAccessError),
            (503, MailchimpTemporaryError),
        ):
            with self.subTest(status=status):
                opener = Mock(side_effect=HTTPError("https://example.test", status, "failed", {}, None))
                client = MailchimpMarketingClient(api_key="secret-key", server_prefix="us21", audience_id="aud-123", opener=opener)
                with self.assertRaises(expected):
                    client.get_configured_audience()


class MailchimpVerificationTests(SimpleTestCase):
    @override_settings(MAILCHIMP_API_KEY="secret-key", MAILCHIMP_SERVER_PREFIX="us21", MAILCHIMP_AUDIENCE_ID="aud-123")
    @patch("mailchimp.services.MailchimpMarketingClient.from_settings")
    def test_service_returns_only_safe_result(self, from_settings):
        client = from_settings.return_value
        client.get_configured_audience.return_value = type("Audience", (), {
            "audience_id": "aud-123",
            "audience_name": "Elevate MK",
            "member_count": 10,
            "unsubscribe_count": 1,
            "cleaned_count": 0,
        })()

        result = verify_mailchimp_connection()

        self.assertEqual(result.audience_id, "aud-123")
        self.assertNotIn("secret-key", repr(result))

    @override_settings(MAILCHIMP_API_KEY="secret-key", MAILCHIMP_SERVER_PREFIX="us21", MAILCHIMP_AUDIENCE_ID="aud-123")
    @patch("mailchimp.management.commands.verify_mailchimp_connection.verify_mailchimp_connection")
    def test_management_command_prints_metadata_and_not_key(self, verify):
        verify.return_value = type("Result", (), {
            "audience_id": "aud-123",
            "audience_name": "Elevate MK",
            "member_count": 10,
            "unsubscribe_count": 1,
            "cleaned_count": 0,
        })()
        output = StringIO()

        call_command("verify_mailchimp_connection", stdout=output)

        self.assertIn("Audience name: Elevate MK", output.getvalue())
        self.assertNotIn("secret-key", output.getvalue())


class MailchimpPersonSyncTests(TestCase):
    def setUp(self):
        self.person = Person.objects.create(
            first_name="Ava",
            last_name="Example",
            primary_email="Ava@Example.com",
        )

    @staticmethod
    def member(member_id="mc-123", status="subscribed", email="ava@example.com", first="Ava", last="Example"):
        return MailchimpMember(
            member_id=member_id,
            email_address=email,
            status=status,
            merge_fields={"FNAME": first, "LNAME": last},
        )

    def test_creates_new_member_as_pending_and_links_reference(self):
        client = Mock()
        client.get_member.return_value = None
        client.create_member.return_value = self.member(status="pending")

        result = synchronize_person_to_mailchimp(person_id=self.person.id, client=client)

        self.assertEqual(result.outcome, "CREATED")
        client.create_member.assert_called_once_with(
            email_address="ava@example.com",
            first_name="Ava",
            last_name="Example",
        )
        self.assertEqual(ExternalPersonReference.objects.count(), 1)
        self.assertEqual(AuditEvent.objects.filter(action=AuditEvent.Action.EXTERNAL_PERSON_REFERENCE_LINKED).count(), 1)

    def test_updates_existing_subscribed_member_only_with_owned_fields(self):
        client = Mock()
        client.get_member.return_value = self.member(first="Old", last="Name")
        client.update_subscribed_member.return_value = self.member()

        result = synchronize_person_to_mailchimp(person_id=self.person.id, client=client)

        self.assertEqual(result.outcome, "UPDATED")
        client.update_subscribed_member.assert_called_once_with(
            email_address="ava@example.com",
            first_name="Ava",
            last_name="Example",
        )

    def test_existing_matching_contact_without_reference_is_linked_without_update(self):
        client = Mock()
        client.get_member.return_value = self.member()

        result = synchronize_person_to_mailchimp(person_id=self.person.id, client=client)

        self.assertEqual(result.outcome, "EXISTING_PROVIDER_CONTACT_LINKED")
        client.update_subscribed_member.assert_not_called()
        self.assertEqual(ExternalPersonReference.objects.get().external_id, "mc-123")

    def test_repeated_sync_is_idempotent_and_does_not_duplicate_reference_or_audit(self):
        client = Mock()
        client.get_member.return_value = self.member(status="pending")

        first = synchronize_person_to_mailchimp(person_id=self.person.id, client=client)
        second = synchronize_person_to_mailchimp(person_id=self.person.id, client=client)

        self.assertEqual(first.outcome, "SKIPPED_PROTECTED_SUBSCRIPTION_STATE")
        self.assertEqual(second.outcome, "SKIPPED_PROTECTED_SUBSCRIPTION_STATE")
        self.assertEqual(ExternalPersonReference.objects.count(), 1)
        self.assertEqual(AuditEvent.objects.filter(action=AuditEvent.Action.EXTERNAL_PERSON_REFERENCE_LINKED).count(), 1)

    def test_ineligible_people_are_skipped_without_provider_calls(self):
        client = Mock()
        for person in (
            Person.objects.create(first_name="No", last_name="Email"),
            Person.objects.create(record_type=Person.RecordType.TECHNICAL, first_name="Tech", last_name="User", primary_email="tech@example.com"),
            Person.objects.create(first_name="Archived", last_name="User", primary_email="archived@example.com", archived_at=timezone.now()),
        ):
            result = synchronize_person_to_mailchimp(person_id=person.id, client=client)
            self.assertEqual(result.outcome, "SKIPPED")
        client.get_member.assert_not_called()

    def test_unsubscribed_and_cleaned_members_are_not_resubscribed_or_updated(self):
        for status in ("unsubscribed", "cleaned"):
            with self.subTest(status=status):
                client = Mock()
                client.get_member.return_value = self.member(status=status)

                result = synchronize_person_to_mailchimp(person_id=self.person.id, client=client)

                self.assertEqual(result.outcome, "SKIPPED_PROTECTED_SUBSCRIPTION_STATE")
                client.update_subscribed_member.assert_not_called()
                ExternalPersonReference.objects.all().delete()

    def test_provider_failure_is_propagated_without_reference(self):
        client = Mock()
        client.get_member.side_effect = MailchimpTemporaryError("temporary")

        with self.assertRaises(MailchimpTemporaryError):
            synchronize_person_to_mailchimp(person_id=self.person.id, client=client)
        self.assertFalse(ExternalPersonReference.objects.exists())

    def test_existing_reference_conflict_fails_without_provider_update(self):
        other = Person.objects.create(first_name="Other", last_name="Person", primary_email="other@example.com")
        ExternalPersonReference.objects.create(
            person=other,
            provider="MAILCHIMP",
            reference_type=ExternalPersonReference.ReferenceType.MARKETING_CONTACT,
            external_id="mc-123",
        )
        client = Mock()
        client.get_member.return_value = self.member()

        with self.assertRaises(MailchimpPersonSyncConflictError):
            synchronize_person_to_mailchimp(person_id=self.person.id, client=client)
        client.update_subscribed_member.assert_not_called()

    @patch("mailchimp.management.commands.sync_mailchimp_person.synchronize_person_to_mailchimp")
    def test_sync_management_command_reports_safe_structured_result(self, synchronize):
        synchronize.return_value = type("Result", (), {
            "person_id": self.person.id,
            "outcome": "CREATED",
            "reason": None,
            "member_id": "mc-123",
            "provider_status": "pending",
            "reference_id": 7,
        })()
        output = StringIO()

        call_command("sync_mailchimp_person", str(self.person.id), stdout=output)

        self.assertIn("Outcome: CREATED", output.getvalue())
        self.assertIn("Person ID: ", output.getvalue())
        synchronize.assert_called_once_with(person_id=self.person.id)
