from rest_framework import serializers

from people.models import Person


class PersonListQuerySerializer(serializers.Serializer):
    RECORD_STATE_ACTIVE = "active"
    RECORD_STATE_ARCHIVED = "archived"
    RECORD_STATE_ALL = "all"

    ORDERING_CHOICES = (
        ("first_name", "First name ascending"),
        ("-first_name", "First name descending"),
        ("last_name", "Last name ascending"),
        ("-last_name", "Last name descending"),
        ("created_at", "Created at ascending"),
        ("-created_at", "Created at descending"),
        ("updated_at", "Updated at ascending"),
        ("-updated_at", "Updated at descending"),
    )

    page = serializers.IntegerField(required=False, min_value=1)
    page_size = serializers.IntegerField(required=False, min_value=1)
    q = serializers.CharField(required=False, allow_blank=True)
    record_state = serializers.ChoiceField(
        choices=(
            (RECORD_STATE_ACTIVE, "Active BUSINESS people"),
            (RECORD_STATE_ARCHIVED, "Archived BUSINESS people"),
            (RECORD_STATE_ALL, "All BUSINESS people"),
        ),
        required=False,
        default=RECORD_STATE_ACTIVE,
    )
    ordering = serializers.ChoiceField(
        choices=ORDERING_CHOICES,
        required=False,
        default="last_name",
    )

    def validate_page_size(self, value):
        if value not in {25, 50, 100}:
            raise serializers.ValidationError("page_size must be one of 25, 50, or 100.")
        return value


class PersonListSerializer(serializers.ModelSerializer):
    class Meta:
        model = Person
        fields = (
            "id",
            "first_name",
            "last_name",
            "primary_email",
            "mobile",
            "location",
            "age_range",
            "gender",
            "archived_at",
            "created_at",
            "updated_at",
        )


class PaginatedPersonListSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    next = serializers.URLField(allow_null=True)
    previous = serializers.URLField(allow_null=True)
    results = PersonListSerializer(many=True)
