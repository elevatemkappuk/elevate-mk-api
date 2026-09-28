from django.db import IntegrityError
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import generics, status
from rest_framework.exceptions import APIException, NotFound, ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import Campaign, CampaignRecipientSnapshot
from .permissions import HasCampaignAccess, HasCampaignWriteAccess
from .serializers import CampaignCreateSerializer, CampaignRecipientSnapshotSerializer, CampaignSerializer
from .services import (
    CampaignLifecycleConflict,
    archive_campaign,
    delete_unused_campaign,
    prepare_campaign_provider,
    prepare_campaign_snapshot,
    restore_campaign,
)
from audit.models import AuditEvent
from audit.services import record_audit_event


class CampaignPreparationConflict(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_code = "campaign_preparation_conflict"
    default_detail = "The campaign cannot be prepared in its current state."


class CampaignPagination(PageNumberPagination):
    page_size = 25
    page_size_query_param = "page_size"
    max_page_size = 100


class CampaignListCreateView(generics.ListCreateAPIView):
    queryset = Campaign.objects.select_related("created_by", "current_preparation").all()
    pagination_class = CampaignPagination

    def get_permissions(self):
        return [IsAuthenticated(), HasCampaignWriteAccess() if self.request.method == "POST" else HasCampaignAccess()]

    def get_serializer_class(self):
        return CampaignCreateSerializer if self.request.method == "POST" else CampaignSerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        lifecycle = self.request.query_params.get("lifecycle", "active")
        if lifecycle == "active":
            return queryset.filter(archived_at__isnull=True)
        if lifecycle == "archived":
            return queryset.filter(archived_at__isnull=False)
        if lifecycle == "all":
            return queryset
        raise ValidationError({"lifecycle": "Use active, archived, or all."})

    @extend_schema(request=CampaignCreateSerializer, responses={201: CampaignSerializer}, tags=["Marketing Campaigns"])
    def create(self, request, *args, **kwargs):
        input_serializer = self.get_serializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        campaign = Campaign.objects.create(
            name=input_serializer.validated_data["name"],
            audience_selection=input_serializer.validated_data["audience_selection"],
            audience_ordering=input_serializer.validated_data["audience_ordering"],
            created_by=request.user,
        )
        record_audit_event(
            action=AuditEvent.Action.CAMPAIGN_CREATED,
            entity_type="Campaign",
            entity_id=campaign.id,
            actor_user=request.user,
            metadata={"campaign_id": campaign.id, "status": campaign.status},
        )
        return Response(CampaignSerializer(campaign, context={"request": request}).data, status=status.HTTP_201_CREATED)


class CampaignDetailView(generics.RetrieveAPIView):
    queryset = Campaign.objects.select_related("created_by", "current_preparation").all()
    serializer_class = CampaignSerializer
    lookup_url_kwarg = "campaign_id"

    def get_permissions(self):
        return [IsAuthenticated(), (HasCampaignWriteAccess if self.request.method == "DELETE" else HasCampaignAccess)()]

    def delete(self, request, *args, **kwargs):
        try:
            delete_unused_campaign(campaign_id=kwargs["campaign_id"], actor_user=request.user)
        except Campaign.DoesNotExist:
            raise NotFound("Campaign not found.")
        except CampaignLifecycleConflict as error:
            raise CampaignPreparationConflict(str(error))
        return Response(status=status.HTTP_204_NO_CONTENT)


class CampaignArchiveView(generics.GenericAPIView):
    queryset = Campaign.objects.all()
    permission_classes = [IsAuthenticated, HasCampaignWriteAccess]
    lookup_url_kwarg = "campaign_id"

    def post(self, request, *args, **kwargs):
        try:
            campaign = archive_campaign(campaign_id=kwargs["campaign_id"], actor_user=request.user)
        except Campaign.DoesNotExist:
            raise NotFound("Campaign not found.")
        return Response(CampaignSerializer(campaign, context={"request": request}).data)


class CampaignRestoreView(generics.GenericAPIView):
    queryset = Campaign.objects.all()
    permission_classes = [IsAuthenticated, HasCampaignWriteAccess]
    lookup_url_kwarg = "campaign_id"

    def post(self, request, *args, **kwargs):
        try:
            campaign = restore_campaign(campaign_id=kwargs["campaign_id"], actor_user=request.user)
        except Campaign.DoesNotExist:
            raise NotFound("Campaign not found.")
        return Response(CampaignSerializer(campaign, context={"request": request}).data)


class CampaignPrepareView(generics.GenericAPIView):
    queryset = Campaign.objects.all()
    permission_classes = [IsAuthenticated, HasCampaignWriteAccess]
    lookup_url_kwarg = "campaign_id"

    @extend_schema(responses={200: CampaignSerializer, 409: OpenApiResponse(description="Campaign preparation conflict.")}, tags=["Marketing Campaigns"])
    def post(self, request, *args, **kwargs):
        try:
            campaign = prepare_campaign_snapshot(campaign_id=kwargs["campaign_id"], actor_user=request.user)
        except Campaign.DoesNotExist:
            raise NotFound("Campaign not found.")
        except (CampaignLifecycleConflict, RuntimeError, IntegrityError) as error:
            raise CampaignPreparationConflict(str(error))
        return Response(CampaignSerializer(campaign, context={"request": request}).data)


class CampaignPrepareProviderView(generics.GenericAPIView):
    queryset = Campaign.objects.all()
    permission_classes = [IsAuthenticated, HasCampaignWriteAccess]
    lookup_url_kwarg = "campaign_id"

    @extend_schema(responses={200: CampaignSerializer, 404: OpenApiResponse(description="Campaign not found."), 409: OpenApiResponse(description="Provider preparation conflict.")}, tags=["Marketing Campaigns"])
    def post(self, request, *args, **kwargs):
        try:
            campaign = prepare_campaign_provider(campaign_id=kwargs["campaign_id"], actor_user=request.user)
        except Campaign.DoesNotExist:
            raise NotFound("Campaign not found.")
        except (CampaignLifecycleConflict, RuntimeError, IntegrityError) as error:
            raise CampaignPreparationConflict(str(error))
        return Response(CampaignSerializer(campaign, context={"request": request}).data)


class CampaignRecipientListView(generics.ListAPIView):
    serializer_class = CampaignRecipientSnapshotSerializer
    permission_classes = [IsAuthenticated, HasCampaignAccess]
    pagination_class = CampaignPagination

    def get_queryset(self):
        if not Campaign.objects.filter(pk=self.kwargs["campaign_id"]).exists():
            raise NotFound("Campaign not found.")
        preparation_id = Campaign.objects.filter(pk=self.kwargs["campaign_id"]).values_list("current_preparation_id", flat=True).first()
        return CampaignRecipientSnapshot.objects.filter(preparation_id=preparation_id).order_by("id") if preparation_id else CampaignRecipientSnapshot.objects.none()
