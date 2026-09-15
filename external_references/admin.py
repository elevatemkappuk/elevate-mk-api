from django.contrib import admin

from external_references.models import ExternalPersonReference


@admin.register(ExternalPersonReference)
class ExternalPersonReferenceAdmin(admin.ModelAdmin):
    list_display = ("id", "person", "provider", "reference_type", "external_id", "status", "linked_at", "revoked_at")
    list_filter = ("provider", "reference_type", "status")
    search_fields = ("external_id", "provider", "person__first_name", "person__last_name", "person__primary_email")
    autocomplete_fields = ("person",)
    readonly_fields = ("person", "provider", "reference_type", "external_id", "status", "linked_at", "revoked_at", "created_at", "updated_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
