from django.urls import path

from interests.views import InterestListView, PersonInterestDetailView, PersonInterestListView


urlpatterns = [
    path("interests/", InterestListView.as_view(), name="interest-list"),
    path("people/<int:person_id>/interests/", PersonInterestListView.as_view(), name="person-interest-list"),
    path(
        "people/<int:person_id>/interests/<int:interest_id>/",
        PersonInterestDetailView.as_view(),
        name="person-interest-detail",
    ),
]
