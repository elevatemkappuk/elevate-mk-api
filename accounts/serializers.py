from django.contrib.auth import authenticate
from rest_framework import serializers

from accounts.models import User, normalize_email_address


class AuthenticatedUserSerializer(serializers.ModelSerializer):
    person = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ("id", "email", "person")

    def get_person(self, obj):
        return {
            "id": obj.person_id,
            "first_name": obj.person.first_name,
            "last_name": obj.person.last_name,
            "primary_email": obj.person.primary_email,
        }


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
