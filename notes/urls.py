from django.urls import path

from notes.views import (
    PersonNoteArchiveView,
    PersonNoteListView,
    PersonNoteRestoreView,
    PersonNoteUpdateView,
)


urlpatterns = [
    path("people/<int:person_id>/notes/", PersonNoteListView.as_view(), name="person-note-list"),
    path("people/<int:person_id>/notes/<int:note_id>/", PersonNoteUpdateView.as_view(), name="person-note-detail"),
    path(
        "people/<int:person_id>/notes/<int:note_id>/archive/",
        PersonNoteArchiveView.as_view(),
        name="person-note-archive",
    ),
    path(
        "people/<int:person_id>/notes/<int:note_id>/restore/",
        PersonNoteRestoreView.as_view(),
        name="person-note-restore",
    ),
]
