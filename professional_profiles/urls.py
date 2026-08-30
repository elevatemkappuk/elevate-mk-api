from django.urls import path

from professional_profiles.views import IndustryListView, ProfessionalProfileDetailView


urlpatterns = [
    path("industries/", IndustryListView.as_view(), name="industry-list"),
    path(
        "people/<int:person_id>/professional-profile/",
        ProfessionalProfileDetailView.as_view(),
        name="person-professional-profile-detail",
    ),
]

