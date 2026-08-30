from django.contrib import admin

from professional_profiles.models import Industry, ProfessionalProfile


@admin.register(Industry)
class IndustryAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "is_active", "display_order")
    list_filter = ("is_active",)
    search_fields = ("name", "slug")
    readonly_fields = ("created_at", "updated_at")
    ordering = ("display_order", "name", "id")


@admin.register(ProfessionalProfile)
class ProfessionalProfileAdmin(admin.ModelAdmin):
    list_display = ("person", "job_title", "company", "industry", "career_stage", "created_at", "updated_at")
    list_filter = ("industry",)
    search_fields = (
        "person__first_name",
        "person__last_name",
        "person__primary_email",
        "job_title",
        "company",
        "industry__name",
    )
    autocomplete_fields = ("person", "industry")
    readonly_fields = ("created_at", "updated_at")

