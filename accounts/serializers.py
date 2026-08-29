from django.contrib.auth import authenticate
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from accounts.models import User, normalize_email_address
from staff_access.permissions import get_active_staff_role_codes_for_user


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
            raise serializers.ValidationError(
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
