from django.contrib import admin
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils.translation import ngettext

from audit.models import AuditEvent
from audit.services import record_audit_event
from staff_access.exceptions import FinalCrmAdminProtectionError
from staff_access.models import (
    CAPABILITY_POLICY_STATE_KEY,
    CapabilityPolicyState,
    StaffInvitation,
    StaffRole,
    StaffRoleAssignment,
)
from staff_access.services import (
    activate_staff_role,
    ensure_staff_role_can_be_deactivated,
    revoke_staff_role_assignments,
)
from staff_access.permissions import user_has_any_active_staff_role


def can_manage_staff_access(user):
    return bool(
        getattr(user, "is_superuser", False)
        or user_has_any_active_staff_role(user, (StaffRole.CRM_ADMIN,))
    )


def record_staff_role_audit_event(*, action, actor_user, assignment, request=None, metadata_extra=None):
    request_id = None
    ip_address = None
    if request is not None:
        request_id = request.headers.get("X-Request-ID") or request.META.get("HTTP_X_REQUEST_ID")
        ip_address = request.META.get("REMOTE_ADDR")
    record_audit_event(
        action=action,
        actor_user=actor_user,
        entity_type="StaffRoleAssignment",
        entity_id=assignment.id,
        changes={
            "is_active": {
                "from": (
                    None
                    if action == AuditEvent.Action.STAFF_ROLE_ASSIGNED
                    else action == AuditEvent.Action.STAFF_ROLE_REVOKED
                ),
                "to": False if action == AuditEvent.Action.STAFF_ROLE_REVOKED else True,
            }
        },
        metadata={
            "target_user_id": str(assignment.user_id),
            "staff_role_id": str(assignment.role_id),
            "staff_role_code": assignment.role.code,
            **(metadata_extra or {}),
        },
        request_id=request_id,
        ip_address=ip_address,
    )


@admin.register(StaffRole)
class StaffRoleAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "is_active")
    search_fields = ("code", "name")
    list_filter = ("is_active",)
    readonly_fields = ("created_at", "updated_at")

    def get_form(self, request, obj=None, change=False, **kwargs):
        base_form = super().get_form(request, obj, change, **kwargs)

        class StaffRoleAdminForm(base_form):
            def clean(self):
                cleaned_data = super().clean()
                if (
                    self.instance.pk
                    and self.instance.code == StaffRole.CRM_ADMIN
                    and cleaned_data.get("is_active") is False
                ):
                    try:
                        ensure_staff_role_can_be_deactivated(self.instance)
                    except FinalCrmAdminProtectionError as error:
                        self.add_error("is_active", error)
                return cleaned_data

        return StaffRoleAdminForm

    def get_fields(self, request, obj=None):
        return ("code", "name", "is_active", "created_at", "updated_at")

    def get_readonly_fields(self, request, obj=None):
        readonly_fields = list(super().get_readonly_fields(request, obj))
        if obj and obj.code in StaffRole.canonical_codes():
            readonly_fields.extend(["code", "name"])
        return readonly_fields

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        if change and obj.pk:
            was_active = StaffRole.objects.filter(pk=obj.pk).values_list("is_active", flat=True).first()
            if was_active and not obj.is_active:
                obj.deactivate(actor_user=request.user, request=request)
                obj.refresh_from_db()
                return
            if was_active is False and obj.is_active:
                activate_staff_role(obj, actor_user=request.user)
                obj.refresh_from_db()
                return

        super().save_model(request, obj, form, change)

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

    def has_add_permission(self, request):
        return can_manage_staff_access(request.user)

    def has_change_permission(self, request, obj=None):
        return can_manage_staff_access(request.user)

    def has_delete_permission(self, request, obj=None):
        return False

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
            raise PermissionDenied("Existing Staff role assignments must be changed through lifecycle actions.")

        if obj.user_id == request.user.pk and not getattr(request.user, "is_superuser", False):
            raise PermissionDenied("Staff role assignments cannot target the acting user.")

        with transaction.atomic():
            assignment, created, reactivated, policy_version = StaffRoleAssignment.objects.assign_role(
                user=obj.user,
                role=obj.role,
                assigned_by=request.user,
                audit=False,
                return_details=True,
            )
            if created or reactivated:
                action = AuditEvent.Action.STAFF_ROLE_REACTIVATED if reactivated else AuditEvent.Action.STAFF_ROLE_ASSIGNED
                record_staff_role_audit_event(
                    action=action,
                    actor_user=request.user,
                    assignment=assignment,
                    request=request,
                    metadata_extra={"policy_version": policy_version} if policy_version is not None else None,
                )

        obj.pk = assignment.pk
        obj.assigned_at = assignment.assigned_at
        obj.assigned_by = assignment.assigned_by
        obj.is_active = assignment.is_active

    @admin.action(description="Revoke selected staff role assignments")
    def revoke_selected_assignments(self, request, queryset):
        try:
            with transaction.atomic():
                assignments = revoke_staff_role_assignments(
                    assignments=queryset,
                    revoked_by=request.user,
                    request=request,
                    audit=False,
                )
                policy_state = CapabilityPolicyState.objects.filter(
                    singleton_key=1,
                    key=CAPABILITY_POLICY_STATE_KEY,
                ).first()
                for assignment in assignments:
                    record_staff_role_audit_event(
                        action=AuditEvent.Action.STAFF_ROLE_REVOKED,
                        actor_user=request.user,
                        assignment=assignment,
                        request=request,
                        metadata_extra=(
                            {"policy_version": policy_state.version}
                            if policy_state is not None
                            else None
                        ),
                    )
        except FinalCrmAdminProtectionError as error:
            self.message_user(request, error.messages[0], level=messages.ERROR)
            return

        updated = len(assignments)
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
                queryset
                .select_related("role")
                .filter(is_active=False)
            )
            if any(assignment.user_id == request.user.pk for assignment in assignments) and not getattr(request.user, "is_superuser", False):
                raise PermissionDenied("Staff role assignments cannot target the acting user.")
            for assignment in assignments:
                assignment, created, reactivated, policy_version = StaffRoleAssignment.objects.assign_role(
                    user=assignment.user,
                    role=assignment.role,
                    assigned_by=request.user,
                    audit=False,
                    return_details=True,
                )
                if reactivated:
                    record_staff_role_audit_event(
                        action=AuditEvent.Action.STAFF_ROLE_REACTIVATED,
                        actor_user=request.user,
                        assignment=assignment,
                        request=request,
                        metadata_extra={"policy_version": policy_version} if policy_version is not None else None,
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


@admin.register(StaffInvitation)
class StaffInvitationAdmin(admin.ModelAdmin):
    list_display = ("intended_email", "pending_role_codes", "expires_at", "accepted_at", "revoked_at", "superseded_at")
    search_fields = ("intended_email", "first_name", "last_name")
    readonly_fields = (
        "public_id", "intended_email", "first_name", "last_name", "pending_role_codes",
        "token_hash", "expires_at", "accepted_at", "revoked_at", "superseded_at",
        "created_by", "created_at", "updated_at",
    )

    def has_add_permission(self, request):
        return can_manage_staff_access(request.user)

    def has_change_permission(self, request, obj=None):
        return can_manage_staff_access(request.user)

    def has_delete_permission(self, request, obj=None):
        return False
