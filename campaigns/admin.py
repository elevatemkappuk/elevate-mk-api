from django.contrib import admin

from .models import Campaign, CampaignPreparation, CampaignRecipientSnapshot


@admin.register(Campaign)
class CampaignAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "status", "created_by", "created_at")
    list_filter = ("status", "created_at")
    search_fields = ("name",)
    readonly_fields = ("created_by", "created_at", "updated_at", "current_preparation")


@admin.register(CampaignPreparation)
class CampaignPreparationAdmin(admin.ModelAdmin):
    list_display = ("id", "campaign", "attempt_number", "status", "selected_count", "included_count", "excluded_count")
    list_filter = ("status",)
    readonly_fields = [field.name for field in CampaignPreparation._meta.fields]


@admin.register(CampaignRecipientSnapshot)
class CampaignRecipientSnapshotAdmin(admin.ModelAdmin):
    list_display = ("id", "preparation", "person", "decision", "exclusion_reason", "captured_at")
    list_filter = ("decision", "exclusion_reason", "consent_state_snapshot")
    search_fields = ("person__first_name", "person__last_name")
    readonly_fields = [field.name for field in CampaignRecipientSnapshot._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
