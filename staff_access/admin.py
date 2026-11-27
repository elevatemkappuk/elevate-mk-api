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
    CapabilityDefinition,
    RoleCapabilityDefault,
    StaffCapabilityAssignment,
    StaffCapabilityRestriction,
)
from staff_access.services import (
    activate_staff_role,
    ensure_staff_role_can_be_deactivated,
    revoke_staff_role_assignments,
)
from staff_access.governance import apply_superuser_capability_change
from staff_access.models import CapabilityProposal
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
                return_details=True,
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
                    return_details=True,
                )
                if reactivated:
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


class SuperuserPrivacyAdminMixin:
    """Django Admin boundary for the approved direct superuser exception."""

    def _is_authorized(self, request):
        return bool(request.user.is_authenticated and request.user.is_active and request.user.is_superuser)

    def has_module_permission(self, request):
        return self._is_authorized(request)

    def has_view_permission(self, request, obj=None):
        return self._is_authorized(request)

    def has_add_permission(self, request):
        return self._is_authorized(request)

    def has_change_permission(self, request, obj=None):
        return self._is_authorized(request)

    def has_delete_permission(self, request, obj=None):
        return False

    def get_actions(self, request):
        actions = super().get_actions(request)
        actions.pop("delete_selected", None)
        return actions


@admin.register(RoleCapabilityDefault)
class RoleCapabilityDefaultAdmin(SuperuserPrivacyAdminMixin, admin.ModelAdmin):
    list_display = ("role", "capability", "is_enabled", "updated_at")
    list_filter = ("role", "capability", "is_enabled")
    readonly_fields = ("created_at", "updated_at")

    def get_readonly_fields(self, request, obj=None):
        fields = list(super().get_readonly_fields(request, obj))
        if obj is not None:
            fields.extend(("role", "capability"))
        return tuple(dict.fromkeys(fields))

    def save_model(self, request, obj, form, change):
        previous = RoleCapabilityDefault.objects.filter(pk=obj.pk).first() if change else None
        if previous is not None and (previous.role_id != obj.role_id or previous.capability_id != obj.capability_id):
            raise PermissionDenied("Role-default identity fields cannot be changed in place.")
        if previous is None or previous.is_enabled != obj.is_enabled:
            operation = (
                CapabilityProposal.Operation.ROLE_DEFAULT_ENABLE
                if obj.is_enabled
                else CapabilityProposal.Operation.ROLE_DEFAULT_DISABLE
            )
            apply_superuser_capability_change(
                actor_user=request.user,
                capability=obj.capability,
                operation=operation,
                role=obj.role,
                reason=None,
                request=request,
                allow_empty_reason=True,
            )
            if previous is None:
                obj.pk = RoleCapabilityDefault.objects.get(role=obj.role, capability=obj.capability).pk
        obj.refresh_from_db()


@admin.register(StaffCapabilityAssignment)
class StaffCapabilityAssignmentAdmin(SuperuserPrivacyAdminMixin, admin.ModelAdmin):
    list_display = ("user", "capability", "state", "granted_by", "updated_at")
    list_filter = ("capability", "state")
    readonly_fields = ("granted_at", "granted_by", "suspended_at", "suspended_by", "revoked_at", "revoked_by", "created_at", "updated_at")

    def get_readonly_fields(self, request, obj=None):
        fields = list(super().get_readonly_fields(request, obj))
        if obj is not None:
            fields.extend(("user", "capability"))
        return tuple(dict.fromkeys(fields))

    def save_model(self, request, obj, form, change):
        previous = StaffCapabilityAssignment.objects.filter(pk=obj.pk).first() if change else None
        if previous is not None and (previous.user_id != obj.user_id or previous.capability_id != obj.capability_id):
            raise PermissionDenied("Capability assignment identity fields cannot be changed in place.")
        old_state = None if previous is None else previous.state
        if old_state != obj.state:
            operation = {
                StaffCapabilityAssignment.State.ACTIVE: (
                    CapabilityProposal.Operation.INDIVIDUAL_GRANT
                    if old_state is None
                    else CapabilityProposal.Operation.INDIVIDUAL_RESTORE
                ),
                StaffCapabilityAssignment.State.SUSPENDED: CapabilityProposal.Operation.INDIVIDUAL_SUSPEND,
                StaffCapabilityAssignment.State.REVOKED: CapabilityProposal.Operation.INDIVIDUAL_REVOKE,
            }[obj.state]
            apply_superuser_capability_change(
                actor_user=request.user,
                capability=obj.capability,
                operation=operation,
                target_user=obj.user,
                reason=None,
                request=request,
                allow_empty_reason=True,
            )
            if previous is None:
                obj.pk = StaffCapabilityAssignment.objects.get(user=obj.user, capability=obj.capability).pk
        obj.refresh_from_db()


@admin.register(StaffCapabilityRestriction)
class StaffCapabilityRestrictionAdmin(SuperuserPrivacyAdminMixin, admin.ModelAdmin):
    list_display = ("user", "capability", "is_active", "restricted_by", "updated_at")
    list_filter = ("capability", "is_active")
    readonly_fields = ("restricted_at", "restricted_by", "removed_at", "removed_by", "created_at", "updated_at")

    def get_readonly_fields(self, request, obj=None):
        fields = list(super().get_readonly_fields(request, obj))
        if obj is not None:
            fields.extend(("user", "capability"))
        return tuple(dict.fromkeys(fields))

    def save_model(self, request, obj, form, change):
        previous = StaffCapabilityRestriction.objects.filter(pk=obj.pk).first() if change else None
        if previous is not None and (previous.user_id != obj.user_id or previous.capability_id != obj.capability_id):
            raise PermissionDenied("Capability restriction identity fields cannot be changed in place.")
        if previous is None or previous.is_active != obj.is_active:
            operation = (
                CapabilityProposal.Operation.INDIVIDUAL_RESTRICT
                if obj.is_active
                else CapabilityProposal.Operation.INDIVIDUAL_UNRESTRICT
            )
            apply_superuser_capability_change(
                actor_user=request.user,
                capability=obj.capability,
                operation=operation,
                target_user=obj.user,
                reason=None,
                request=request,
                allow_empty_reason=True,
            )
            if previous is None:
                obj.pk = StaffCapabilityRestriction.objects.get(user=obj.user, capability=obj.capability).pk
        obj.refresh_from_db()
