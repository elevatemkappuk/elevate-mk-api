from unittest.mock import Mock, patch

from django.test import SimpleTestCase, override_settings

from notifications.exceptions import (
    TransactionalEmailConfigurationError,
    TransactionalEmailError,
)
from notifications.providers.brevo import BrevoTransactionalEmailProvider
from notifications.services import send_transactional_email


@override_settings(
    BREVO_API_KEY="test-api-key",
    BREVO_SENDER_EMAIL="no-reply@example.com",
    BREVO_SENDER_NAME="Elevate MK",
    BREVO_REPLY_TO_EMAIL="support@example.com",
    BREVO_REPLY_TO_NAME="Elevate MK Support",
)
class BrevoTransactionalEmailProviderTests(SimpleTestCase):
    def build_client(self, message_id="brevo-message-id"):
        client = Mock()
        client.transactional_emails.send_transac_email.return_value = Mock(message_id=message_id)
        return client

    @patch("notifications.providers.brevo.Brevo")
    def test_template_email_passes_template_recipient_sender_and_parameters(self, brevo_client):
        client = self.build_client()
        brevo_client.return_value = client

        result = send_transactional_email(
            recipient_email="recipient@example.com",
            recipient_name="Recipient Name",
            template_id=42,
            template_params={"reset_url": "https://example.com/reset/token"},
        )

        kwargs = client.transactional_emails.send_transac_email.call_args.kwargs
        self.assertEqual(kwargs["template_id"], 42)
        self.assertEqual(kwargs["params"], {"reset_url": "https://example.com/reset/token"})
        self.assertEqual(kwargs["to"][0].email, "recipient@example.com")
        self.assertEqual(kwargs["to"][0].name, "Recipient Name")
        self.assertEqual(kwargs["sender"].email, "no-reply@example.com")
        self.assertEqual(kwargs["sender"].name, "Elevate MK")
        self.assertEqual(kwargs["reply_to"].email, "support@example.com")
        self.assertEqual(kwargs["reply_to"].name, "Elevate MK Support")
        self.assertEqual(result.provider, "brevo")
        self.assertEqual(result.provider_message_id, "brevo-message-id")

    @patch("notifications.providers.brevo.Brevo")
    def test_template_id_is_converted_to_the_sdk_integer_type(self, brevo_client):
        client = self.build_client()
        brevo_client.return_value = client

        send_transactional_email(
            recipient_email="recipient@example.com",
            template_id="10",
            template_params={"reset_url": "sensitive"},
        )

        self.assertEqual(client.transactional_emails.send_transac_email.call_args.kwargs["template_id"], 10)

    @override_settings(BREVO_REPLY_TO_EMAIL="", BREVO_REPLY_TO_NAME="")
    @patch("notifications.providers.brevo.Brevo")
    def test_reply_to_is_omitted_when_not_configured(self, brevo_client):
        client = self.build_client()
        brevo_client.return_value = client

        send_transactional_email(
            recipient_email="recipient@example.com",
            template_id=42,
            template_params={},
        )

        kwargs = client.transactional_emails.send_transac_email.call_args.kwargs
        self.assertNotIn("reply_to", kwargs)

    @patch("notifications.providers.brevo.Brevo")
    def test_provider_failure_is_translated_without_exposing_the_api_key(self, brevo_client):
        client = self.build_client()
        client.transactional_emails.send_transac_email.side_effect = RuntimeError("test-api-key")
        brevo_client.return_value = client

        with self.assertRaises(TransactionalEmailError) as raised:
            send_transactional_email(
                recipient_email="recipient@example.com",
                template_id=42,
                template_params={"reset_url": "sensitive"},
            )

        self.assertNotIn("test-api-key", str(raised.exception))

    @patch("notifications.providers.brevo.Brevo")
    def test_bad_request_logs_safe_provider_diagnostics_without_template_parameters(self, brevo_client):
        class BadRequestError(Exception):
            status_code = 400
            body = {"code": "invalid_parameter", "message": "Template ID is invalid."}

        client = self.build_client()
        client.transactional_emails.send_transac_email.side_effect = BadRequestError()
        brevo_client.return_value = client

        with self.assertLogs("notifications.providers.brevo", level="ERROR") as logs:
            with self.assertRaises(TransactionalEmailError):
                send_transactional_email(
                    recipient_email="recipient@example.com",
                    template_id="10",
                    template_params={"reset_url": "https://example.com/reset/secret-token"},
                )

        output = "\n".join(logs.output)
        self.assertIn("status=400", output)
        self.assertIn("code=invalid_parameter", output)
        self.assertIn("message=Template ID is invalid.", output)
        self.assertIn("template_id=10", output)
        self.assertNotIn("secret-token", output)
        self.assertNotIn("test-api-key", output)

    def test_invalid_template_id_fails_as_safe_configuration_error(self):
        with self.assertRaises(TransactionalEmailConfigurationError):
            send_transactional_email(
                recipient_email="recipient@example.com",
                template_id="not-a-number",
                template_params={},
            )

    @override_settings(BREVO_API_KEY="")
    @patch("notifications.providers.brevo.Brevo")
    def test_missing_runtime_configuration_fails_before_creating_a_client(self, brevo_client):
        with self.assertRaises(TransactionalEmailConfigurationError):
            send_transactional_email(
                recipient_email="recipient@example.com",
                template_id=42,
                template_params={},
            )

        brevo_client.assert_not_called()

    @patch("notifications.providers.brevo.Brevo")
    def test_provider_is_not_instantiated_until_a_send_is_requested(self, brevo_client):
        BrevoTransactionalEmailProvider.from_settings()

        brevo_client.assert_not_called()


class TransactionalEmailServiceTests(SimpleTestCase):
    def test_service_can_use_a_provider_boundary_without_network_access(self):
        provider = Mock()
        provider.send.return_value = "result"

        result = send_transactional_email(
            recipient_email="recipient@example.com",
            recipient_name="Recipient Name",
            template_id=42,
            template_params={"reset_url": "sensitive"},
            provider=provider,
        )

        self.assertEqual(result, "result")
        provider.send.assert_called_once_with(
            recipient_email="recipient@example.com",
            recipient_name="Recipient Name",
            template_id=42,
            template_params={"reset_url": "sensitive"},
            subject=None,
            html_content=None,
        )
