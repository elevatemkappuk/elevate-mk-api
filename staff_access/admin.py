from django.contrib import admin
from django.contrib import messages
from django.db import transaction
from django.utils.translation import ngettext

from audit.models import AuditEvent
from audit.services import record_audit_event
from staff_access.models import StaffRole, StaffRoleAssignment


def record_staff_role_audit_event(*, action, actor_user, assignment):
    record_audit_event(
        action=action,
        actor_user=actor_user,
        entity_type="StaffRoleAssignment",
        entity_id=assignment.id,
        changes={
            "is_active": {
                "from": None if action == AuditEvent.Action.STAFF_ROLE_ASSIGNED else False,
                "to": False if action == AuditEvent.Action.STAFF_ROLE_REVOKED else True,
            }
        },
        metadata={
            "target_user_id": str(assignment.user_id),
            "staff_role_id": str(assignment.role_id),
            "staff_role_code": assignment.role.code,
        },
    )


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

        with transaction.atomic():
            existing = (
                StaffRoleAssignment.objects.select_for_update()
                .select_related("role")
                .filter(user=obj.user, role=obj.role)
                .first()
            )
            if existing:
                if not existing.is_active:
                    existing.reactivate()
                    record_staff_role_audit_event(
                        action=AuditEvent.Action.STAFF_ROLE_REACTIVATED,
                        actor_user=request.user,
                        assignment=existing,
                    )
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
            assignment = StaffRoleAssignment.objects.select_related("role").get(pk=assignment.pk)
            record_staff_role_audit_event(
                action=AuditEvent.Action.STAFF_ROLE_ASSIGNED,
                actor_user=request.user,
                assignment=assignment,
            )

        obj.pk = assignment.pk
        obj.assigned_at = assignment.assigned_at
        obj.assigned_by = assignment.assigned_by
        obj.is_active = assignment.is_active

    @admin.action(description="Revoke selected staff role assignments")
    def revoke_selected_assignments(self, request, queryset):
        updated = 0
        with transaction.atomic():
            assignments = list(
                queryset.select_for_update()
                .select_related("role")
                .filter(is_active=True)
            )
            for assignment in assignments:
                assignment.revoke(revoked_by=request.user)
                record_staff_role_audit_event(
                    action=AuditEvent.Action.STAFF_ROLE_REVOKED,
                    actor_user=request.user,
                    assignment=assignment,
                )
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
        with transaction.atomic():
            assignments = list(
                queryset.select_for_update()
                .select_related("role")
                .filter(is_active=False)
            )
            for assignment in assignments:
                assignment.reactivate()
                record_staff_role_audit_event(
                    action=AuditEvent.Action.STAFF_ROLE_REACTIVATED,
                    actor_user=request.user,
                    assignment=assignment,
                )
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
