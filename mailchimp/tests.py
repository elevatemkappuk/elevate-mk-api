import json
from io import StringIO
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from django.core.management import call_command
from django.test import SimpleTestCase, override_settings

from mailchimp.client import MailchimpMarketingClient
from mailchimp.exceptions import (
    MailchimpAudienceAccessError,
    MailchimpAuthenticationError,
    MailchimpConfigurationError,
    MailchimpTemporaryError,
)
from mailchimp.services import verify_mailchimp_connection


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
