from rest_framework import serializers

from accounts.models import User
from notes.models import InternalNote


class NotesRecordStateQuerySerializer(serializers.Serializer):
    RECORD_STATE_ACTIVE = "active"
    RECORD_STATE_ARCHIVED = "archived"
    RECORD_STATE_ALL = "all"

    page = serializers.IntegerField(required=False, min_value=1)
    page_size = serializers.IntegerField(required=False, min_value=1)
    record_state = serializers.ChoiceField(
        choices=(
            (RECORD_STATE_ACTIVE, "Active notes"),
            (RECORD_STATE_ARCHIVED, "Archived notes"),
            (RECORD_STATE_ALL, "All notes"),
        ),
        required=False,
        default=RECORD_STATE_ACTIVE,
    )

    def validate_page_size(self, value):
        if value not in {25, 50, 100}:
            raise serializers.ValidationError("page_size must be one of 25, 50, or 100.")
        return value


class InternalNoteUserSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ("id", "email")


class InternalNoteSerializer(serializers.ModelSerializer):
    created_by = InternalNoteUserSummarySerializer()
    archived_by = InternalNoteUserSummarySerializer(allow_null=True)

    class Meta:
        model = InternalNote
        fields = (
            "id",
            "body",
            "created_by",
            "created_at",
            "updated_at",
            "archived_at",
            "archived_by",
            "archive_reason",
        )


class PaginatedInternalNoteListSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    next = serializers.URLField(allow_null=True)
    previous = serializers.URLField(allow_null=True)
    results = InternalNoteSerializer(many=True)


class StrictSerializer(serializers.Serializer):
    def validate(self, attrs):
        unknown_fields = set(self.initial_data.keys()) - set(self.fields.keys())
        if unknown_fields:
            raise serializers.ValidationError(
                {field: ["This field is not allowed."] for field in sorted(unknown_fields)}
            )
        return attrs


class InternalNoteCreateSerializer(StrictSerializer):
    body = serializers.CharField(allow_blank=False, trim_whitespace=True)

    def validate_body(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Body cannot be blank.")
        return value


class InternalNoteUpdateSerializer(StrictSerializer):
    body = serializers.CharField(required=False, allow_blank=False, trim_whitespace=True)

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if "body" not in attrs:
            raise serializers.ValidationError({"body": ["This field is required."]})
        return attrs

    def validate_body(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Body cannot be blank.")
        return value


class InternalNoteArchiveSerializer(StrictSerializer):
    archive_reason = serializers.CharField(required=False, allow_blank=True, trim_whitespace=True)


class EmptyRequestSerializer(StrictSerializer):
    def validate(self, attrs):
        attrs = super().validate(attrs)
        return attrs

