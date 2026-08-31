from django.contrib import admin

from interests.models import Interest, PersonInterest


@admin.register(Interest)
class InterestAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "is_active", "display_order")
    list_filter = ("is_active",)
    search_fields = ("name", "slug")
    readonly_fields = ("created_at", "updated_at")
    ordering = ("display_order", "name", "id")


@admin.register(PersonInterest)
class PersonInterestAdmin(admin.ModelAdmin):
    list_display = ("person", "interest", "created_at")
    list_filter = ("interest",)
    search_fields = (
        "person__first_name",
        "person__last_name",
        "person__primary_email",
        "interest__name",
        "interest__slug",
    )
    autocomplete_fields = ("person", "interest")
    readonly_fields = ("created_at",)
    ordering = ("person_id", "interest__display_order", "interest__name", "interest__id")

