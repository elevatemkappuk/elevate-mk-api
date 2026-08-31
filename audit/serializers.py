from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from audit.policies import get_person_audit_description, project_person_audit_changes


class PersonAuditHistoryQuerySerializer(serializers.Serializer):
    page = serializers.IntegerField(required=False, min_value=1)
    page_size = serializers.IntegerField(required=False, min_value=1)

    def validate_page_size(self, value):
        if value not in {25, 50, 100}:
            raise serializers.ValidationError("page_size must be one of 25, 50, or 100.")
        return value


class AuditActorSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    email = serializers.EmailField()


class PersonAuditHistoryEventSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    action = serializers.CharField()
    description = serializers.SerializerMethodField()
    actor = serializers.SerializerMethodField()
    occurred_at = serializers.DateTimeField()
    entity_type = serializers.CharField()
    entity_id = serializers.CharField(allow_null=True)
    changes = serializers.SerializerMethodField()

    @extend_schema_field(serializers.CharField())
    def get_description(self, instance):
        return get_person_audit_description(instance.action)

    @extend_schema_field(AuditActorSerializer(allow_null=True))
    def get_actor(self, instance):
        actor_user = instance.actor_user
        if actor_user is None:
            return None

        return {
            "id": actor_user.id,
            "email": actor_user.email,
        }

    @extend_schema_field(serializers.JSONField())
    def get_changes(self, instance):
        return project_person_audit_changes(instance)


class PaginatedPersonAuditHistorySerializer(serializers.Serializer):
    count = serializers.IntegerField()
    next = serializers.URLField(allow_null=True)
    previous = serializers.URLField(allow_null=True)
    results = PersonAuditHistoryEventSerializer(many=True)
