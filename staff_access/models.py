from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
import uuid


class StaffRole(models.Model):
    CRM_ADMIN = "CRM_ADMIN"
    CRM_MANAGER = "CRM_MANAGER"
    CRM_VIEWER = "CRM_VIEWER"

    CANONICAL_ROLES = (
        (CRM_ADMIN, "CRM Administrator"),
        (CRM_MANAGER, "CRM Manager"),
        (CRM_VIEWER, "CRM Viewer"),
    )

    code = models.CharField(max_length=100, unique=True)
    name = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["code"]

    @classmethod
    def canonical_codes(cls):
        return {code for code, _name in cls.CANONICAL_ROLES}

    def __str__(self):
        return f"{self.code} ({self.name})"

    def save(self, *args, **kwargs):
        if self.pk and self.code == self.CRM_ADMIN and not self.is_active:
            from staff_access.services import ensure_staff_role_can_be_deactivated

            ensure_staff_role_can_be_deactivated(self)
        super().save(*args, **kwargs)

    def deactivate(self, *, actor_user=None, request=None):
        from staff_access.services import deactivate_staff_role

        return deactivate_staff_role(self, actor_user=actor_user, request=request)


class StaffRoleAssignmentQuerySet(models.QuerySet):
    def active(self):
        return self.filter(is_active=True, revoked_at__isnull=True, role__is_active=True)


class StaffRoleAssignmentManager(models.Manager):
    def get_queryset(self):
        return StaffRoleAssignmentQuerySet(self.model, using=self._db)

    def active(self):
        return self.get_queryset().active()

    def assign_role(self, *, user, role, assigned_by=None):
        if isinstance(role, str):
            role = StaffRole.objects.get(code=role)

        if user.person.record_type == "COMMUNITY":
            from memberships.models import Membership

            if Membership.objects.filter(person_id=user.person_id).exists():
                raise ValidationError("A Community Person with Membership cannot be provisioned as Staff.")
            raise ValidationError(
                "Staff roles require a separately provisioned STAFF Person and User."
            )

        assignment, created = self.get_or_create(
            user=user,
            role=role,
            defaults={"is_active": True, "assigned_by": assigned_by},
        )
        if not created and not assignment.is_active:
            assignment.reactivate()
        return assignment


class StaffRoleAssignment(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="staff_role_assignments",
    )
    role = models.ForeignKey(
        StaffRole,
        on_delete=models.PROTECT,
        related_name="assignments",
    )
    is_active = models.BooleanField(default=True)
    assigned_at = models.DateTimeField(default=timezone.now)
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="staff_role_assignments_created",
        null=True,
        blank=True,
    )
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="staff_role_assignments_revoked",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = StaffRoleAssignmentManager()

    def clean(self):
        if self.user_id and self.user.person.record_type == "COMMUNITY":
            raise ValidationError({"user": "Community Users cannot receive CRM roles."})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    class Meta:
        ordering = ["user_id", "role__code", "id"]
        constraints = [
            models.UniqueConstraint(fields=["user", "role"], name="staff_access_user_role_unique"),
        ]

    def __str__(self):
        return f"{self.user.email} -> {self.role.code}"

    def revoke(self, *, revoked_by=None, revoked_at=None, request=None):
        from staff_access.services import revoke_staff_role_assignments

        revoked_assignments = revoke_staff_role_assignments(
            assignments=[self],
            revoked_by=revoked_by,
            revoked_at=revoked_at,
            request=request,
        )
        if revoked_assignments:
            self.refresh_from_db()
        return self

    def _revoke_unchecked(self, *, revoked_by=None, revoked_at=None):
        self.is_active = False
        self.revoked_at = revoked_at or timezone.now()
        self.revoked_by = revoked_by
        self.save(update_fields=["is_active", "revoked_at", "revoked_by", "updated_at"])
        return self

    def reactivate(self):
        self.is_active = True
        self.revoked_at = None
        self.revoked_by = None
        self.save(update_fields=["is_active", "revoked_at", "revoked_by", "updated_at"])
        return self


class StaffInvitation(models.Model):
    """Durable, single-use staff account invitation state.

    Only a SHA-256 token digest is stored.  Delivery is intentionally kept at
    the service boundary so this model cannot leak a usable invitation secret.
    """

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    intended_email = models.EmailField()
    first_name = models.CharField(max_length=150)
    last_name = models.CharField(max_length=150)
    pending_role_codes = models.JSONField(default=list)
    token_hash = models.CharField(max_length=64, null=True, blank=True, unique=True)
    expires_at = models.DateTimeField()
    accepted_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    superseded_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="staff_invitations_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["intended_email"],
                condition=(
                    models.Q(accepted_at__isnull=True)
                    & models.Q(revoked_at__isnull=True)
                    & models.Q(superseded_at__isnull=True)
                ),
                name="staff_invitation_one_current_email",
            ),
        ]

    def __str__(self):
        return f"Staff invitation for {self.intended_email}"


class StaffEmailChangeRequest(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="staff_email_change_requests",
    )
    requested_email = models.EmailField()
    token_hash = models.CharField(max_length=64, null=True, blank=True, unique=True)
    expires_at = models.DateTimeField()
    verified_at = models.DateTimeField(null=True, blank=True)
    superseded_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="staff_email_changes_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"Staff email change for {self.user_id}"
