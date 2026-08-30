from django.contrib import admin

from memberships.models import Membership


@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "person",
        "status",
        "membership_source",
        "joined_at",
        "ended_at",
        "created_at",
        "updated_at",
    )
    list_filter = ("status", "membership_source", "joined_at", "ended_at")
    search_fields = (
        "person__first_name",
        "person__last_name",
        "person__primary_email",
    )
    autocomplete_fields = ("person",)
    readonly_fields = ("created_at", "updated_at")
