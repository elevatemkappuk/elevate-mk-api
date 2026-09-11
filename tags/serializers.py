from rest_framework import serializers

from tags.models import Tag


class TagSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = Tag
        fields = ("id", "name", "slug")


class AssignTagSerializer(serializers.Serializer):
    tag = serializers.IntegerField(min_value=1)

    def validate(self, attrs):
        unknown_fields = set(self.initial_data.keys()) - set(self.fields.keys())
        if unknown_fields:
            raise serializers.ValidationError(
                {field: ["This field is not allowed."] for field in sorted(unknown_fields)}
            )
        return attrs


class EmptyRequestSerializer(serializers.Serializer):
    pass
