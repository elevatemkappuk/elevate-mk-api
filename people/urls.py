from django.urls import path

from people.views import PeopleListView


urlpatterns = [
    path("people/", PeopleListView.as_view(), name="people-list"),
]
