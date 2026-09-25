from django.urls import path

from .views import CampaignDetailView, CampaignListCreateView, CampaignPrepareProviderView, CampaignPrepareView, CampaignRecipientListView


urlpatterns = [
    path("marketing/campaigns/", CampaignListCreateView.as_view(), name="marketing-campaign-list-create"),
    path("marketing/campaigns/<int:campaign_id>/", CampaignDetailView.as_view(), name="marketing-campaign-detail"),
    path("marketing/campaigns/<int:campaign_id>/prepare/", CampaignPrepareView.as_view(), name="marketing-campaign-prepare"),
    path("marketing/campaigns/<int:campaign_id>/prepare-provider/", CampaignPrepareProviderView.as_view(), name="marketing-campaign-prepare-provider"),
    path("marketing/campaigns/<int:campaign_id>/recipients/", CampaignRecipientListView.as_view(), name="marketing-campaign-recipients"),
]
