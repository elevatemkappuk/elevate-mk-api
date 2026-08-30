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
