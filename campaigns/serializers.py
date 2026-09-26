from rest_framework import serializers

from marketing_preferences.serializers import AudienceSelectionSerializer, PEOPLE_ORDERING_CHOICES
from staff_access.models import StaffRole
from staff_access.permissions import user_has_any_active_staff_role
from .models import Campaign, CampaignPreparation, CampaignRecipientSnapshot
from .services import campaign_delete_block_reason


class CampaignCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255, trim_whitespace=True)
    audience_selection = AudienceSelectionSerializer(required=False, default=dict)
    audience_ordering = serializers.ChoiceField(choices=PEOPLE_ORDERING_CHOICES, default="last_name")

    def to_internal_value(self, data):
        if isinstance(data, dict):
            unknown_keys = sorted(set(data) - set(self.fields))
            if unknown_keys:
                raise serializers.ValidationError({key: "This field is not supported." for key in unknown_keys})
        return super().to_internal_value(data)

    def validate(self, attrs):
        selection = attrs.get("audience_selection") or {}
        attrs["audience_selection"] = {
            key: selection.get(key, [] if key != "q" else "")
            for key in ("q", "relationship", "location", "industry", "career_stage", "interest", "skill", "tag")
        }
        return attrs


class CampaignPreparationSerializer(serializers.ModelSerializer):
    provider_ready_count = serializers.SerializerMethodField()
    provider_issue_count = serializers.SerializerMethodField()
    can_start_provider_preparation = serializers.SerializerMethodField()
    can_retry_provider_preparation = serializers.SerializerMethodField()

    class Meta:
        model = CampaignPreparation
        fields = ("id", "attempt_number", "status", "started_at", "completed_at", "selected_count", "included_count", "excluded_count", "provider_ready_count", "provider_issue_count", "can_start_provider_preparation", "can_retry_provider_preparation", "brevo_list_id", "brevo_campaign_id", "brevo_editor_url", "provider_error_code", "provider_error_message")
        read_only_fields = fields

    def get_provider_ready_count(self, obj):
        return obj.recipient_snapshots.filter(provider_outcome="ADDED_TO_CAMPAIGN_LIST").count()

    def get_provider_issue_count(self, obj):
        return obj.recipient_snapshots.filter(provider_outcome__in=("RECONCILIATION_REQUIRED", "PROVIDER_FAILED")).count()

    def get_can_start_provider_preparation(self, obj):
        return obj.status == CampaignPreparation.Status.SNAPSHOT_READY and not obj.campaign.is_archived

    def get_can_retry_provider_preparation(self, obj):
        return not obj.campaign.is_archived and obj.status in (
            CampaignPreparation.Status.PROVIDER_FAILED,
            CampaignPreparation.Status.RECONCILIATION_REQUIRED,
        )


class CampaignSerializer(serializers.ModelSerializer):
    current_preparation = CampaignPreparationSerializer(read_only=True)
    is_archived = serializers.BooleanField(read_only=True)
    can_archive = serializers.SerializerMethodField()
    can_restore = serializers.SerializerMethodField()
    can_delete = serializers.SerializerMethodField()

    class Meta:
        model = Campaign
        fields = ("id", "name", "status", "audience_selection", "audience_ordering", "audience_schema_version", "created_by", "created_at", "updated_at", "archived_at", "archived_by", "is_archived", "can_archive", "can_restore", "can_delete", "current_preparation")
        read_only_fields = fields

    def _can_write(self):
        request = self.context.get("request")
        return bool(request and user_has_any_active_staff_role(request.user, (StaffRole.CRM_ADMIN, StaffRole.CRM_MANAGER)))

    def get_can_archive(self, obj):
        return self._can_write() and not obj.is_archived

    def get_can_restore(self, obj):
        return self._can_write() and obj.is_archived

    def get_can_delete(self, obj):
        return self._can_write() and campaign_delete_block_reason(obj) is None


class CampaignRecipientSnapshotSerializer(serializers.ModelSerializer):
    class Meta:
        model = CampaignRecipientSnapshot
        fields = ("id", "person", "first_name_snapshot", "last_name_snapshot", "consent_state_snapshot", "decision", "exclusion_reason", "captured_at", "provider_outcome", "provider_error_code")
        read_only_fields = fields
