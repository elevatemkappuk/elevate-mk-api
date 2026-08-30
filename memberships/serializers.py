from rest_framework import serializers

from memberships.models import Membership


class MembershipSerializer(serializers.ModelSerializer):
    class Meta:
        model = Membership
        fields = (
            "id",
            "status",
            "joined_at",
            "ended_at",
            "membership_source",
            "created_at",
            "updated_at",
        )


class MakeMembershipSerializer(serializers.Serializer):
    joined_at = serializers.DateField()
    membership_source = serializers.ChoiceField(choices=Membership.Source.choices)

    def validate(self, attrs):
        unknown_fields = set(self.initial_data.keys()) - set(self.fields.keys())
        if unknown_fields:
            raise serializers.ValidationError(
                {field: ["This field is not allowed."] for field in sorted(unknown_fields)}
            )
        return attrs
