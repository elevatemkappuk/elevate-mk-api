from django.urls import path

from brevo_marketing.views import BrevoMarketingWebhookView


urlpatterns = [
    path("webhooks/brevo/marketing/", BrevoMarketingWebhookView.as_view(), name="brevo-marketing-webhook"),
]
