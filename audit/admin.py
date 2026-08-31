from django.contrib import admin

from audit.models import AuditEvent


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    list_display = ("occurred_at", "action", "actor_user", "entity_type", "entity_id")
    list_filter = ("action", "occurred_at", "entity_type")
    search_fields = ("actor_user__email", "entity_id", "action")
    readonly_fields = (
        "actor_user",
        "action",
        "entity_type",
        "entity_id",
        "changes",
        "metadata",
        "request_id",
        "ip_address",
        "occurred_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_view_permission(self, request, obj=None):
        return super().has_view_permission(request, obj)

