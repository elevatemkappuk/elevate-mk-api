from rest_framework import serializers

from skills.models import Skill


class SkillSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = Skill
        fields = ("id", "name", "slug")

