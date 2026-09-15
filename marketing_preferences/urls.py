from django.urls import path

from marketing_preferences.views import PersonMarketingPreferenceView


urlpatterns = [
    path("people/<int:person_id>/marketing-preference/", PersonMarketingPreferenceView.as_view(), name="people-marketing-preference"),
]
