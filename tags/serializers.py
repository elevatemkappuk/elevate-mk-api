from rest_framework import serializers

from tags.models import Tag


class TagSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = Tag
        fields = ("id", "name", "slug")


class AssignTagSerializer(serializers.Serializer):
    tag = serializers.IntegerField(min_value=1)


class EmptyRequestSerializer(serializers.Serializer):
    pass
