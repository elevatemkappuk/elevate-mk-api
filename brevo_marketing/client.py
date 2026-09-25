import re
from dataclasses import dataclass

import httpx
from brevo import Brevo, ForbiddenError, NotFoundError, TooManyRequestsError, UnauthorizedError
from brevo.core.api_error import ApiError
from brevo.contacts.types.add_contact_to_list_request_body_ids import AddContactToListRequestBodyIds
from brevo.email_campaigns.types.create_email_campaign_request_recipients import CreateEmailCampaignRequestRecipients
from brevo.email_campaigns.types.create_email_campaign_request_sender import CreateEmailCampaignRequestSender
from django.conf import settings

from brevo_marketing.exceptions import (
    BrevoMarketingAPIError,
    BrevoMarketingAccessError,
    BrevoMarketingAuthenticationError,
    BrevoMarketingConfigurationError,
    BrevoMarketingPropagationDelay,
    BrevoMarketingTemporaryError,
    BrevoMarketingRateLimitError,
    BrevoMarketingValidationError,
)


@dataclass(frozen=True)
class BrevoContactAttribute:
    name: str
    attribute_type: str | None
    category: str | None
    options: tuple[str, ...] = ()


@dataclass(frozen=True)
class BrevoContactList:
    list_id: int
    name: str
    total_subscribers: int | None
    total_blacklisted: int | None
    unique_subscribers: int | None
    folder_id: int | None = None


@dataclass(frozen=True)
class BrevoEmailCampaign:
    campaign_id: int
    name: str
    status: str | None = None


@dataclass(frozen=True)
class BrevoContact:
    contact_id: int
    email: str
    attributes: dict
    list_ids: tuple[int, ...]
    list_unsubscribed: tuple[int, ...]
    email_blacklisted: bool
    sms_blacklisted: bool


class BrevoMarketingClient:
    """Brevo Contacts API facade for safe discovery and one-Person marketing sync."""

    def __init__(self, *, api_key, marketing_list_id=None, campaign_folder_id=None, sdk_factory=Brevo):
        self.api_key = api_key
        self.marketing_list_id = marketing_list_id
        self.campaign_folder_id = campaign_folder_id
        self._sdk_factory = sdk_factory
        self._validate_configuration()
        self._client = sdk_factory(api_key=api_key)

    @classmethod
    def from_settings(cls, *, require_marketing_list=False):
        client = cls(
            api_key=settings.BREVO_API_KEY,
            marketing_list_id=settings.BREVO_MARKETING_LIST_ID,
            campaign_folder_id=settings.BREVO_MARKETING_CAMPAIGN_FOLDER_ID,
        )
        if require_marketing_list:
            client.get_marketing_list_id()
        return client

    def get_marketing_list_id(self):
        try:
            value = int(str(self.marketing_list_id).strip())
        except (TypeError, ValueError) as error:
            raise BrevoMarketingConfigurationError(
                "Brevo marketing synchronization is not configured: BREVO_MARKETING_LIST_ID must be a positive integer."
            ) from error
        if value <= 0:
            raise BrevoMarketingConfigurationError(
                "Brevo marketing synchronization is not configured: BREVO_MARKETING_LIST_ID must be a positive integer."
            )
        return value

    def get_contact_attributes(self):
        response = self._call(lambda: self._client.contacts.get_attributes())
        return tuple(self._attribute_from_provider(item) for item in (getattr(response, "attributes", None) or ()))

    def get_contact(self, email):
        response = self._call(
            lambda: self._client.contacts.get_contact_info(email.strip().casefold(), identifier_type="email_id"),
            not_found_is_none=True,
        )
        if response is None:
            return None
        return self._contact_from_provider(response)

    def get_contact_by_id(self, contact_id):
        response = self._call(
            lambda: self._client.contacts.get_contact_info(
                str(contact_id),
                identifier_type="contact_id",
            ),
            not_found_is_none=True,
        )
        if response is None:
            return None
        return self._contact_from_provider(response)

    def create_contact(self, *, email, attributes, list_id):
        response = self._call(
            lambda: self._client.contacts.create_contact(
                email=email,
                attributes=attributes,
                list_ids=[list_id],
                get_id=True,
            )
        )
        contact_id = getattr(response, "id", None)
        if not isinstance(contact_id, int) or contact_id <= 0:
            raise BrevoMarketingAPIError("Brevo returned an invalid contact identity.")
        return self.get_contact(email) or BrevoContact(
            contact_id=contact_id,
            email=email,
            attributes=dict(attributes),
            list_ids=(list_id,),
            list_unsubscribed=(),
            email_blacklisted=False,
            sms_blacklisted=False,
        )

    def update_contact(self, *, contact_id, attributes=None, list_id=None, email_blacklisted=None):
        kwargs = {"identifier": contact_id, "identifier_type": "contact_id"}
        if attributes is not None:
            kwargs["attributes"] = attributes
        if list_id is not None:
            kwargs["list_ids"] = [list_id]
        if email_blacklisted is not None:
            kwargs["email_blacklisted"] = email_blacklisted
        self._call(lambda: self._client.contacts.update_contact(**kwargs))

    @staticmethod
    def _contact_from_provider(item):
        return BrevoContact(
            contact_id=int(getattr(item, "id")),
            email=str(getattr(item, "email", "") or "").strip().casefold(),
            attributes=dict(getattr(item, "attributes", None) or {}),
            list_ids=tuple(int(value) for value in (getattr(item, "list_ids", None) or ())),
            list_unsubscribed=tuple(int(value) for value in (getattr(item, "list_unsubscribed", None) or ())),
            email_blacklisted=bool(getattr(item, "email_blacklisted", False)),
            sms_blacklisted=bool(getattr(item, "sms_blacklisted", False)),
        )

    def get_contact_lists(self):
        results = []
        offset = 0
        while True:
            response = self._call(lambda: self._client.contacts.get_lists(limit=50, offset=offset))
            items = getattr(response, "lists", None) or ()
            results.extend(self._list_from_provider(item) for item in items)
            count = getattr(response, "count", None)
            if not items or not isinstance(count, int) or len(results) >= count or len(items) < 50:
                break
            offset += len(items)
        return tuple(results)

    def get_campaign_folder_id(self):
        if self.campaign_folder_id in (None, ""):
            marketing_list_id = self.get_marketing_list_id()
            response = self._call(lambda: self._client.contacts.get_list(marketing_list_id))
            folder_id = getattr(response, "folder_id", None)
            if isinstance(folder_id, int) and folder_id > 0:
                return folder_id
            raise BrevoMarketingConfigurationError(
                "Brevo campaign preparation could not determine the configured marketing list folder."
            )
        try:
            value = int(str(self.campaign_folder_id).strip())
        except (TypeError, ValueError) as error:
            raise BrevoMarketingConfigurationError(
                "Brevo campaign preparation is not configured: BREVO_MARKETING_CAMPAIGN_FOLDER_ID must be a positive integer."
            ) from error
        if value <= 0:
            raise BrevoMarketingConfigurationError(
                "Brevo campaign preparation is not configured: BREVO_MARKETING_CAMPAIGN_FOLDER_ID must be a positive integer."
            )
        return value

    def create_campaign_list(self, *, name):
        folder_id = self.get_campaign_folder_id()
        response = self._call(lambda: self._client.contacts.create_list(folder_id=folder_id, name=name))
        list_id = getattr(response, "id", None)
        if not isinstance(list_id, int) or list_id <= 0:
            raise BrevoMarketingAPIError("Brevo returned an invalid campaign list identity.")
        return BrevoContactList(list_id=list_id, name=name, total_subscribers=0, total_blacklisted=0, unique_subscribers=0, folder_id=folder_id)

    def add_contact_to_list(self, *, list_id, contact_id):
        self._call(lambda: self._client.contacts.add_contact_to_list(
            list_id=int(list_id),
            request=AddContactToListRequestBodyIds(ids=[int(contact_id)]),
        ))

    def find_draft_campaign_by_name(self, *, name):
        offset = 0
        while True:
            response = self._call(lambda: self._client.email_campaigns.get_email_campaigns(
                type="classic", status="draft", limit=50, offset=offset, sort="asc", exclude_html_content=True,
            ))
            items = getattr(response, "campaigns", None) or ()
            for item in items:
                if str(getattr(item, "name", "") or "") == name:
                    campaign_id = getattr(item, "id", None)
                    if isinstance(campaign_id, int) and campaign_id > 0:
                        return BrevoEmailCampaign(campaign_id=campaign_id, name=name, status="draft")
            count = getattr(response, "count", None)
            if not items or not isinstance(count, int) or offset + len(items) >= count or len(items) < 50:
                return None
            offset += len(items)

    def create_email_campaign_draft(self, *, name, subject, template_id, list_id):
        try:
            template_id = int(template_id)
        except (TypeError, ValueError) as error:
            raise BrevoMarketingConfigurationError(
                "Brevo campaign preparation is not configured: BREVO_MARKETING_STARTER_TEMPLATE_ID must be a positive integer."
            ) from error
        if template_id <= 0:
            raise BrevoMarketingConfigurationError(
                "Brevo campaign preparation is not configured: BREVO_MARKETING_STARTER_TEMPLATE_ID must be a positive integer."
            )
        sender = CreateEmailCampaignRequestSender(
            email=str(settings.BREVO_SENDER_EMAIL).strip(),
            name=str(settings.BREVO_SENDER_NAME).strip() or None,
        )
        if not sender.email:
            raise BrevoMarketingConfigurationError("Brevo campaign preparation requires BREVO_SENDER_EMAIL.")
        subject = " ".join(str(subject or "").split())
        if not subject:
            raise BrevoMarketingConfigurationError("Brevo campaign preparation requires a non-empty draft subject.")
        response = self._call(lambda: self._client.email_campaigns.create_email_campaign(
            name=name,
            subject=subject,
            sender=sender,
            recipients=CreateEmailCampaignRequestRecipients(listIds=[int(list_id)]),
            template_id=template_id,
        ))
        campaign_id = getattr(response, "id", None)
        if not isinstance(campaign_id, int) or campaign_id <= 0:
            raise BrevoMarketingAPIError("Brevo returned an invalid campaign identity.")
        return BrevoEmailCampaign(campaign_id=campaign_id, name=name, status="draft")

    def _validate_configuration(self):
        if not self.api_key or not str(self.api_key).strip():
            raise BrevoMarketingConfigurationError(
                "Brevo marketing verification is not configured: BREVO_API_KEY"
            )

    @staticmethod
    def _attribute_from_provider(item):
        options = []
        for option in getattr(item, "enumeration", None) or ():
            value = getattr(option, "value_str", None)
            if value is None:
                value = getattr(option, "value", None)
            if value is not None:
                options.append(str(value))
        options.extend(str(value) for value in (getattr(item, "multi_category_options", None) or ()))
        return BrevoContactAttribute(
            name=str(getattr(item, "name", "") or "").strip(),
            attribute_type=getattr(item, "type", None),
            category=getattr(item, "category", None),
            options=tuple(options),
        )

    @staticmethod
    def _list_from_provider(item):
        return BrevoContactList(
            list_id=int(getattr(item, "id")),
            name=str(getattr(item, "name", "") or "").strip(),
            total_subscribers=BrevoMarketingClient._safe_count(getattr(item, "total_subscribers", None)),
            total_blacklisted=BrevoMarketingClient._safe_count(getattr(item, "total_blacklisted", None)),
            unique_subscribers=BrevoMarketingClient._safe_count(getattr(item, "unique_subscribers", None)),
            folder_id=BrevoMarketingClient._safe_count(getattr(item, "folder_id", None)),
        )

    @staticmethod
    def _safe_count(value):
        return value if isinstance(value, int) and value >= 0 else None

    @staticmethod
    def _call(operation, *, not_found_is_none=False):
        try:
            return operation()
        except NotFoundError as error:
            if not_found_is_none:
                return None
            raise BrevoMarketingAccessError("The configured Brevo account cannot access the requested resource.") from error
        except UnauthorizedError as error:
            raise BrevoMarketingAuthenticationError("Brevo rejected the configured credentials.") from error
        except (ForbiddenError, NotFoundError) as error:
            raise BrevoMarketingAccessError("The configured Brevo account cannot access the requested resource.") from error
        except TooManyRequestsError as error:
            raise BrevoMarketingRateLimitError("Brevo rate-limited the marketing API request.") from error
        except httpx.HTTPError as error:
            raise BrevoMarketingTemporaryError("The Brevo API request could not reach the provider.") from error
        except ApiError as error:
            status_code = getattr(error, "status_code", None)
            if isinstance(status_code, int) and (status_code == 429 or status_code >= 500):
                raise BrevoMarketingTemporaryError("Brevo temporarily could not complete the API request.") from error
            if status_code in {400, 422}:
                message = BrevoMarketingClient._safe_error_message(error)
                if status_code == 400 and "invalid_parameter" in message and "There are no contacts associated with the given recipients info" in message:
                    raise BrevoMarketingPropagationDelay(message) from error
                raise BrevoMarketingValidationError(message) from error
            raise BrevoMarketingAPIError("Brevo rejected the marketing API request.") from error
        except Exception as error:
            raise BrevoMarketingAPIError("Brevo returned an unexpected marketing API failure.") from error

    @staticmethod
    def _safe_error_message(error):
        body = getattr(error, "body", None)
        values = []
        for key in ("code", "message", "detail"):
            value = body.get(key) if isinstance(body, dict) else getattr(body, key, None)
            if value:
                values.append(str(value))
        message = " | ".join(values) or "Brevo rejected the marketing API request."
        message = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[redacted-email]", message)
        message = re.sub(r"(?i)(api[-_ ]?key|authorization|bearer|secret|token)\s*[:=]\s*[^\s|]+", r"\1=[redacted]", message)
        return " ".join(message.split())[:500]
