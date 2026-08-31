from django.contrib import admin

from notes.models import InternalNote


class ArchivedStateFilter(admin.SimpleListFilter):
    title = "archived state"
    parameter_name = "archived_state"

    def lookups(self, request, model_admin):
        return (
            ("active", "Active"),
            ("archived", "Archived"),
        )

    def queryset(self, request, queryset):
        if self.value() == "active":
            return queryset.filter(archived_at__isnull=True)
        if self.value() == "archived":
            return queryset.filter(archived_at__isnull=False)
        return queryset


@admin.register(InternalNote)
class InternalNoteAdmin(admin.ModelAdmin):
    list_display = ("id", "person", "created_by", "created_at", "updated_at", "archived_at")
    list_filter = (ArchivedStateFilter, "created_at")
    search_fields = (
        "person__first_name",
        "person__last_name",
        "person__primary_email",
        "created_by__email",
        "archived_by__email",
    )
    readonly_fields = (
        "person",
        "body",
        "created_by",
        "created_at",
        "updated_at",
        "archived_at",
        "archived_by",
        "archive_reason",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_view_permission(self, request, obj=None):
        return super().has_view_permission(request, obj)

