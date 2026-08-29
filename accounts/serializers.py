from django.contrib.auth import authenticate
from rest_framework import serializers

from accounts.models import User, normalize_email_address


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
