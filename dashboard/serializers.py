from rest_framework import serializers
from people.models import Person


class OverviewSerializer(serializers.Serializer):
    total_people = serializers.IntegerField(min_value=0)
    active_members = serializers.IntegerField(min_value=0)
    contacts = serializers.IntegerField(min_value=0)
    former_members = serializers.IntegerField(min_value=0)


class MonthlyCountSerializer(serializers.Serializer):
    month = serializers.RegexField(r"^\d{4}-\d{2}$", help_text="Calendar month, YYYY-MM, oldest first.")
    count = serializers.IntegerField(min_value=0)


class GrowthSerializer(serializers.Serializer):
    people_by_month = MonthlyCountSerializer(many=True, min_length=6, max_length=6)
    members_by_month = MonthlyCountSerializer(many=True, min_length=6, max_length=6)


class LabelCountSerializer(serializers.Serializer):
    label = serializers.CharField()
    count = serializers.IntegerField(min_value=0)


class IndustryCountSerializer(LabelCountSerializer):
    id = serializers.IntegerField()


class AgeRangeCountSerializer(LabelCountSerializer):
    value = serializers.ChoiceField(choices=Person.AgeRange.choices)


class CommunityProfileSerializer(serializers.Serializer):
    top_locations = LabelCountSerializer(many=True, max_length=5)
    top_industries = IndustryCountSerializer(many=True, max_length=5)
    age_ranges = AgeRangeCountSerializer(many=True)


class AttentionSerializer(serializers.Serializer):
    imports_needing_review = serializers.IntegerField(min_value=0)
    archived_people = serializers.IntegerField(min_value=0)


class DashboardSerializer(serializers.Serializer):
    overview = OverviewSerializer()
    growth = GrowthSerializer()
    community_profile = CommunityProfileSerializer()
    attention = AttentionSerializer()
