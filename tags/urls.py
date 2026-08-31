from django.urls import path

from tags.views import PersonTagListView, PersonTagRemoveView, TagListView


urlpatterns = [
    path("tags/", TagListView.as_view(), name="tag-list"),
    path("people/<int:person_id>/tags/", PersonTagListView.as_view(), name="person-tag-list"),
    path(
        "people/<int:person_id>/tags/<int:tag_id>/remove/",
        PersonTagRemoveView.as_view(),
        name="person-tag-remove",
    ),
]
