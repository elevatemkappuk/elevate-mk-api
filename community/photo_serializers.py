from rest_framework import serializers


class CommunityProfilePhotoUploadSerializer(serializers.Serializer):
    photo = serializers.ImageField()

    def validate(self, attrs):
        unknown_fields = set(self.initial_data.keys()) - {"photo"}
        if unknown_fields:
            raise serializers.ValidationError(
                {field: ["This field is not allowed."] for field in sorted(unknown_fields)}
            )
        return attrs
