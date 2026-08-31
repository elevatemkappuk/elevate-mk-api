from django.urls import path

from interests.views import InterestListView, PersonInterestListView


urlpatterns = [
    path("interests/", InterestListView.as_view(), name="interest-list"),
    path("people/<int:person_id>/interests/", PersonInterestListView.as_view(), name="person-interest-list"),
]

