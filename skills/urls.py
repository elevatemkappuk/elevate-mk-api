from django.urls import path

from skills.views import PersonSkillDetailView, PersonSkillListView, SkillListView


urlpatterns = [
    path("skills/", SkillListView.as_view(), name="skill-list"),
    path("people/<int:person_id>/skills/", PersonSkillListView.as_view(), name="person-skill-list"),
    path("people/<int:person_id>/skills/<int:skill_id>/", PersonSkillDetailView.as_view(), name="person-skill-detail"),
]
