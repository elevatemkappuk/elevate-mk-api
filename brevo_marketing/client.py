import re
from dataclasses import dataclass

import httpx
from brevo import Brevo, ForbiddenError, NotFoundError, TooManyRequestsError, UnauthorizedError
from brevo.core.api_error import ApiError
from django.conf import settings

from brevo_marketing.exceptions import (
    BrevoMarketingAPIError,
    BrevoMarketingAccessError,
    BrevoMarketingAuthenticationError,
    BrevoMarketingConfigurationError,
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

    def __init__(self, *, api_key, marketing_list_id=None, sdk_factory=Brevo):
        self.api_key = api_key
        self.marketing_list_id = marketing_list_id
        self._sdk_factory = sdk_factory
        self._validate_configuration()
        self._client = sdk_factory(api_key=api_key)

    @classmethod
    def from_settings(cls, *, require_marketing_list=False):
        client = cls(
            api_key=settings.BREVO_API_KEY,
            marketing_list_id=settings.BREVO_MARKETING_LIST_ID,
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
                raise BrevoMarketingValidationError(BrevoMarketingClient._safe_error_message(error)) from error
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
