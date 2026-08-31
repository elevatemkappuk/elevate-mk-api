from django.urls import path

from tags.views import PersonTagListView, TagListView


urlpatterns = [
    path("tags/", TagListView.as_view(), name="tag-list"),
    path("people/<int:person_id>/tags/", PersonTagListView.as_view(), name="person-tag-list"),
]

