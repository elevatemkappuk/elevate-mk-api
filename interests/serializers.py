from rest_framework import serializers

from interests.models import Interest


class InterestSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = Interest
        fields = ("id", "name", "slug")

