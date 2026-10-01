from django.urls import path

from community.views import CommunityActivationView, CommunityIndustryListView, CommunityJoinView, CommunityLoginView, CommunityMeView, CommunityPasswordResetConfirmView, CommunityPasswordResetRequestView


urlpatterns = [
    path("community/industries/", CommunityIndustryListView.as_view(), name="community-industry-list"),
    path("community/password-reset/", CommunityPasswordResetRequestView.as_view(), name="community-password-reset"),
    path("community/password-reset/confirm/", CommunityPasswordResetConfirmView.as_view(), name="community-password-reset-confirm"),
    path("community/join/", CommunityJoinView.as_view(), name="community-join"),
    path("community/login/", CommunityLoginView.as_view(), name="community-login"),
    path("community/activate/<uuid:invitation_id>/<str:token>/", CommunityActivationView.as_view(), name="community-activate"),
    path("community/me/", CommunityMeView.as_view(), name="community-me"),
]
