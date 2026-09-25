from rest_framework import serializers

from marketing_preferences.serializers import AudienceSelectionSerializer, PEOPLE_ORDERING_CHOICES
from .models import Campaign, CampaignPreparation, CampaignRecipientSnapshot


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
    class Meta:
        model = CampaignPreparation
        fields = ("id", "attempt_number", "status", "started_at", "completed_at", "selected_count", "included_count", "excluded_count", "brevo_list_id", "brevo_campaign_id", "brevo_editor_url", "provider_error_code", "provider_error_message")
        read_only_fields = fields


class CampaignSerializer(serializers.ModelSerializer):
    current_preparation = CampaignPreparationSerializer(read_only=True)

    class Meta:
        model = Campaign
        fields = ("id", "name", "status", "audience_selection", "audience_ordering", "audience_schema_version", "created_by", "created_at", "updated_at", "current_preparation")
        read_only_fields = fields


class CampaignRecipientSnapshotSerializer(serializers.ModelSerializer):
    class Meta:
        model = CampaignRecipientSnapshot
        fields = ("id", "person", "first_name_snapshot", "last_name_snapshot", "consent_state_snapshot", "decision", "exclusion_reason", "captured_at", "provider_outcome", "provider_error_code")
        read_only_fields = fields
