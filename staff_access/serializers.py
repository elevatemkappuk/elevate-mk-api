from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

from staff_access.models import StaffRole


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
