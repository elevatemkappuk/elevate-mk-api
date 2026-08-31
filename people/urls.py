from django.urls import path

from people.views import PeopleListView, PersonAuditHistoryView, PersonDetailView, PersonOverviewDetailView


urlpatterns = [
    path("people/", PeopleListView.as_view(), name="people-list"),
    path("people/<int:person_id>/", PersonDetailView.as_view(), name="people-detail"),
    path("people/<int:person_id>/overview/", PersonOverviewDetailView.as_view(), name="people-overview-detail"),
    path("people/<int:person_id>/audit-history/", PersonAuditHistoryView.as_view(), name="people-audit-history"),
]
