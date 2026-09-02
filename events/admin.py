from django.contrib import admin

from events.models import Event, EventParticipation, ExternalEventReference


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "start_at", "end_at", "timezone", "location_name", "status")
    list_filter = ("status", "timezone")
    search_fields = ("name", "location_name")
    readonly_fields = ("created_at", "updated_at")


@admin.register(EventParticipation)
class EventParticipationAdmin(admin.ModelAdmin):
    list_display = ("id", "event", "person", "status", "ticket_quantity", "registered_at")
    list_filter = ("status", "event")
    search_fields = ("event__name", "person__first_name", "person__last_name", "person__primary_email")
    autocomplete_fields = ("event", "person")
    readonly_fields = ("created_at", "updated_at")


@admin.register(ExternalEventReference)
class ExternalEventReferenceAdmin(admin.ModelAdmin):
    list_display = ("id", "provider", "reference_type", "external_id", "event", "participation", "import_record", "created_at")
    list_filter = ("provider", "reference_type")
    search_fields = ("provider", "external_id", "event__name")
    autocomplete_fields = ("event", "participation", "import_record")
    readonly_fields = ("created_at",)
