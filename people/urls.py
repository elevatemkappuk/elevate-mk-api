from django.urls import path

from people.views import PeopleListView, Person360DetailView, PersonDetailView


urlpatterns = [
    path("people/", PeopleListView.as_view(), name="people-list"),
    path("people/<int:person_id>/", PersonDetailView.as_view(), name="people-detail"),
    path("people/<int:person_id>/360/", Person360DetailView.as_view(), name="people-360-detail"),
]
