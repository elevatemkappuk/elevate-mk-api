from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

from staff_access.models import CapabilityDefinition, CapabilityProposal, StaffRole


class StaffInvitationCreateSerializer(serializers.Serializer):
    email = serializers.EmailField()
    first_name = serializers.CharField(max_length=150)
    last_name = serializers.CharField(max_length=150)
    role_codes = serializers.ListField(
        child=serializers.ChoiceField(choices=StaffRole.CANONICAL_ROLES),
        allow_empty=False,
    )
    expiry_hours = serializers.IntegerField(min_value=1, max_value=168, default=72, required=False)


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


class StaffInvitationRoleUpdateSerializer(serializers.Serializer):
    role_codes = serializers.ListField(
        child=serializers.ChoiceField(choices=StaffRole.CANONICAL_ROLES),
        allow_empty=False,
    )


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
