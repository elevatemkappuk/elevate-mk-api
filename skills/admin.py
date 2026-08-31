from django.contrib import admin

from skills.models import PersonSkill, Skill


@admin.register(Skill)
class SkillAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "is_active", "display_order")
    list_filter = ("is_active",)
    search_fields = ("name", "slug")
    readonly_fields = ("created_at", "updated_at")
    ordering = ("display_order", "name", "id")


@admin.register(PersonSkill)
class PersonSkillAdmin(admin.ModelAdmin):
    list_display = ("person", "skill", "created_at")
    list_filter = ("skill",)
    search_fields = (
        "person__first_name",
        "person__last_name",
        "person__primary_email",
        "skill__name",
        "skill__slug",
    )
    autocomplete_fields = ("person", "skill")
    readonly_fields = ("created_at",)
    ordering = ("person_id", "skill__display_order", "skill__name", "skill__id")

