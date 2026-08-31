from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class AuditEventImmutableError(ValidationError):
    pass


class AuditEventQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise AuditEventImmutableError("AuditEvent records are append-only and cannot be updated.")

    def delete(self):
        raise AuditEventImmutableError("AuditEvent records are append-only and cannot be deleted.")


class AuditEventManager(models.Manager.from_queryset(AuditEventQuerySet)):
    pass


class AuditEvent(models.Model):
    class Action(models.TextChoices):
        LOGIN_SUCCEEDED = "LOGIN_SUCCEEDED", "Login succeeded"
        LOGIN_FAILED = "LOGIN_FAILED", "Login failed"
        LOGOUT = "LOGOUT", "Logout"
        PERSON_CREATED = "PERSON_CREATED", "Person created"
        PERSON_UPDATED = "PERSON_UPDATED", "Person updated"
        PERSON_ARCHIVED = "PERSON_ARCHIVED", "Person archived"
        PERSON_RESTORED = "PERSON_RESTORED", "Person restored"
        MEMBERSHIP_CREATED = "MEMBERSHIP_CREATED", "Membership created"
        MEMBERSHIP_ENDED = "MEMBERSHIP_ENDED", "Membership ended"
        PROFESSIONAL_PROFILE_CREATED = "PROFESSIONAL_PROFILE_CREATED", "Professional profile created"
        PROFESSIONAL_PROFILE_UPDATED = "PROFESSIONAL_PROFILE_UPDATED", "Professional profile updated"
        SKILL_ASSIGNED = "SKILL_ASSIGNED", "Skill assigned"
        SKILL_REMOVED = "SKILL_REMOVED", "Skill removed"
        INTEREST_ASSIGNED = "INTEREST_ASSIGNED", "Interest assigned"
        INTEREST_REMOVED = "INTEREST_REMOVED", "Interest removed"
        TAG_ASSIGNED = "TAG_ASSIGNED", "Tag assigned"
        TAG_REACTIVATED = "TAG_REACTIVATED", "Tag reactivated"
        TAG_REMOVED = "TAG_REMOVED", "Tag removed"
        NOTE_CREATED = "NOTE_CREATED", "Note created"
        NOTE_UPDATED = "NOTE_UPDATED", "Note updated"
        NOTE_ARCHIVED = "NOTE_ARCHIVED", "Note archived"
        NOTE_RESTORED = "NOTE_RESTORED", "Note restored"
        STAFF_ROLE_ASSIGNED = "STAFF_ROLE_ASSIGNED", "Staff role assigned"
        STAFF_ROLE_REACTIVATED = "STAFF_ROLE_REACTIVATED", "Staff role reactivated"
        STAFF_ROLE_REVOKED = "STAFF_ROLE_REVOKED", "Staff role revoked"
        ACCOUNT_DISABLED = "ACCOUNT_DISABLED", "Account disabled"
        ACCOUNT_REENABLED = "ACCOUNT_REENABLED", "Account reenabled"
        PASSWORD_CHANGED = "PASSWORD_CHANGED", "Password changed"
        PASSWORD_RESET = "PASSWORD_RESET", "Password reset"

    actor_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="audit_events",
    )
    action = models.CharField(max_length=100, choices=Action.choices, db_index=True)
    entity_type = models.CharField(max_length=100)
    entity_id = models.CharField(max_length=255, null=True, blank=True)
    changes = models.JSONField(default=dict, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    request_id = models.CharField(max_length=255, null=True, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    occurred_at = models.DateTimeField(default=timezone.now, db_index=True)

    objects = AuditEventManager()

    class Meta:
        ordering = ["-occurred_at", "-id"]
        indexes = [
            models.Index(fields=["entity_type", "entity_id"], name="audit_event_entity_idx"),
        ]

    def clean(self):
        errors = {}

        if not self.action:
            errors["action"] = ["Action is required."]
        if not self.entity_type or not self.entity_type.strip():
            errors["entity_type"] = ["Entity type is required."]
        if self.changes is None:
            errors["changes"] = ["Changes must not be null."]
        if self.metadata is None:
            errors["metadata"] = ["Metadata must not be null."]

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if not self._state.adding and self.pk is not None:
            raise AuditEventImmutableError("AuditEvent records are append-only and cannot be updated.")

        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise AuditEventImmutableError("AuditEvent records are append-only and cannot be deleted.")

    def __str__(self):
        return f"{self.action} {self.entity_type} {self.entity_id or '-'}"
