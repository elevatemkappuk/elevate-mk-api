from django.contrib import admin

from people.models import Person


@admin.register(Person)
class PersonAdmin(admin.ModelAdmin):
    list_display = ("id", "record_type", "first_name", "last_name", "primary_email", "mobile", "archived_at")
    search_fields = ("first_name", "last_name", "primary_email", "mobile", "location")
    list_filter = ("record_type", "archived_at", "created_at", "updated_at")
    readonly_fields = ("record_type",)

    def save_model(self, request, obj, form, change):
        # Classification changes are only valid through the audited correction
        # workflow.  Do not let a crafted Admin POST or a custom form bypass it.
        if change and obj.pk:
            previous_type = type(obj).objects.filter(pk=obj.pk).values_list("record_type", flat=True).first()
            if previous_type is not None:
                obj.record_type = previous_type
        return super().save_model(request, obj, form, change)
