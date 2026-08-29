from django.contrib import admin
from django.contrib import messages
from django.utils.translation import ngettext

from staff_access.models import StaffRole, StaffRoleAssignment


@admin.register(StaffRole)
class StaffRoleAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "is_active")
    search_fields = ("code", "name")
    list_filter = ("is_active",)
    readonly_fields = ("created_at", "updated_at")

    def get_fields(self, request, obj=None):
        return ("code", "name", "is_active", "created_at", "updated_at")

    def get_readonly_fields(self, request, obj=None):
        readonly_fields = list(super().get_readonly_fields(request, obj))
        if obj and obj.code in StaffRole.canonical_codes():
            readonly_fields.extend(["code", "name"])
        return readonly_fields

    def has_delete_permission(self, request, obj=None):
        return False

    def get_actions(self, request):
        actions = super().get_actions(request)
        actions.pop("delete_selected", None)
        return actions


@admin.register(StaffRoleAssignment)
class StaffRoleAssignmentAdmin(admin.ModelAdmin):
    actions = ("revoke_selected_assignments", "reactivate_selected_assignments")
    list_display = (
        "user",
        "role",
        "is_active",
        "assigned_at",
        "assigned_by",
        "revoked_at",
        "revoked_by",
    )
    search_fields = (
        "user__email",
        "user__person__first_name",
        "user__person__last_name",
        "role__code",
        "role__name",
    )
    list_filter = ("role", "is_active")
    autocomplete_fields = ("user", "role")
    readonly_fields = ("is_active", "assigned_at", "assigned_by", "revoked_at", "revoked_by", "created_at", "updated_at")

    def get_fields(self, request, obj=None):
        if obj is None:
            return ("user", "role")
        return ("user", "role", "is_active", "assigned_at", "assigned_by", "revoked_at", "revoked_by", "created_at", "updated_at")

    def get_readonly_fields(self, request, obj=None):
        readonly_fields = list(super().get_readonly_fields(request, obj))
        if obj:
            readonly_fields.extend(["user", "role"])
        return readonly_fields

    def has_delete_permission(self, request, obj=None):
        return False

    def get_actions(self, request):
        actions = super().get_actions(request)
        actions.pop("delete_selected", None)
        return actions

    def save_model(self, request, obj, form, change):
        if change:
            super().save_model(request, obj, form, change)
            return

        existing = StaffRoleAssignment.objects.filter(user=obj.user, role=obj.role).first()
        if existing:
            if not existing.is_active:
                existing.reactivate()
                self.message_user(request, "Existing assignment reactivated.", level=messages.SUCCESS)
            else:
                self.message_user(request, "Assignment already exists and remains active.", level=messages.WARNING)
            obj.pk = existing.pk
            return

        assignment = StaffRoleAssignment.objects.create(
            user=obj.user,
            role=obj.role,
            assigned_by=request.user,
        )
        obj.pk = assignment.pk
        obj.assigned_at = assignment.assigned_at
        obj.assigned_by = assignment.assigned_by
        obj.is_active = assignment.is_active

    @admin.action(description="Revoke selected staff role assignments")
    def revoke_selected_assignments(self, request, queryset):
        updated = 0
        for assignment in queryset.filter(is_active=True):
            assignment.revoke(revoked_by=request.user)
            updated += 1
        self.message_user(
            request,
            ngettext(
                "%d assignment was revoked.",
                "%d assignments were revoked.",
                updated,
            )
            % updated,
            level=messages.SUCCESS,
        )

    @admin.action(description="Reactivate selected staff role assignments")
    def reactivate_selected_assignments(self, request, queryset):
        updated = 0
        for assignment in queryset.filter(is_active=False):
            assignment.reactivate()
            updated += 1
        self.message_user(
            request,
            ngettext(
                "%d assignment was reactivated.",
                "%d assignments were reactivated.",
                updated,
            )
            % updated,
            level=messages.SUCCESS,
        )
