from rest_framework import serializers

from skills.models import Skill


class SkillSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = Skill
        fields = ("id", "name", "slug")


class AssignSkillInputSerializer(serializers.Serializer):
    skill = serializers.PrimaryKeyRelatedField(
        queryset=Skill.objects.all(),
    )

    def validate(self, attrs):
        unknown_fields = set(self.initial_data.keys()) - set(self.fields.keys())
        if unknown_fields:
            raise serializers.ValidationError(
                {field: ["This field is not allowed."] for field in sorted(unknown_fields)}
            )

        return attrs

    def validate_skill(self, value):
        if not value.is_active:
            raise serializers.ValidationError("Only active skills may be assigned.")
        return value
