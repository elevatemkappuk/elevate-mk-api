import logging
from dataclasses import dataclass

from brevo import Brevo
from brevo.transactional_emails import (
    SendTransacEmailRequestReplyTo,
    SendTransacEmailRequestSender,
    SendTransacEmailRequestToItem,
)
from django.conf import settings

from notifications.exceptions import (
    TransactionalEmailConfigurationError,
    TransactionalEmailError,
)


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TransactionalEmailDeliveryResult:
    provider: str
    provider_message_id: str | None


class BrevoTransactionalEmailProvider:
    provider_name = "brevo"

    def __init__(self, *, api_key, sender_email, sender_name, reply_to_email="", reply_to_name=""):
        self.api_key = api_key
        self.sender_email = sender_email
        self.sender_name = sender_name
        self.reply_to_email = reply_to_email
        self.reply_to_name = reply_to_name

    @classmethod
    def from_settings(cls):
        return cls(
            api_key=settings.BREVO_API_KEY,
            sender_email=settings.BREVO_SENDER_EMAIL,
            sender_name=settings.BREVO_SENDER_NAME,
            reply_to_email=settings.BREVO_REPLY_TO_EMAIL,
            reply_to_name=settings.BREVO_REPLY_TO_NAME,
        )

    def send(
        self,
        *,
        recipient_email,
        recipient_name=None,
        template_id=None,
        template_params=None,
        subject=None,
        html_content=None,
    ):
        self._validate_configuration()
        if template_id is None and not (subject and html_content):
            raise TransactionalEmailConfigurationError(
                "A template ID or a subject with HTML content is required for transactional email delivery."
            )

        request = {
            "sender": SendTransacEmailRequestSender(
                email=self.sender_email,
                name=self.sender_name,
            ),
            "to": [
                SendTransacEmailRequestToItem(
                    email=recipient_email,
                    name=recipient_name,
                )
            ],
        }
        if template_id is not None:
            request["template_id"] = template_id
            request["params"] = template_params or {}
        if subject is not None:
            request["subject"] = subject
        if html_content is not None:
            request["html_content"] = html_content
        if self.reply_to_email:
            request["reply_to"] = SendTransacEmailRequestReplyTo(
                email=self.reply_to_email,
                name=self.reply_to_name or self.sender_name,
            )

        try:
            result = Brevo(api_key=self.api_key).transactional_emails.send_transac_email(**request)
        except Exception as error:
            logger.error(
                "Brevo transactional email delivery failed. template_id=%s error_type=%s status_code=%s",
                template_id,
                type(error).__name__,
                getattr(error, "status_code", None),
            )
            raise TransactionalEmailError("Transactional email delivery failed.") from error

        message_id = getattr(result, "message_id", None)
        logger.info(
            "Brevo transactional email delivered. template_id=%s message_id=%s",
            template_id,
            message_id,
        )
        return TransactionalEmailDeliveryResult(
            provider=self.provider_name,
            provider_message_id=message_id,
        )

    def _validate_configuration(self):
        missing = [
            name
            for name, value in (
                ("BREVO_API_KEY", self.api_key),
                ("BREVO_SENDER_EMAIL", self.sender_email),
                ("BREVO_SENDER_NAME", self.sender_name),
            )
            if not value
        ]
        if missing:
            raise TransactionalEmailConfigurationError(
                "Transactional email delivery is not configured: " + ", ".join(missing)
            )
