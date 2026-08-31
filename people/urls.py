from django.urls import path

from people.views import (
    PeopleListView,
    PersonArchiveView,
    PersonAuditHistoryView,
    PersonDetailView,
    PersonMemberCreateView,
    PersonOverviewDetailView,
    PersonRestoreView,
)


urlpatterns = [
    path("people/", PeopleListView.as_view(), name="people-list"),
    path("people/members/", PersonMemberCreateView.as_view(), name="people-member-create"),
    path("people/<int:person_id>/archive/", PersonArchiveView.as_view(), name="people-archive"),
    path("people/<int:person_id>/restore/", PersonRestoreView.as_view(), name="people-restore"),
    path("people/<int:person_id>/", PersonDetailView.as_view(), name="people-detail"),
    path("people/<int:person_id>/overview/", PersonOverviewDetailView.as_view(), name="people-overview-detail"),
    path("people/<int:person_id>/audit-history/", PersonAuditHistoryView.as_view(), name="people-audit-history"),
]
