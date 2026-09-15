from django.contrib import admin

from marketing_preferences.models import MarketingPreference, MarketingPreferenceHistory, MarketingWebhookReceipt


@admin.register(MarketingPreference)
class MarketingPreferenceAdmin(admin.ModelAdmin):
    list_display = ("id", "person", "channel", "state", "source", "recorded_at", "actor_user")
    list_filter = ("channel", "state", "source", "recorded_at")
    search_fields = ("person__first_name", "person__last_name", "person__primary_email")
    autocomplete_fields = ("person", "actor_user")
    readonly_fields = ("person", "channel", "state", "source", "recorded_at", "actor_user", "created_at", "updated_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(MarketingPreferenceHistory)
class MarketingPreferenceHistoryAdmin(admin.ModelAdmin):
    list_display = ("id", "preference", "channel", "state", "source", "recorded_at", "actor_user")
    list_filter = ("channel", "state", "source", "recorded_at")
    search_fields = ("preference__person__first_name", "preference__person__last_name", "preference__person__primary_email")
    autocomplete_fields = ("preference", "actor_user")
    readonly_fields = ("preference", "channel", "state", "source", "recorded_at", "actor_user", "created_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(MarketingWebhookReceipt)
class MarketingWebhookReceiptAdmin(admin.ModelAdmin):
    list_display = ("id", "provider", "event_type", "event_fingerprint", "person", "outcome", "received_at")
    list_filter = ("provider", "event_type", "outcome", "received_at")
    search_fields = ("event_fingerprint", "campaign_id", "person__first_name", "person__last_name")
    autocomplete_fields = ("person",)
    readonly_fields = ("provider", "event_fingerprint", "event_type", "person", "event_recorded_at", "list_ids", "campaign_id", "outcome", "received_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
