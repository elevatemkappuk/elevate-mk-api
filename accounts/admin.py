from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.core.exceptions import ValidationError

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

    def has_add_permission(self, request):
        from staff_access.admin import can_manage_staff_access

        return can_manage_staff_access(request.user)

    def has_change_permission(self, request, obj=None):
        from staff_access.admin import can_manage_staff_access

        return can_manage_staff_access(request.user)

    def get_readonly_fields(self, request, obj=None):
        readonly_fields = list(super().get_readonly_fields(request, obj))
        if obj is not None:
            readonly_fields.extend(["email", "person"])
        if not request.user.is_superuser:
            readonly_fields.extend(["is_superuser", "is_staff", "groups", "user_permissions"])
        return tuple(dict.fromkeys(readonly_fields))

    def save_model(self, request, obj, form, change):
        if change and obj.pk:
            previous = type(obj).objects.select_related("person").get(pk=obj.pk)
            # Authentication email and identity linkage are changed only by
            # their verified lifecycle workflows, never by direct Admin POST.
            obj.email = previous.email
            obj.person = previous.person
        if obj.person.record_type not in ("STAFF", "TECHNICAL"):
            raise ValidationError("Django Admin User provisioning requires a STAFF or TECHNICAL Person.")
        if obj.person.record_type == "TECHNICAL" and not request.user.is_superuser:
            raise ValidationError("Only designated superusers may provision TECHNICAL Users.")
        obj._audit_actor_user = request.user
        obj._audit_request = request
        try:
            super().save_model(request, obj, form, change)
        finally:
            if hasattr(obj, "_audit_actor_user"):
                del obj._audit_actor_user
            if hasattr(obj, "_audit_request"):
                del obj._audit_request

    @admin.display(ordering="person__last_name", description="Person")
    def person_name(self, obj):
        return obj.get_full_name()

    @admin.display(ordering="person__primary_email", description="Person email")
    def person_primary_email(self, obj):
        return obj.person.primary_email
