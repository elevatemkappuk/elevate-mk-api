from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from memberships.models import Membership
from memberships.serializers import MembershipSerializer
from people.models import Person
from professional_profiles.models import ProfessionalProfile
from professional_profiles.serializers import ProfessionalProfileSerializer
from skills.models import Skill
from skills.serializers import SkillSummarySerializer


class PersonListQuerySerializer(serializers.Serializer):
    RECORD_STATE_ACTIVE = "active"
    RECORD_STATE_ARCHIVED = "archived"
    RECORD_STATE_ALL = "all"

    ORDERING_CHOICES = (
        ("first_name", "First name ascending"),
        ("-first_name", "First name descending"),
        ("last_name", "Last name ascending"),
        ("-last_name", "Last name descending"),
        ("created_at", "Created at ascending"),
        ("-created_at", "Created at descending"),
        ("updated_at", "Updated at ascending"),
        ("-updated_at", "Updated at descending"),
    )

    page = serializers.IntegerField(required=False, min_value=1)
    page_size = serializers.IntegerField(required=False, min_value=1)
    q = serializers.CharField(required=False, allow_blank=True)
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
