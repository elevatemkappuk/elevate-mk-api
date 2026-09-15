import base64
import json
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from mailchimp.exceptions import (
    MailchimpAPIError,
    MailchimpAudienceAccessError,
    MailchimpAuthenticationError,
    MailchimpConfigurationError,
    MailchimpTemporaryError,
)


@dataclass(frozen=True)
class MailchimpAudience:
    audience_id: str
    audience_name: str
    member_count: int | None
    unsubscribe_count: int | None
    cleaned_count: int | None


class MailchimpMarketingClient:
    """Minimal read-only Mailchimp Marketing API client."""

    api_base = "https://{server_prefix}.api.mailchimp.com/3.0"

    def __init__(self, *, api_key, server_prefix, audience_id, opener=urlopen):
        self.api_key = api_key
        self.server_prefix = server_prefix
        self.audience_id = audience_id
        self._opener = opener
        self._validate_configuration()

    @classmethod
    def from_settings(cls):
        from django.conf import settings

        return cls(
            api_key=settings.MAILCHIMP_API_KEY,
            server_prefix=settings.MAILCHIMP_SERVER_PREFIX,
            audience_id=settings.MAILCHIMP_AUDIENCE_ID,
        )

    def get_configured_audience(self):
        payload = self._get(f"/lists/{quote(self.audience_id, safe='')}")
        stats = payload.get("stats") or {}
        return MailchimpAudience(
            audience_id=self._required_string(payload, "id", self.audience_id),
            audience_name=self._required_string(payload, "name", ""),
            member_count=self._safe_count(stats.get("member_count")),
            unsubscribe_count=self._safe_count(stats.get("unsubscribe_count")),
            cleaned_count=self._safe_count(stats.get("cleaned_count")),
        )

    def _get(self, path):
        url = f"{self.api_base.format(server_prefix=self.server_prefix)}{path}"
        token = base64.b64encode(f"any:{self.api_key}".encode("utf-8")).decode("ascii")
        request = Request(
            url,
            method="GET",
            headers={
                "Accept": "application/json",
                "Authorization": f"Basic {token}",
            },
        )
        try:
            with self._opener(request, timeout=15) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            self._raise_http_error(error)
        except (URLError, TimeoutError, OSError) as error:
            raise MailchimpTemporaryError("Mailchimp verification could not reach the API.") from error
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise MailchimpTemporaryError("Mailchimp returned an invalid verification response.") from error

    def _validate_configuration(self):
        missing = [
            name
            for name, value in (
                ("MAILCHIMP_API_KEY", self.api_key),
                ("MAILCHIMP_SERVER_PREFIX", self.server_prefix),
                ("MAILCHIMP_AUDIENCE_ID", self.audience_id),
            )
            if not value or not str(value).strip()
        ]
        if missing:
            raise MailchimpConfigurationError(
                "Mailchimp verification is not configured: " + ", ".join(missing)
            )

        self.server_prefix = str(self.server_prefix).strip().lower()
        self.audience_id = str(self.audience_id).strip()
        if "." in self.server_prefix or "/" in self.server_prefix:
            raise MailchimpConfigurationError("MAILCHIMP_SERVER_PREFIX must be a server prefix.")

    @staticmethod
    def _raise_http_error(error):
        if error.code == 401:
            raise MailchimpAuthenticationError("Mailchimp rejected the configured credentials.") from error
        if error.code in {403, 404}:
            raise MailchimpAudienceAccessError("The configured Mailchimp audience was not found or is not accessible.") from error
        if error.code == 429 or error.code >= 500:
            raise MailchimpTemporaryError("Mailchimp temporarily could not verify the configured audience.") from error
        raise MailchimpAPIError("Mailchimp rejected the audience verification request.") from error

    @staticmethod
    def _required_string(payload, key, fallback):
        value = payload.get(key)
        return str(value).strip() if value is not None else fallback

    @staticmethod
    def _safe_count(value):
        return value if isinstance(value, int) and value >= 0 else None
