from django.contrib.auth import authenticate, get_user_model, password_validation
from django.contrib.auth.tokens import default_token_generator
from django.utils.encoding import force_str
from django.utils.http import urlsafe_base64_decode
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from accounts.models import normalize_email_address
from staff_access.permissions import get_active_staff_role_codes_for_user


User = get_user_model()


class InvalidCredentialsError(serializers.ValidationError):
    pass


class PersonSummarySerializer(serializers.Serializer):
    id = serializers.IntegerField()
    first_name = serializers.CharField()
    last_name = serializers.CharField()
    primary_email = serializers.EmailField(allow_null=True)


class AuthenticatedUserSerializer(serializers.ModelSerializer):
    person = PersonSummarySerializer(read_only=True)

    class Meta:
        model = User
        fields = ("id", "email", "person")

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data["person"] = PersonSummarySerializer(instance.person).data
        return data


class CurrentUserSerializer(AuthenticatedUserSerializer):
    staff_roles = serializers.SerializerMethodField()

    class Meta(AuthenticatedUserSerializer.Meta):
        fields = ("id", "email", "person", "staff_roles")

    @extend_schema_field(serializers.ListField(child=serializers.CharField()))
    def get_staff_roles(self, obj):
        return get_active_staff_role_codes_for_user(obj)


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, trim_whitespace=False)

    default_error_messages = {
        "invalid_credentials": "Invalid email or password.",
    }

    def validate(self, attrs):
        email = normalize_email_address(attrs["email"])
        password = attrs["password"]
        request = self.context.get("request")
        user = authenticate(request=request, email=email, password=password)

        if user is None or not user.is_active:
            raise InvalidCredentialsError(
                {"detail": self.error_messages["invalid_credentials"]}
            )

        attrs["email"] = email
        attrs["user"] = user
        return attrs


class AuthErrorSerializer(serializers.Serializer):
    detail = serializers.ListField(
        child=serializers.CharField(),
        help_text="Generic authentication or validation error messages.",
    )


class DetailSerializer(serializers.Serializer):
    detail = serializers.CharField()


class PasswordResetRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()

    def validate_email(self, value):
        return normalize_email_address(value)


class PasswordResetConfirmSerializer(serializers.Serializer):
    uid = serializers.CharField()
    token = serializers.CharField(trim_whitespace=False)
    new_password = serializers.CharField(write_only=True, trim_whitespace=False)
    confirm_password = serializers.CharField(write_only=True, trim_whitespace=False)

    default_error_messages = {
        "invalid_token": "This password reset link is invalid or has expired.",
        "password_mismatch": "The passwords do not match.",
    }

    def validate(self, attrs):
        if attrs["new_password"] != attrs["confirm_password"]:
            raise serializers.ValidationError({"confirm_password": self.error_messages["password_mismatch"]})
        try:
            user_id = force_str(urlsafe_base64_decode(attrs["uid"]))
            user = User.objects.get(pk=user_id)
        except (TypeError, ValueError, OverflowError, User.DoesNotExist):
            raise serializers.ValidationError(
                {"code": "invalid_password_reset_token", "detail": self.error_messages["invalid_token"]}
            )
        if not user.is_active or not default_token_generator.check_token(user, attrs["token"]):
            raise serializers.ValidationError(
                {"code": "invalid_password_reset_token", "detail": self.error_messages["invalid_token"]}
            )
        password_validation.validate_password(attrs["new_password"], user)
        attrs["user"] = user
        return attrs
