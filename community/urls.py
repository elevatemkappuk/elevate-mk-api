from django.urls import path

from community.views import CommunityIndustryListView, CommunityJoinView


urlpatterns = [
    path("community/industries/", CommunityIndustryListView.as_view(), name="community-industry-list"),
    path("community/join/", CommunityJoinView.as_view(), name="community-join"),
]
