from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from accounts.models import User


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    model = User
    ordering = ("email",)
    list_display = ("email", "person_name", "person_primary_email", "is_staff", "is_active")
    search_fields = ("email", "person__first_name", "person__last_name", "person__primary_email")
    fieldsets = (
        (None, {"fields": ("email", "password", "person")}),
        ("Permissions", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Important dates", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "person", "password1", "password2"),
            },
        ),
    )
    readonly_fields = ("last_login", "date_joined")

    @admin.display(ordering="person__last_name", description="Person")
    def person_name(self, obj):
        return obj.get_full_name()

    @admin.display(ordering="person__primary_email", description="Person email")
    def person_primary_email(self, obj):
        return obj.person.primary_email
