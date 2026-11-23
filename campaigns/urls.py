from django.urls import path

from .views import CampaignArchiveView, CampaignDetailView, CampaignListCreateView, CampaignPrepareProviderView, CampaignPrepareView, CampaignRecipientListView, CampaignRestoreView


urlpatterns = [
    path("marketing/campaigns/", CampaignListCreateView.as_view(), name="marketing-campaign-list-create"),
    path("marketing/campaigns/<int:campaign_id>/", CampaignDetailView.as_view(), name="marketing-campaign-detail"),
    path("marketing/campaigns/<int:campaign_id>/archive/", CampaignArchiveView.as_view(), name="marketing-campaign-archive"),
    path("marketing/campaigns/<int:campaign_id>/restore/", CampaignRestoreView.as_view(), name="marketing-campaign-restore"),
    path("marketing/campaigns/<int:campaign_id>/prepare/", CampaignPrepareView.as_view(), name="marketing-campaign-prepare"),
    path("marketing/campaigns/<int:campaign_id>/prepare-provider/", CampaignPrepareProviderView.as_view(), name="marketing-campaign-prepare-provider"),
    path("marketing/campaigns/<int:campaign_id>/recipients/", CampaignRecipientListView.as_view(), name="marketing-campaign-recipients"),
]
