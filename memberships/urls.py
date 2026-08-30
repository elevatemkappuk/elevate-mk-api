from django.urls import path

from memberships.views import PersonMembershipView


urlpatterns = [
    path(
        "people/<int:person_id>/membership/",
        PersonMembershipView.as_view(),
        name="person-membership-detail",
    ),
]
