from rest_framework import serializers

from marketing_preferences.models import MarketingPreference
from professional_profiles.models import ProfessionalProfile


PEOPLE_ORDERING_CHOICES = (
    ("name", "Name ascending"),
    ("-name", "Name descending"),
    ("first_name", "First name ascending"),
    ("-first_name", "First name descending"),
    ("last_name", "Last name ascending"),
    ("-last_name", "Last name descending"),
    ("created_at", "Created at ascending"),
    ("-created_at", "Created at descending"),
    ("updated_at", "Updated at ascending"),
    ("-updated_at", "Updated at descending"),
    ("membership_joined_at", "Membership joined date ascending"),
    ("-membership_joined_at", "Membership joined date descending"),
)


class AudienceSelectionSerializer(serializers.Serializer):
    q = serializers.CharField(required=False, allow_blank=True, default="")
    relationship = serializers.ListField(
        child=serializers.ChoiceField(choices=(
            ("CONTACT", "Contact"),
            ("ACTIVE_MEMBER", "Active member"),
            ("FORMER_MEMBER", "Former member"),
        )),
        required=False,
        default=list,
    )
    location = serializers.ListField(
        child=serializers.CharField(allow_blank=False, trim_whitespace=True),
        required=False,
        default=list,
    )
    industry = serializers.ListField(child=serializers.IntegerField(min_value=1), required=False, default=list)
    career_stage = serializers.ListField(
        child=serializers.ChoiceField(choices=ProfessionalProfile.CareerStage.choices),
        required=False,
        default=list,
    )
    interest = serializers.ListField(child=serializers.IntegerField(min_value=1), required=False, default=list)
    skill = serializers.ListField(child=serializers.IntegerField(min_value=1), required=False, default=list)
    tag = serializers.ListField(child=serializers.IntegerField(min_value=1), required=False, default=list)
    record_state = serializers.ChoiceField(
        choices=(("active", "Active BUSINESS people"),),
        required=False,
        default="active",
    )

    def to_internal_value(self, data):
        if isinstance(data, dict):
            unknown_keys = sorted(set(data) - set(self.fields))
            if unknown_keys:
                raise serializers.ValidationError({
                    key: "This selection criterion is not supported by audience preview."
                    for key in unknown_keys
                })
        return super().to_internal_value(data)


class AudiencePreviewRequestSerializer(serializers.Serializer):
    RESULT_CHOICES = ("all", "eligible", "excluded")
    selection = AudienceSelectionSerializer(required=False, default=dict)
    result = serializers.ChoiceField(choices=RESULT_CHOICES, default="all")
    ordering = serializers.ChoiceField(
        choices=PEOPLE_ORDERING_CHOICES,
        default="last_name",
    )
    page = serializers.IntegerField(required=False, min_value=1, default=1)
    page_size = serializers.IntegerField(required=False, min_value=1, default=25)

    def validate_page_size(self, value):
        if value not in {25, 50, 100}:
            raise serializers.ValidationError("page_size must be one of 25, 50, or 100.")
        return value

    def validate(self, attrs):
        validated = attrs.get("selection") or {}
        attrs["selection"] = {
            key: validated.get(key, [] if key != "q" else "")
            for key in ("q", "relationship", "location", "industry", "career_stage", "interest", "skill", "tag")
        }
        return attrs


class AudiencePreviewPersonSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    first_name = serializers.CharField()
    last_name = serializers.CharField()
    primary_email = serializers.SerializerMethodField()
    classification = serializers.CharField(source="audience_classification")
    exclusion_reasons = serializers.SerializerMethodField()

    def get_primary_email(self, instance):
        return instance.primary_email or None

    def get_exclusion_reasons(self, instance):
        classification = instance.audience_classification
        return [] if classification == "ELIGIBLE" else [classification]


class MarketingPreferenceSerializer(serializers.Serializer):
    channel = serializers.CharField()
    state = serializers.CharField()
    source = serializers.CharField(allow_null=True)
    recorded_at = serializers.DateTimeField(allow_null=True)
    recorded_by_id = serializers.IntegerField(allow_null=True)


class MarketingPreferenceMutationSerializer(serializers.Serializer):
    state = serializers.ChoiceField(
        choices=(
            (MarketingPreference.State.OPTED_IN, "Opted in"),
            (MarketingPreference.State.OPTED_OUT, "Opted out"),
        )
    )


class MarketingPreferenceWriteResponseSerializer(serializers.Serializer):
    preference = MarketingPreferenceSerializer()
    changed = serializers.BooleanField()
