from django.core.validators import URLValidator
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from people.models import Person
from community.normalization import normalize_community_name
from people.services import (
    is_plausible_crm_mobile,
    is_supported_phone_region,
    normalize_phone_for_community,
    PhoneNormalizationStatus,
)
from professional_profiles.models import Industry


class CommunityIndustrySerializer(serializers.ModelSerializer):
    label = serializers.CharField(source="name")

    class Meta:
        model = Industry
        fields = ("slug", "label")


class CommunityJoinSerializer(serializers.Serializer):
    first_name = serializers.CharField(max_length=150)
    last_name = serializers.CharField(max_length=150)
    gender = serializers.ChoiceField(choices=Person.Gender.choices)
    age_range = serializers.ChoiceField(choices=Person.AgeRange.choices)
    email = serializers.EmailField()
    email_marketing_opt_in = serializers.BooleanField(required=False, default=False)
    mobile = serializers.CharField(max_length=50, required=False, allow_blank=True)
    phone_region = serializers.CharField(max_length=2, required=False, allow_blank=True)
    location = serializers.CharField(max_length=255)
    industry = serializers.SlugField(max_length=255)
    job_title = serializers.CharField(max_length=255)
    linkedin_url = serializers.URLField(required=False, allow_blank=True)

    def validate_first_name(self, value):
        return normalize_community_name(value)

    def validate_last_name(self, value):
        return normalize_community_name(value)

    def validate_mobile(self, value):
        if not is_plausible_crm_mobile(value):
            raise serializers.ValidationError("Enter a valid mobile number.")
        return value

    def validate_phone_region(self, value):
        value = value.strip().upper()
        if value and not is_supported_phone_region(value):
            raise serializers.ValidationError("Enter a supported phone region.")
        return value

    def validate_industry(self, value):
        if not Industry.objects.filter(slug=value, is_active=True).exists():
            raise serializers.ValidationError("Select a valid industry.")
        return value

    def validate_linkedin_url(self, value):
        if value:
            try:
                URLValidator(schemes=["http", "https"])(value)
            except DjangoValidationError:
                raise serializers.ValidationError("LinkedIn URL must use http or https.")
        return value

    def validate(self, attrs):
        unknown_fields = set(self.initial_data.keys()) - set(self.fields.keys())
        if unknown_fields:
            raise serializers.ValidationError(
                {field: ["This field is not allowed."] for field in sorted(unknown_fields)}
            )
        mobile = attrs.get("mobile", "")
        phone_region = attrs.get("phone_region", "")
        if mobile and not phone_region:
            raise serializers.ValidationError({"phone_region": ["This field is required when mobile is supplied."]})
        if phone_region and not mobile:
            raise serializers.ValidationError({"phone_region": ["This field requires a mobile number."]})
        if mobile:
            normalized = normalize_phone_for_community(mobile, region=phone_region)
            if normalized.status != PhoneNormalizationStatus.NORMALIZED:
                raise serializers.ValidationError({"mobile": ["Enter a valid mobile number for the selected phone region."]})
            attrs["mobile"] = normalized.e164
        return attrs
