from django.contrib import admin

from tags.models import PersonTag, Tag


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "is_active", "display_order")
    list_filter = ("is_active",)
    search_fields = ("name", "slug")
    readonly_fields = ("created_at", "updated_at")
    ordering = ("display_order", "name", "id")


@admin.register(PersonTag)
class PersonTagAdmin(admin.ModelAdmin):
    list_display = ("person", "tag", "is_active", "assigned_by", "assigned_at", "removed_by", "removed_at")
    list_filter = ("is_active", "tag", "assigned_at", "removed_at")
    search_fields = (
        "person__first_name",
        "person__last_name",
        "person__primary_email",
        "tag__name",
        "tag__slug",
        "assigned_by__email",
    )
    autocomplete_fields = ("person", "tag", "assigned_by", "removed_by")
    readonly_fields = ("assigned_at",)
    ordering = ("person_id", "tag__display_order", "tag__name", "tag__id")

