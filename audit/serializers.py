from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from audit.policies import get_person_audit_description, project_person_audit_changes
from staff_access.models import CapabilityDefinition
from staff_access.permissions import user_has_capability


class PersonAuditHistoryQuerySerializer(serializers.Serializer):
    page = serializers.IntegerField(required=False, min_value=1)
    page_size = serializers.IntegerField(required=False, min_value=1)

    def validate_page_size(self, value):
        if value not in {25, 50, 100}:
            raise serializers.ValidationError("page_size must be one of 25, 50, or 100.")
        return value


class AuditActorSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    email = serializers.EmailField(allow_null=True)
    contact_restricted = serializers.BooleanField()


class PersonAuditHistoryEventSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    action = serializers.CharField()
    description = serializers.SerializerMethodField()
    actor = serializers.SerializerMethodField()
    occurred_at = serializers.DateTimeField()
    entity_type = serializers.CharField()
    changes = serializers.SerializerMethodField()

    @extend_schema_field(serializers.CharField())
    def get_description(self, instance):
        return get_person_audit_description(instance.action)

    @extend_schema_field(AuditActorSerializer(allow_null=True))
    def get_actor(self, instance):
        actor_user = instance.actor_user
        if actor_user is None:
            return None

        request = self.context.get("request")
        viewer = getattr(request, "user", None)
        can_view_contact = bool(viewer and user_has_capability(
            viewer,
            CapabilityDefinition.PERSON_CONTACT_READ,
        ))
        if not can_view_contact:
            return {"id": actor_user.id, "email": None, "contact_restricted": True}
        return {"id": actor_user.id, "email": actor_user.email, "contact_restricted": False}

    @extend_schema_field(serializers.JSONField())
    def get_changes(self, instance):
        return project_person_audit_changes(instance)


class PaginatedPersonAuditHistorySerializer(serializers.Serializer):
    count = serializers.IntegerField()
    next = serializers.URLField(allow_null=True)
    previous = serializers.URLField(allow_null=True)
    results = PersonAuditHistoryEventSerializer(many=True)
