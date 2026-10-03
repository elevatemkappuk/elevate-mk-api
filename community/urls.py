from django.urls import path

from community.views import CommunityActivationView, CommunityDirectoryDetailView, CommunityDirectoryListView, CommunityIndustryListView, CommunityJoinView, CommunityLoginView, CommunityMeView, CommunityPasswordResetConfirmView, CommunityPasswordResetRequestView, CommunityProfileView, CommunityProfileOptionsView, CommunityProfilePhotoView, CommunityProfileReviewAcknowledgementView


urlpatterns = [
    path("community/industries/", CommunityIndustryListView.as_view(), name="community-industry-list"),
    path("community/password-reset/", CommunityPasswordResetRequestView.as_view(), name="community-password-reset"),
    path("community/password-reset/confirm/", CommunityPasswordResetConfirmView.as_view(), name="community-password-reset-confirm"),
    path("community/join/", CommunityJoinView.as_view(), name="community-join"),
    path("community/login/", CommunityLoginView.as_view(), name="community-login"),
    path("community/activate/<uuid:invitation_id>/<str:token>/", CommunityActivationView.as_view(), name="community-activate"),
    path("community/me/", CommunityMeView.as_view(), name="community-me"),
    path("community/profile/", CommunityProfileView.as_view(), name="community-profile"),
    path("community/profile/options/", CommunityProfileOptionsView.as_view(), name="community-profile-options"),
    path("community/profile/photo/", CommunityProfilePhotoView.as_view(), name="community-profile-photo"),
    path("community/profile/review-acknowledgement/", CommunityProfileReviewAcknowledgementView.as_view(), name="community-profile-review-acknowledgement"),
    path("community/directory/", CommunityDirectoryListView.as_view(), name="community-directory-list"),
    path("community/directory/<uuid:directory_id>/", CommunityDirectoryDetailView.as_view(), name="community-directory-detail"),
]
