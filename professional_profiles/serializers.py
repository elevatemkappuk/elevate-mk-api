from rest_framework import serializers

from professional_profiles.models import Industry, ProfessionalProfile


class IndustryOptionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Industry
        fields = ("id", "name", "slug")


class ProfessionalProfileSerializer(serializers.ModelSerializer):
    industry = IndustryOptionSerializer(allow_null=True)

    class Meta:
        model = ProfessionalProfile
        fields = (
            "id",
            "job_title",
            "company",
            "industry",
            "career_stage",
            "linkedin_url",
            "created_at",
               "updated_at",
        )


class ProfessionalProfileWriteSerializer(serializers.Serializer):
    job_title = serializers.CharField(required=False, allow_blank=True, max_length=255)
    company = serializers.CharField(required=False, allow_blank=True, max_length=255)
    industry = serializers.PrimaryKeyRelatedField(
        queryset=Industry.objects.all(),
        required=False,
        allow_null=True,
    )
    career_stage = serializers.ChoiceField(
        choices=ProfessionalProfile.CareerStage.choices,
        required=False,
        allow_null=True,
        allow_blank=True,
    )
    linkedin_url = serializers.URLField(required=False, allow_blank=True)

    def validate(self, attrs):
        unknown_fields = set(self.initial_data.keys()) - set(self.fields.keys())
        if unknown_fields:
            raise serializers.ValidationError(
                {field: ["This field is not allowed."] for field in sorted(unknown_fields)}
            )

        if "industry" in attrs and attrs["industry"] is not None and not attrs["industry"].is_active:
            raise serializers.ValidationError(
                {"industry": ["Only active industries may be assigned."]}
            )

        return attrs
