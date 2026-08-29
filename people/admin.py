from django.contrib import admin

from people.models import Person


@admin.register(Person)
class PersonAdmin(admin.ModelAdmin):
    list_display = ("id", "first_name", "last_name", "primary_email", "mobile", "archived_at")
    search_fields = ("first_name", "last_name", "primary_email", "mobile", "location")
    list_filter = ("archived_at", "created_at", "updated_at")
