from rest_framework import serializers

from interests.models import Interest


class InterestSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = Interest
        fields = ("id", "name", "slug")


class AssignInterestInputSerializer(serializers.Serializer):
    interest = serializers.PrimaryKeyRelatedField(
        queryset=Interest.objects.all(),
    )

    def validate(self, attrs):
        unknown_fields = set(self.initial_data.keys()) - set(self.fields.keys())
        if unknown_fields:
            raise serializers.ValidationError(
                {field: ["This field is not allowed."] for field in sorted(unknown_fields)}
            )

        return attrs
