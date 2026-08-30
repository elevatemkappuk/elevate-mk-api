from django.urls import path

from memberships.views import PersonMembershipDetailView


urlpatterns = [
    path(
        "people/<int:person_id>/membership/",
        PersonMembershipDetailView.as_view(),
        name="person-membership-detail",
    ),
]
