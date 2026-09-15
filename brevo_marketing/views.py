import json
import logging

from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from brevo_marketing.webhooks import (
    BrevoWebhookAuthenticationError,
    BrevoWebhookConfigurationError,
    BrevoWebhookPayloadError,
    authenticate_webhook_request,
    handle_unsubscribe_event,
    parse_webhook_payload,
)


logger = logging.getLogger(__name__)


class BrevoMarketingWebhookView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        try:
            authenticate_webhook_request(request)
        except BrevoWebhookConfigurationError:
            logger.error("Brevo marketing webhook authentication is not configured.")
            return Response({"detail": "Webhook authentication is unavailable."}, status=503)
        except BrevoWebhookAuthenticationError:
            logger.warning("Brevo marketing webhook authentication failed.")
            return Response({"detail": "Webhook authentication failed."}, status=401, headers={"WWW-Authenticate": "Basic"})

        try:
            payload = json.loads(request.body.decode("utf-8"))
            event = parse_webhook_payload(payload)
        except (UnicodeDecodeError, json.JSONDecodeError, BrevoWebhookPayloadError):
            logger.warning("Malformed Brevo marketing webhook payload.")
            return Response({"detail": "Invalid webhook payload."}, status=400)
        except BrevoWebhookConfigurationError:
            logger.error("Brevo marketing webhook list configuration is invalid.")
            return Response({"detail": "Webhook configuration is unavailable."}, status=503)

        if event is None:
            logger.info("Unsupported Brevo marketing webhook event acknowledged.")
            return Response({"status": "ignored", "outcome": "UNSUPPORTED_EVENT"}, status=200)

        try:
            result = handle_unsubscribe_event(event)
        except Exception:
            logger.exception("Brevo marketing webhook processing failed. event_id=%s", event.event_id)
            return Response({"detail": "Webhook processing failed."}, status=500)
        return Response({"status": "accepted", "outcome": result.outcome}, status=200)
