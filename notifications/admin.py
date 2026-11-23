from django.contrib import admin

from notifications.models import TransactionalEmailJob


@admin.register(TransactionalEmailJob)
class TransactionalEmailJobAdmin(admin.ModelAdmin):
    list_display = ("job_type", "recipient_email", "status", "attempts", "available_at", "sent_at")
    list_filter = ("job_type", "status")
    search_fields = ("recipient_email", "recipient_name")
    readonly_fields = ("created_at", "updated_at")
