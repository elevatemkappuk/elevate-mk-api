from django.urls import path

from memberships.views import PersonMembershipEndView, PersonMembershipView


urlpatterns = [
    path(
        "people/<int:person_id>/membership/",
        PersonMembershipView.as_view(),
        name="person-membership-detail",
    ),
    path(
        "people/<int:person_id>/membership/end/",
        PersonMembershipEndView.as_view(),
        name="person-membership-end",
    ),
]
