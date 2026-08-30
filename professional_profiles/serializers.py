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

