from rest_framework import serializers

from marketing_preferences.models import MarketingPreference


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
