from django.core.validators import URLValidator
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from people.models import Person
from community.models import CommunityContentReport, CommunityPost
from community.normalization import normalize_community_name
from people.services import (
    is_plausible_crm_mobile,
    is_supported_phone_region,
    normalize_phone_for_community,
    PhoneNormalizationStatus,
)
from professional_profiles.models import Industry, ProfessionalProfile
from skills.models import Skill
from interests.models import Interest


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


class CommunityActivationSerializer(serializers.Serializer):
    password = serializers.CharField(write_only=True, trim_whitespace=False)
    confirm_password = serializers.CharField(write_only=True, trim_whitespace=False)

    def validate(self, attrs):
        if attrs["password"] != attrs["confirm_password"]:
            raise serializers.ValidationError({"confirm_password": ["The passwords do not match."]})
        return attrs


class CommunityCurrentUserSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    first_name = serializers.CharField()
    last_name = serializers.CharField()


class CommunityAccountMobileSerializer(serializers.Serializer):
    present = serializers.BooleanField()
    masked = serializers.CharField(allow_null=True)


class CommunityAccountMarketingPreferenceSerializer(serializers.Serializer):
    email_marketing = serializers.BooleanField(required=True)

    def validate(self, attrs):
        unknown_fields = set(self.initial_data.keys()) - set(self.fields.keys())
        if unknown_fields:
            raise serializers.ValidationError({field: ["This field is not allowed."] for field in sorted(unknown_fields)})
        if not isinstance(self.initial_data.get("email_marketing"), bool):
            raise serializers.ValidationError({"email_marketing": ["This field must be a boolean."]})
        return attrs


class CommunityAccountMarketingSerializer(serializers.Serializer):
    state = serializers.ChoiceField(choices=("UNKNOWN", "OPTED_IN", "OPTED_OUT"))


class CommunityAccountPasswordSerializer(serializers.Serializer):
    configured = serializers.BooleanField()


class CommunityPasswordChangeSerializer(serializers.Serializer):
    current_password = serializers.CharField(write_only=True, trim_whitespace=False)
    new_password = serializers.CharField(write_only=True, trim_whitespace=False)
    confirm_password = serializers.CharField(write_only=True, trim_whitespace=False)

    def validate(self, attrs):
        unknown_fields = set(self.initial_data.keys()) - set(self.fields.keys())
        if unknown_fields:
            raise serializers.ValidationError(
                {field: ["This field is not allowed."] for field in sorted(unknown_fields)}
            )
        if attrs["new_password"] != attrs["confirm_password"]:
            raise serializers.ValidationError({"confirm_password": ["The passwords do not match."]})
        return attrs


class CommunityEmailChangeRequestSerializer(serializers.Serializer):
    new_email = serializers.EmailField(write_only=True)
    current_password = serializers.CharField(write_only=True, trim_whitespace=False)

    def to_internal_value(self, data):
        data = dict(data)
        if isinstance(data.get("new_email"), str):
            data["new_email"] = data["new_email"].strip()
        return super().to_internal_value(data)

    def validate(self, attrs):
        unknown_fields = set(self.initial_data.keys()) - set(self.fields.keys())
        if unknown_fields:
            raise serializers.ValidationError({field: ["This field is not allowed."] for field in sorted(unknown_fields)})
        attrs["new_email"] = attrs["new_email"].strip().lower()
        return attrs


class CommunityEmailChangeVerificationSerializer(serializers.Serializer):
    request_id = serializers.UUIDField(write_only=True)
    token = serializers.CharField(write_only=True, trim_whitespace=False, allow_blank=False)

    def validate(self, attrs):
        unknown_fields = set(self.initial_data.keys()) - set(self.fields.keys())
        if unknown_fields:
            raise serializers.ValidationError({field: ["This field is not allowed."] for field in sorted(unknown_fields)})
        return attrs


class CommunityMobileUpdateSerializer(serializers.Serializer):
    mobile = serializers.CharField(max_length=50, allow_blank=True)
    phone_region = serializers.CharField(max_length=2, required=False, allow_blank=True)

    def validate_phone_region(self, value):
        value = value.strip().upper()
        if value and not is_supported_phone_region(value):
            raise serializers.ValidationError("Enter a supported phone region.")
        return value

    def validate(self, attrs):
        mobile = attrs.get("mobile", "").strip()
        phone_region = attrs.get("phone_region", "")
        attrs["mobile"] = mobile
        if not mobile:
            if phone_region:
                raise serializers.ValidationError({"phone_region": ["This field must be empty when removing a mobile number."]})
            attrs["mobile"] = ""
            return attrs
        if not phone_region:
            raise serializers.ValidationError({"phone_region": ["This field is required when mobile is supplied."]})
        normalized = normalize_phone_for_community(mobile, region=phone_region)
        if normalized.status != PhoneNormalizationStatus.NORMALIZED:
            raise serializers.ValidationError({"mobile": ["Enter a valid mobile number for the selected phone region."]})
        attrs["mobile"] = normalized.e164
        return attrs


class CommunityAccountSerializer(serializers.Serializer):
    email = serializers.CharField(allow_blank=True)
    mobile = CommunityAccountMobileSerializer()
    email_marketing = CommunityAccountMarketingSerializer()
    password = CommunityAccountPasswordSerializer()


class CommunityProfileIndustrySerializer(serializers.Serializer):
    id = serializers.IntegerField()
    slug = serializers.CharField()
    label = serializers.CharField()


class CommunityProfilePersonSerializer(serializers.Serializer):
    first_name = serializers.CharField()
    last_name = serializers.CharField()
    location = serializers.CharField()


class CommunityProfileCommunitySerializer(serializers.Serializer):
    bio = serializers.CharField()
    review_required = serializers.BooleanField()
    photo_url = serializers.URLField(allow_null=True)
    directory_id = serializers.UUIDField()
    directory_visible = serializers.BooleanField()
    email_visible = serializers.BooleanField()
    mobile_visible = serializers.BooleanField()


class CommunityProfileProfessionalSerializer(serializers.Serializer):
    job_title = serializers.CharField()
    company = serializers.CharField()
    industry = CommunityProfileIndustrySerializer(allow_null=True)
    career_stage = serializers.CharField(allow_blank=True, allow_null=True)
    linkedin_url = serializers.URLField(allow_blank=True)


class CommunityProfileMembershipSerializer(serializers.Serializer):
    status = serializers.CharField()
    joined_at = serializers.DateField()


class CommunityProfileCompletionSerializer(serializers.Serializer):
    name = serializers.BooleanField()
    professional_details = serializers.BooleanField()
    bio = serializers.BooleanField()
    skills = serializers.BooleanField()
    interests = serializers.BooleanField()


class CommunityProfileSerializer(serializers.Serializer):
    person = CommunityProfilePersonSerializer()
    community = CommunityProfileCommunitySerializer()
    professional = CommunityProfileProfessionalSerializer()
    skills = serializers.ListField(child=serializers.DictField())
    interests = serializers.ListField(child=serializers.DictField())
    membership = CommunityProfileMembershipSerializer()
    completion = CommunityProfileCompletionSerializer()


class CommunityWriteSerializer(serializers.Serializer):
    def to_internal_value(self, data):
        unknown_fields = set(data.keys()) - set(self.fields.keys())
        if unknown_fields:
            raise serializers.ValidationError({field: ["This field is not allowed."] for field in sorted(unknown_fields)})
        return super().to_internal_value(data)


class CommunityProfilePersonWriteSerializer(CommunityWriteSerializer):
    first_name = serializers.CharField(required=False, max_length=150, allow_blank=False)
    last_name = serializers.CharField(required=False, max_length=150, allow_blank=False)
    location = serializers.CharField(required=False, max_length=255, allow_blank=True)

    def validate(self, attrs):
        return attrs


class CommunityProfileCommunityWriteSerializer(CommunityWriteSerializer):
    bio = serializers.CharField(required=False, allow_blank=True, max_length=400, trim_whitespace=True)
    directory_visible = serializers.BooleanField(required=False)
    email_visible = serializers.BooleanField(required=False)
    mobile_visible = serializers.BooleanField(required=False)

    def validate(self, attrs):
        return attrs


class CommunityProfileProfessionalWriteSerializer(CommunityWriteSerializer):
    job_title = serializers.CharField(required=False, max_length=255, allow_blank=True)
    company = serializers.CharField(required=False, max_length=255, allow_blank=True)
    industry = serializers.SlugRelatedField(
        slug_field="slug",
        queryset=Industry.objects.filter(is_active=True),
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

    def validate_linkedin_url(self, value):
        if value:
            try:
                URLValidator(schemes=["http", "https"])(value)
            except DjangoValidationError:
                raise serializers.ValidationError("LinkedIn URL must use http or https.")
        return value.strip()

    def validate(self, attrs):
        return attrs


class CommunityProfileWriteSerializer(CommunityWriteSerializer):
    person = CommunityProfilePersonWriteSerializer(required=False)
    community = CommunityProfileCommunityWriteSerializer(required=False)
    professional = CommunityProfileProfessionalWriteSerializer(required=False)
    skills = serializers.ListField(
        child=serializers.SlugRelatedField(slug_field="slug", queryset=Skill.objects.filter(is_active=True)),
        required=False,
    )
    interests = serializers.ListField(
        child=serializers.SlugRelatedField(slug_field="slug", queryset=Interest.objects.filter(is_active=True)),
        required=False,
    )

    def validate(self, attrs):
        for field in ("skills", "interests"):
            if field in attrs and len({item.slug for item in attrs[field]}) != len(attrs[field]):
                raise serializers.ValidationError({field: ["Duplicate selections are not allowed."]})
        return attrs


class CommunityProfileOptionSerializer(serializers.Serializer):
    slug = serializers.CharField()
    label = serializers.CharField()


class CommunityProfileOptionsSerializer(serializers.Serializer):
    industries = CommunityProfileOptionSerializer(many=True)
    career_stages = CommunityProfileOptionSerializer(many=True)
    skills = CommunityProfileOptionSerializer(many=True)
    interests = CommunityProfileOptionSerializer(many=True)


class CommunityDirectoryIndustrySerializer(serializers.Serializer):
    slug = serializers.CharField()
    label = serializers.CharField()


class CommunityDirectoryProfessionalSerializer(serializers.Serializer):
    job_title = serializers.CharField()
    company = serializers.CharField()
    industry = CommunityDirectoryIndustrySerializer(allow_null=True)
    career_stage = serializers.CharField(allow_blank=True, allow_null=True)
    linkedin_url = serializers.URLField(allow_blank=True)


class CommunityDirectoryListProfessionalSerializer(serializers.Serializer):
    job_title = serializers.CharField()
    company = serializers.CharField()
    industry = CommunityDirectoryIndustrySerializer(allow_null=True)


class CommunityDirectoryTaxonomySerializer(serializers.Serializer):
    slug = serializers.CharField()
    label = serializers.CharField()


class CommunityConnectionRelationshipSerializer(serializers.Serializer):
    state = serializers.ChoiceField(
        choices=(
            "NO_RELATIONSHIP",
            "OUTGOING_PENDING",
            "INCOMING_PENDING",
            "CONNECTED",
        )
    )
    connection_id = serializers.UUIDField(allow_null=True)
    can_connect = serializers.BooleanField()
    can_accept = serializers.BooleanField()
    can_decline = serializers.BooleanField()
    can_remove = serializers.BooleanField()


class CommunityDirectoryListSerializer(serializers.Serializer):
    directory_id = serializers.UUIDField()
    photo_url = serializers.URLField(allow_null=True)
    first_name = serializers.CharField()
    last_name = serializers.CharField()
    location = serializers.CharField()
    professional = CommunityDirectoryListProfessionalSerializer()
    skills = CommunityDirectoryTaxonomySerializer(many=True)
    interests = CommunityDirectoryTaxonomySerializer(many=True)
    relationship = CommunityConnectionRelationshipSerializer(required=False)


class CommunityDirectoryContactSerializer(serializers.Serializer):
    email = serializers.EmailField(allow_null=True)
    mobile = serializers.CharField(allow_null=True, allow_blank=True)


class CommunityDirectoryDetailSerializer(CommunityDirectoryListSerializer):
    professional = CommunityDirectoryProfessionalSerializer()
    bio = serializers.CharField()
    contact = CommunityDirectoryContactSerializer()
    relationship = CommunityConnectionRelationshipSerializer()


class CommunityConnectionMemberSerializer(CommunityDirectoryListSerializer):
    pass


class CommunityConnectionSerializer(serializers.Serializer):
    connection_id = serializers.UUIDField()
    member = CommunityConnectionMemberSerializer()


class CommunityConnectionRequestSerializer(serializers.Serializer):
    connection_id = serializers.UUIDField()
    state = serializers.ChoiceField(choices=("OUTGOING_PENDING", "INCOMING_PENDING"))
    requested_at = serializers.DateTimeField()
    member = CommunityConnectionMemberSerializer()


class CommunityConnectionRequestCreateSerializer(serializers.Serializer):
    directory_id = serializers.UUIDField()


class CommunityConnectionRequestQuerySerializer(serializers.Serializer):
    direction = serializers.ChoiceField(choices=("incoming", "outgoing"))


class CommunityDirectoryQuerySerializer(serializers.Serializer):
    q = serializers.CharField(required=False, allow_blank=True, max_length=100, trim_whitespace=True)
    page_size = serializers.IntegerField(required=False, min_value=1, max_value=100)
    industry = serializers.SlugField(required=False)
    skill = serializers.SlugField(required=False)
    interest = serializers.SlugField(required=False)

    def _validate_active_slug(self, value, model, field):
        if value and not model.objects.filter(slug=value, is_active=True).exists():
            raise serializers.ValidationError("Select a valid active taxonomy value.")
        return value

    def validate_industry(self, value):
        return self._validate_active_slug(value, Industry, "industry")

    def validate_skill(self, value):
        return self._validate_active_slug(value, Skill, "skill")

    def validate_interest(self, value):
        return self._validate_active_slug(value, Interest, "interest")


class CommunityPostQuerySerializer(serializers.Serializer):
    purpose = serializers.ChoiceField(choices=CommunityPost.Purpose.choices, required=False)
    page_size = serializers.IntegerField(required=False, min_value=1, max_value=100)


class CommunityPostCreateSerializer(serializers.Serializer):
    purpose = serializers.ChoiceField(choices=CommunityPost.Purpose.choices)
    headline = serializers.CharField(max_length=120, allow_blank=False, trim_whitespace=True)
    body = serializers.CharField(max_length=2000, allow_blank=False, trim_whitespace=True)
    audience = serializers.ChoiceField(choices=CommunityPost.Audience.choices)

    def validate(self, attrs):
        unknown_fields = set(self.initial_data.keys()) - set(self.fields.keys())
        if unknown_fields:
            raise serializers.ValidationError(
                {field: ["This field is not allowed."] for field in sorted(unknown_fields)}
            )
        attrs["headline"] = attrs["headline"].strip()
        attrs["body"] = attrs["body"].strip()
        return attrs


class CommunityPostAuthorProfessionalSerializer(serializers.Serializer):
    job_title = serializers.CharField()
    industry = CommunityDirectoryIndustrySerializer(allow_null=True)


class CommunityPostAuthorSerializer(serializers.Serializer):
    directory_id = serializers.UUIDField(allow_null=True)
    first_name = serializers.CharField()
    last_name = serializers.CharField()
    photo_url = serializers.URLField(allow_null=True)
    professional = CommunityPostAuthorProfessionalSerializer()
    location = serializers.CharField()


class CommunityPostSerializer(serializers.Serializer):
    public_id = serializers.UUIDField()
    purpose = serializers.ChoiceField(choices=CommunityPost.Purpose.choices)
    headline = serializers.CharField()
    body = serializers.CharField()
    audience = serializers.ChoiceField(choices=CommunityPost.Audience.choices)
    author = CommunityPostAuthorSerializer()
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()
    edited_at = serializers.DateTimeField(allow_null=True)
    reply_count = serializers.IntegerField()
    is_own_post = serializers.BooleanField()


class CommunityReplyCreateSerializer(serializers.Serializer):
    body = serializers.CharField(max_length=1000, allow_blank=False, trim_whitespace=True)
    reply_to_id = serializers.UUIDField(required=False, allow_null=True, default=None)

    def validate(self, attrs):
        unknown_fields = set(self.initial_data.keys()) - set(self.fields.keys())
        if unknown_fields:
            raise serializers.ValidationError(
                {field: ["This field is not allowed."] for field in sorted(unknown_fields)}
            )
        attrs["body"] = attrs["body"].strip()
        return attrs


class CommunityReplyUpdateSerializer(serializers.Serializer):
    body = serializers.CharField(max_length=1000, allow_blank=False, trim_whitespace=True)

    def validate(self, attrs):
        unknown_fields = set(self.initial_data.keys()) - set(self.fields.keys())
        if unknown_fields:
            raise serializers.ValidationError(
                {field: ["This field is not allowed."] for field in sorted(unknown_fields)}
            )
        attrs["body"] = attrs["body"].strip()
        return attrs


class CommunityReplyingToAuthorSerializer(serializers.Serializer):
    directory_id = serializers.UUIDField(allow_null=True)
    first_name = serializers.CharField()
    last_name = serializers.CharField()


class CommunityReplyingToSerializer(serializers.Serializer):
    reply_id = serializers.UUIDField()
    author = CommunityReplyingToAuthorSerializer(allow_null=True)


class CommunityReplySerializer(serializers.Serializer):
    public_id = serializers.UUIDField()
    body = serializers.CharField()
    author = CommunityPostAuthorSerializer(allow_null=True)
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()
    edited_at = serializers.DateTimeField(allow_null=True)
    is_own_reply = serializers.BooleanField()
    replying_to = CommunityReplyingToSerializer(allow_null=True)


class CommunityContentReportCreateSerializer(serializers.Serializer):
    reason = serializers.ChoiceField(choices=CommunityContentReport.Reason.choices)
    details = serializers.CharField(required=False, allow_blank=True, max_length=1000, trim_whitespace=True)

    def validate(self, attrs):
        unknown = set(self.initial_data) - set(self.fields)
        if unknown:
            raise serializers.ValidationError({field: ["This field is not allowed."] for field in sorted(unknown)})
        attrs["details"] = attrs.get("details", "").strip()
        return attrs


class CommunityContentReportAcknowledgementSerializer(serializers.Serializer):
    report_id = serializers.UUIDField()
    status = serializers.ChoiceField(choices=CommunityContentReport.Status.choices)


class CommunityModerationActionSerializer(serializers.Serializer):
    resolution = serializers.CharField(required=False, allow_blank=True, max_length=1000, trim_whitespace=True)


class CommunityModerationIdentitySerializer(serializers.Serializer):
    directory_id = serializers.UUIDField(allow_null=True)
    first_name = serializers.CharField()
    last_name = serializers.CharField()
    location = serializers.CharField()
    job_title = serializers.CharField()


class CommunityModerationTargetSerializer(serializers.Serializer):
    type = serializers.ChoiceField(choices=("POST", "REPLY"))
    public_id = serializers.UUIDField()
    status = serializers.CharField()
    headline = serializers.CharField(allow_blank=True)
    body = serializers.CharField()
    author = CommunityModerationIdentitySerializer()
    parent_post = serializers.DictField(allow_null=True)


class CommunityModerationReportSerializer(serializers.Serializer):
    report_id = serializers.UUIDField()
    reason = serializers.ChoiceField(choices=CommunityContentReport.Reason.choices)
    details = serializers.CharField()
    status = serializers.ChoiceField(choices=CommunityContentReport.Status.choices)
    created_at = serializers.DateTimeField()
    resolved_at = serializers.DateTimeField(allow_null=True)
    reporter = CommunityModerationIdentitySerializer()
    target = CommunityModerationTargetSerializer()
