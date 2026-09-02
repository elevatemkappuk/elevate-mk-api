from interests.models import Interest
from interests.serializers import InterestSummarySerializer
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from memberships.models import Membership
from memberships.serializers import MembershipSerializer
from people.models import Person
from professional_profiles.models import ProfessionalProfile
from professional_profiles.serializers import ProfessionalProfileSerializer
from skills.models import Skill
from skills.serializers import SkillSummarySerializer
from tags.models import Tag
from tags.serializers import TagSummarySerializer


class PersonListQuerySerializer(serializers.Serializer):
    RECORD_STATE_ACTIVE = "active"
    RECORD_STATE_ARCHIVED = "archived"
    RECORD_STATE_ALL = "all"

    ORDERING_CHOICES = (
        ("name", "Name ascending"),
        ("-name", "Name descending"),
        ("first_name", "First name ascending"),
        ("-first_name", "First name descending"),
        ("last_name", "Last name ascending"),
        ("-last_name", "Last name descending"),
        ("created_at", "Created at ascending"),
        ("-created_at", "Created at descending"),
        ("updated_at", "Updated at ascending"),
        ("-updated_at", "Updated at descending"),
        ("membership_joined_at", "Membership joined date ascending"),
        ("-membership_joined_at", "Membership joined date descending"),
    )

    page = serializers.IntegerField(required=False, min_value=1)
    page_size = serializers.IntegerField(required=False, min_value=1)
    q = serializers.CharField(required=False, allow_blank=True)
    relationship = serializers.ListField(
        child=serializers.ChoiceField(choices=(
            ("CONTACT", "Contact"),
            ("ACTIVE_MEMBER", "Active member"),
            ("FORMER_MEMBER", "Former member"),
        )),
        required=False,
    )
    location = serializers.ListField(
        child=serializers.CharField(allow_blank=False, trim_whitespace=True),
        required=False,
    )
    industry = serializers.ListField(child=serializers.IntegerField(min_value=1), required=False)
    career_stage = serializers.ListField(
        child=serializers.ChoiceField(choices=ProfessionalProfile.CareerStage.choices),
        required=False,
    )
    interest = serializers.ListField(child=serializers.IntegerField(min_value=1), required=False)
    skill = serializers.ListField(child=serializers.IntegerField(min_value=1), required=False)
    tag = serializers.ListField(child=serializers.IntegerField(min_value=1), required=False)
    record_state = serializers.ChoiceField(
        choices=(
            (RECORD_STATE_ACTIVE, "Active BUSINESS people"),
            (RECORD_STATE_ARCHIVED, "Archived BUSINESS people"),
            (RECORD_STATE_ALL, "All BUSINESS people"),
        ),
        required=False,
        default=RECORD_STATE_ACTIVE,
    )
    ordering = serializers.ChoiceField(
        choices=ORDERING_CHOICES,
        required=False,
        default="last_name",
    )

    def validate_page_size(self, value):
        if value not in {25, 50, 100}:
            raise serializers.ValidationError("page_size must be one of 25, 50, or 100.")
        return value


class PersonListSerializer(serializers.ModelSerializer):
    class Meta:
        model = Person
        fields = (
            "id",
            "first_name",
            "last_name",
            "primary_email",
            "mobile",
            "location",
            "age_range",
            "gender",
            "archived_at",
            "created_at",
            "updated_at",
        )


class StrictPersonWriteSerializer(serializers.Serializer):
    """Explicit Person write contract; read projections remain read-only."""

    first_name = serializers.CharField(max_length=150)
    last_name = serializers.CharField(max_length=150)
    primary_email = serializers.EmailField(required=False, allow_blank=True, allow_null=True)
    mobile = serializers.CharField(max_length=50, required=False, allow_blank=True)
    location = serializers.CharField(max_length=255, required=False, allow_blank=True)
    age_range = serializers.ChoiceField(
        choices=Person.AgeRange.choices,
        required=False,
        allow_blank=True,
    )
    gender = serializers.ChoiceField(
        choices=Person.Gender.choices,
        required=False,
        allow_blank=True,
    )

    def validate(self, attrs):
        unknown_fields = set(self.initial_data.keys()) - set(self.fields.keys())
        if unknown_fields:
            raise serializers.ValidationError(
                {field: ["This field is not allowed."] for field in sorted(unknown_fields)}
            )
        return attrs


class ReviewedIdentityCollisionSerializer(serializers.Serializer):
    collision = serializers.ChoiceField(choices=(
        "MOBILE_COLLISION",
        "EMAIL_COLLISION",
        "EMAIL_AND_MOBILE_COLLISION",
    ))
    person_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        allow_empty=False,
    )


class IdentityOverrideCreateSerializerMixin(serializers.Serializer):
    confirm_identity_override = serializers.BooleanField(required=False, default=False)
    reviewed_collision = ReviewedIdentityCollisionSerializer(required=False)

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if attrs.get("confirm_identity_override") and "reviewed_collision" not in attrs:
            raise serializers.ValidationError({"reviewed_collision": ["Reviewed collision evidence is required when confirming a separate Person."]})
        if "reviewed_collision" in attrs and not attrs.get("confirm_identity_override"):
            raise serializers.ValidationError({"confirm_identity_override": ["This field must be true when reviewed collision evidence is supplied."]})
        return attrs


class PersonCreateSerializer(IdentityOverrideCreateSerializerMixin, StrictPersonWriteSerializer):
    pass


class PersonUpdateSerializer(StrictPersonWriteSerializer):
    first_name = serializers.CharField(max_length=150, required=False)
    last_name = serializers.CharField(max_length=150, required=False)


class PersonMemberCreateSerializer(IdentityOverrideCreateSerializerMixin, StrictPersonWriteSerializer):
    joined_at = serializers.DateField()
    membership_source = serializers.ChoiceField(choices=Membership.Source.choices)


class DuplicatePersonMatchSerializer(serializers.ModelSerializer):
    class Meta:
        model = Person
        fields = ("id", "first_name", "last_name", "primary_email", "mobile", "archived_at")


class IdentityCollisionResponseSerializer(serializers.Serializer):
    code = serializers.ChoiceField(choices=("IDENTITY_COLLISION", "IDENTITY_COLLISION_STALE"))
    detail = serializers.CharField()
    collision = ReviewedIdentityCollisionSerializer()
    candidates = DuplicatePersonMatchSerializer(many=True)


class EmptyPersonLifecycleSerializer(serializers.Serializer):
    def validate(self, attrs):
        if self.initial_data:
            raise serializers.ValidationError(
                {field: ["This field is not allowed."] for field in sorted(self.initial_data.keys())}
            )
        return attrs


class PaginatedPersonListSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    next = serializers.URLField(allow_null=True)
    previous = serializers.URLField(allow_null=True)
    results = PersonListSerializer(many=True)


class PersonRelationshipSerializer(serializers.Serializer):
    TYPE_CONTACT = "CONTACT"
    TYPE_ACTIVE_MEMBER = "ACTIVE_MEMBER"
    TYPE_FORMER_MEMBER = "FORMER_MEMBER"

    TYPE_CHOICES = (
        (TYPE_CONTACT, "Contact"),
        (TYPE_ACTIVE_MEMBER, "Active Member"),
        (TYPE_FORMER_MEMBER, "Former Member"),
    )

    type = serializers.ChoiceField(choices=TYPE_CHOICES)
    label = serializers.CharField()


class PersonOverviewSerializer(serializers.Serializer):
    person = PersonListSerializer(source="*")
    relationship = serializers.SerializerMethodField()
    membership = serializers.SerializerMethodField()
    professional_profile = serializers.SerializerMethodField()
    skills = serializers.SerializerMethodField()
    interests = serializers.SerializerMethodField()
    tags = serializers.SerializerMethodField()

    @extend_schema_field(PersonRelationshipSerializer)
    def get_relationship(self, instance):
        membership = self._get_membership(instance)
        if membership is None:
            relationship_type = PersonRelationshipSerializer.TYPE_CONTACT
            label = "Contact"
        elif membership.status == Membership.Status.ACTIVE:
            relationship_type = PersonRelationshipSerializer.TYPE_ACTIVE_MEMBER
            label = "Active Member"
        else:
            relationship_type = PersonRelationshipSerializer.TYPE_FORMER_MEMBER
            label = "Former Member"

        return {
            "type": relationship_type,
            "label": label,
        }

    @extend_schema_field(MembershipSerializer(allow_null=True))
    def get_membership(self, instance):
        membership = self._get_membership(instance)
        if membership is None:
            return None
        return MembershipSerializer(membership).data

    @extend_schema_field(ProfessionalProfileSerializer(allow_null=True))
    def get_professional_profile(self, instance):
        professional_profile = self._get_professional_profile(instance)
        if professional_profile is None:
            return None
        return ProfessionalProfileSerializer(professional_profile).data

    @extend_schema_field(SkillSummarySerializer(many=True))
    def get_skills(self, instance):
        return SkillSummarySerializer(self._get_active_skills(instance), many=True).data

    @extend_schema_field(InterestSummarySerializer(many=True))
    def get_interests(self, instance):
        return InterestSummarySerializer(self._get_active_interests(instance), many=True).data

    @extend_schema_field(TagSummarySerializer(many=True))
    def get_tags(self, instance):
        return TagSummarySerializer(self._get_active_tags(instance), many=True).data

    def _get_membership(self, instance):
        try:
            return instance.membership
        except Membership.DoesNotExist:
            return None

    def _get_professional_profile(self, instance):
        try:
            return instance.professional_profile
        except ProfessionalProfile.DoesNotExist:
            return None

    def _get_active_skills(self, instance):
        prefetched_person_skills = getattr(instance, "active_person_skills", None)
        if prefetched_person_skills is not None:
            return [person_skill.skill for person_skill in prefetched_person_skills]

        return list(
            Skill.objects.filter(
                person_skills__person=instance,
                is_active=True,
            ).order_by("display_order", "name", "id")
        )

    def _get_active_interests(self, instance):
        prefetched_person_interests = getattr(instance, "active_person_interests", None)
        if prefetched_person_interests is not None:
            return [person_interest.interest for person_interest in prefetched_person_interests]

        return list(
            Interest.objects.filter(
                person_interests__person=instance,
                is_active=True,
            ).order_by("display_order", "name", "id")
        )

    def _get_active_tags(self, instance):
        prefetched_person_tags = getattr(instance, "active_person_tags", None)
        if prefetched_person_tags is not None:
            return [person_tag.tag for person_tag in prefetched_person_tags]

        return list(
            Tag.objects.filter(
                person_tags__person=instance,
                person_tags__is_active=True,
                is_active=True,
            ).order_by("display_order", "name", "id")
        )
