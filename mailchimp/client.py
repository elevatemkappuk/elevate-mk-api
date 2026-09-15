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


@dataclass(frozen=True)
class MailchimpMember:
    member_id: str
    email_address: str
    status: str
    merge_fields: dict


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
        payload = self._request("GET", f"/lists/{quote(self.audience_id, safe='')}")
        stats = payload.get("stats") or {}
        return MailchimpAudience(
            audience_id=self._required_string(payload, "id", self.audience_id),
            audience_name=self._required_string(payload, "name", ""),
            member_count=self._safe_count(stats.get("member_count")),
            unsubscribe_count=self._safe_count(stats.get("unsubscribe_count")),
            cleaned_count=self._safe_count(stats.get("cleaned_count")),
        )

    def get_member(self, email_address):
        subscriber_hash = self.subscriber_hash(email_address)
        payload = self._request(
            "GET",
            f"/lists/{quote(self.audience_id, safe='')}/members/{subscriber_hash}",
            not_found_is_none=True,
        )
        if payload is None:
            return None
        return self._member_from_payload(payload, email_address)

    def create_member(self, *, email_address, first_name, last_name):
        payload = self._request(
            "POST",
            f"/lists/{quote(self.audience_id, safe='')}/members",
            body={
                "email_address": email_address,
                # Pending avoids treating CRM membership as marketing consent.
                "status_if_new": "pending",
                "merge_fields": self._merge_fields(first_name, last_name),
            },
        )
        return self._member_from_payload(payload, email_address)

    def update_subscribed_member(self, *, email_address, first_name, last_name):
        subscriber_hash = self.subscriber_hash(email_address)
        payload = self._request(
            "PATCH",
            f"/lists/{quote(self.audience_id, safe='')}/members/{subscriber_hash}",
            body={
                "email_address": email_address,
                "merge_fields": self._merge_fields(first_name, last_name),
            },
        )
        return self._member_from_payload(payload, email_address)

    @staticmethod
    def subscriber_hash(email_address):
        import hashlib

        return hashlib.md5(email_address.strip().casefold().encode("utf-8")).hexdigest()

    @staticmethod
    def _merge_fields(first_name, last_name):
        return {"FNAME": first_name, "LNAME": last_name}

    @classmethod
    def _member_from_payload(cls, payload, fallback_email):
        member_id = str(payload.get("id") or "").strip()
        if not member_id:
            raise MailchimpAPIError("Mailchimp returned an invalid member response.")
        return MailchimpMember(
            member_id=member_id,
            email_address=str(payload.get("email_address") or fallback_email).strip(),
            status=str(payload.get("status") or "").strip().lower(),
            merge_fields=dict(payload.get("merge_fields") or {}),
        )

    def _request(self, method, path, *, body=None, not_found_is_none=False):
        url = f"{self.api_base.format(server_prefix=self.server_prefix)}{path}"
        token = base64.b64encode(f"any:{self.api_key}".encode("utf-8")).decode("ascii")
        request = Request(
            url,
            method=method,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": f"Basic {token}",
            },
        )
        if body is not None:
            request.data = json.dumps(body).encode("utf-8")
        try:
            with self._opener(request, timeout=15) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            if error.code == 404 and not_found_is_none:
                return None
            self._raise_http_error(error, not_found_is_none=not_found_is_none)
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
    def _raise_http_error(error, *, not_found_is_none=False):
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
