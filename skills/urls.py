from django.urls import path

from skills.views import PersonSkillListView, SkillListView


urlpatterns = [
    path("skills/", SkillListView.as_view(), name="skill-list"),
    path("people/<int:person_id>/skills/", PersonSkillListView.as_view(), name="person-skill-list"),
]

