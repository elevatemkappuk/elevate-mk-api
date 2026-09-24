from django.urls import path

from marketing_preferences.views import AudiencePreviewView, PersonMarketingPreferenceView


urlpatterns = [
    path("marketing/audiences/preview/", AudiencePreviewView.as_view(), name="marketing-audience-preview"),
    path("people/<int:person_id>/marketing-preference/", PersonMarketingPreferenceView.as_view(), name="people-marketing-preference"),
]
