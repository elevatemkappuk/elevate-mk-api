from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

from staff_access.models import CapabilityDefinition, CapabilityProposal, StaffRole


class StaffOnboardingPersonLookupSerializer(serializers.Serializer):
    q = serializers.CharField(min_length=2, max_length=120, trim_whitespace=True)


class StaffOnboardingValidationSerializer(serializers.Serializer):
    person_id = serializers.IntegerField(required=False, min_value=1)
    first_name = serializers.CharField(required=False, max_length=150)
    last_name = serializers.CharField(required=False, max_length=150)
    primary_email = serializers.EmailField(required=False, allow_blank=True, allow_null=True)
    mobile = serializers.CharField(required=False, allow_blank=True, max_length=50)
    location = serializers.CharField(required=False, allow_blank=True, max_length=255)
    age_range = serializers.ChoiceField(required=False, choices=(), allow_blank=True)
    gender = serializers.ChoiceField(required=False, choices=(), allow_blank=True)
    login_email = serializers.EmailField(required=False, allow_blank=True)
    role_code = serializers.ChoiceField(choices=StaffRole.CANONICAL_ROLES)
    reason = serializers.CharField(min_length=1, max_length=4000, trim_whitespace=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from people.models import Person

        self.fields["age_range"].choices = Person.AgeRange.choices
        self.fields["gender"].choices = Person.Gender.choices

    def validate(self, attrs):
        unknown_fields = set(self.initial_data.keys()) - set(self.fields.keys())
        if unknown_fields:
            raise serializers.ValidationError({field: ["This field is not allowed."] for field in sorted(unknown_fields)})
        person_id = attrs.get("person_id")
        has_new_identity = any(attrs.get(field) not in (None, "") for field in ("first_name", "last_name"))
        if bool(person_id) == has_new_identity:
            raise serializers.ValidationError("Provide either person_id or a new Person identity, not both.")
        if not person_id and (not attrs.get("first_name", "").strip() or not attrs.get("last_name", "").strip()):
            raise serializers.ValidationError({"first_name": "First name and last name are required for a new Person."})
        return attrs


class StaffOnboardingSubmissionSerializer(StaffOnboardingValidationSerializer):
    pass


class StaffInvitationInspectionSerializer(serializers.Serializer):
    invitation = serializers.UUIDField()
    token = serializers.CharField(min_length=20, max_length=200)


class StaffInvitationAcceptanceSerializer(serializers.Serializer):
    password = serializers.CharField(required=False, allow_blank=False, trim_whitespace=False, write_only=True)
    confirm_password = serializers.CharField(required=False, allow_blank=False, trim_whitespace=False, write_only=True)

    def validate(self, attrs):
        if attrs.get("password") != attrs.get("confirm_password"):
            raise serializers.ValidationError({"confirm_password": "Passwords do not match."})
        return attrs


class StaffDirectoryQuerySerializer(serializers.Serializer):
    q = serializers.CharField(required=False, allow_blank=True, max_length=120)
    role = serializers.ChoiceField(required=False, choices=StaffRole.CANONICAL_ROLES)
    staff_state = serializers.ChoiceField(required=False, choices=[
        ("ACTIVE", "Active"), ("FORMER", "Former"), ("PENDING", "Pending"), ("DISABLED", "Disabled"),
    ])
    account_state = serializers.ChoiceField(required=False, choices=[("ACTIVE", "Active"), ("DISABLED", "Disabled"), ("PENDING_ACTIVATION", "Pending activation")])
    ordering = serializers.ChoiceField(required=False, choices=[("name", "Name"), ("-name", "Name descending"), ("updated_at", "Updated"), ("-updated_at", "Updated descending")])
    page_size = serializers.IntegerField(required=False, min_value=1, max_value=100)


class StaffDirectorySerializer(serializers.Serializer):
    user_id = serializers.IntegerField(allow_null=True)
    person_id = serializers.IntegerField(allow_null=True)
    display_name = serializers.CharField()
    login_email = serializers.CharField(allow_null=True)
    crm_role = serializers.CharField(allow_null=True)
    staff_access_state = serializers.CharField()
    account_state = serializers.CharField(allow_null=True)
    invitation_state = serializers.CharField(allow_null=True)
    administrator_approval_state = serializers.CharField(allow_null=True)
    protected_account = serializers.BooleanField()
    updated_at = serializers.DateTimeField(allow_null=True)


class StaffDirectoryDetailSerializer(StaffDirectorySerializer):
    person = serializers.DictField(allow_null=True)
    role_assignments = serializers.ListField()
    invitations = serializers.ListField()
    administrator_proposals = serializers.ListField()


class StaffActivitySerializer(serializers.Serializer):
    id = serializers.IntegerField()
    event_type = serializers.CharField()
    occurred_at = serializers.DateTimeField()
    actor = serializers.DictField(allow_null=True)
    outcome = serializers.CharField(allow_null=True)
    reason = serializers.CharField(allow_null=True)


class StaffInvitationCreateSerializer(serializers.Serializer):
    email = serializers.EmailField()
    first_name = serializers.CharField(max_length=150)
    last_name = serializers.CharField(max_length=150)
    role_codes = serializers.ListField(
        child=serializers.ChoiceField(choices=StaffRole.CANONICAL_ROLES),
        allow_empty=False,
    )
    expiry_hours = serializers.IntegerField(min_value=1, max_value=168, default=72, required=False)
    reason = serializers.CharField(min_length=1, max_length=4000, trim_whitespace=True)


class StaffInvitationRedeemSerializer(serializers.Serializer):
    password = serializers.CharField(write_only=True, trim_whitespace=False)
    confirm_password = serializers.CharField(write_only=True, trim_whitespace=False)

    def validate(self, attrs):
        if attrs["password"] != attrs["confirm_password"]:
            raise serializers.ValidationError({"confirm_password": "Passwords do not match."})
        validate_password(attrs["password"])
        return attrs


class StaffInvitationResponseSerializer(serializers.Serializer):
    public_id = serializers.UUIDField()
    intended_email = serializers.EmailField()
    expires_at = serializers.DateTimeField()
    delivery = serializers.CharField()


class StaffInvitationRoleUpdateSerializer(serializers.Serializer):
    role_codes = serializers.ListField(
        child=serializers.ChoiceField(choices=StaffRole.CANONICAL_ROLES),
        allow_empty=False,
    )


class StaffInvitationReasonSerializer(serializers.Serializer):
    reason = serializers.CharField(min_length=1, max_length=4000, trim_whitespace=True)


class StaffEmailChangeRequestSerializer(serializers.Serializer):
    user_id = serializers.IntegerField(min_value=1)
    requested_email = serializers.EmailField()


class StaffEmailChangeConfirmSerializer(serializers.Serializer):
    token = serializers.CharField()


class StaffLifecycleSerializer(serializers.Serializer):
    user_id = serializers.IntegerField(min_value=1)
    reason = serializers.CharField(min_length=1, max_length=4000, trim_whitespace=True)


class EffectiveAccessSerializer(serializers.Serializer):
    capability_codes = serializers.ListField(
        child=serializers.CharField(),
        read_only=True,
    )
    policy_version = serializers.IntegerField(read_only=True)


class CapabilityGovernanceMutationSerializer(serializers.Serializer):
    operation = serializers.ChoiceField(choices=CapabilityProposal.Operation.choices)
    capability = serializers.ChoiceField(
        choices=[(code, code) for code, _name, _description in CapabilityDefinition.CANONICAL_CAPABILITIES]
    )
    target_user_id = serializers.IntegerField(min_value=1, required=False)
    role_id = serializers.IntegerField(min_value=1, required=False)
    reason = serializers.CharField(max_length=4000, trim_whitespace=True)

    def validate(self, attrs):
        role_operation = attrs["operation"] in CapabilityProposal.ROLE_DEFAULT_OPERATIONS
        if role_operation and (attrs.get("role_id") is None or attrs.get("target_user_id") is not None):
            raise serializers.ValidationError("Role-default operations require role_id and no target_user_id.")
        if not role_operation and (attrs.get("target_user_id") is None or attrs.get("role_id") is not None):
            raise serializers.ValidationError("Individual operations require target_user_id and no role_id.")
        return attrs


class CapabilityProposalDecisionSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=4000, trim_whitespace=True, required=False)
