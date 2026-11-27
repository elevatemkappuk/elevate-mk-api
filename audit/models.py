from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
import uuid


AUDIT_CONTRACT_VERSION = 1


class AuditEventImmutableError(ValidationError):
    pass


class AuditEventQuerySet(models.QuerySet):
    def operational(self):
        return self.filter(audit_domain=AuditEvent.Domain.OPERATIONAL)

    def privacy(self):
        return self.filter(audit_domain=AuditEvent.Domain.PRIVACY)

    def update(self, **kwargs):
        raise AuditEventImmutableError("AuditEvent records are append-only and cannot be updated.")

    def delete(self):
        raise AuditEventImmutableError("AuditEvent records are append-only and cannot be deleted.")


class AuditEventManager(models.Manager.from_queryset(AuditEventQuerySet)):
    pass


class AuditEventSubject(models.Model):
    class SubjectType(models.TextChoices):
        PERSON = "PERSON", "Person"

    event = models.ForeignKey("audit.AuditEvent", on_delete=models.PROTECT, related_name="subjects")
    subject_type = models.CharField(max_length=20, choices=SubjectType.choices, default=SubjectType.PERSON)
    person = models.ForeignKey(
        "people.Person",
        on_delete=models.PROTECT,
        related_name="audit_subjects",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["subject_type", "person_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["event", "subject_type", "person"],
                name="audit_event_subject_unique",
            ),
        ]
        indexes = [
            models.Index(fields=["subject_type", "person", "event"], name="audit_subject_person_event_idx"),
            models.Index(fields=["event", "person"], name="audit_subject_event_person_idx"),
        ]

    def clean(self):
        if self.subject_type != self.SubjectType.PERSON or not self.person_id or not self.event_id:
            raise ValidationError("Only normalized Person audit subjects are supported.")

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class AuditEvent(models.Model):
    CURRENT_CONTRACT_VERSION = AUDIT_CONTRACT_VERSION

    class Domain(models.TextChoices):
        OPERATIONAL = "OPERATIONAL", "Operational"
        PRIVACY = "PRIVACY", "Privacy"

    class ProvenanceSource(models.TextChoices):
        LEGACY = "LEGACY", "Legacy event"
        HTTP_REQUEST = "HTTP_REQUEST", "HTTP request"
        BACKGROUND_JOB = "BACKGROUND_JOB", "Background job"
        SYSTEM = "SYSTEM", "System operation"
        MIGRATION = "MIGRATION", "Migration"

    class Action(models.TextChoices):
        LOGIN_SUCCEEDED = "LOGIN_SUCCEEDED", "Login succeeded"
        LOGIN_FAILED = "LOGIN_FAILED", "Login failed"
        LOGOUT = "LOGOUT", "Logout"
        SESSION_EXPIRED = "SESSION_EXPIRED", "Session expired"
        CREDENTIALS_REVOKED = "CREDENTIALS_REVOKED", "Credentials revoked"
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
        COMMUNITY_EMAIL_CHANGE_REQUESTED = "COMMUNITY_EMAIL_CHANGE_REQUESTED", "Community email change requested"
        COMMUNITY_EMAIL_CHANGED = "COMMUNITY_EMAIL_CHANGED", "Community email changed"
        COMMUNITY_ACCOUNT_ACTIVATED = "COMMUNITY_ACCOUNT_ACTIVATED", "Community account activated"
        CONNECTION_REQUESTED = "CONNECTION_REQUESTED", "Connection requested"
        CONNECTION_ACCEPTED = "CONNECTION_ACCEPTED", "Connection accepted"
        CONNECTION_DECLINED = "CONNECTION_DECLINED", "Connection declined"
        CONNECTION_REMOVED = "CONNECTION_REMOVED", "Connection removed"
        COMMUNITY_POST_CREATED = "COMMUNITY_POST_CREATED", "Community post created"
        COMMUNITY_POST_EDITED = "COMMUNITY_POST_EDITED", "Community post edited"
        COMMUNITY_POST_DELETED = "COMMUNITY_POST_DELETED", "Community post deleted"
        COMMUNITY_REPLY_CREATED = "COMMUNITY_REPLY_CREATED", "Community reply created"
        COMMUNITY_REPLY_EDITED = "COMMUNITY_REPLY_EDITED", "Community reply edited"
        COMMUNITY_REPLY_DELETED = "COMMUNITY_REPLY_DELETED", "Community reply deleted"
        COMMUNITY_CONTENT_REPORTED = "COMMUNITY_CONTENT_REPORTED", "Community content reported"
        COMMUNITY_MODERATION_DISMISSED = "COMMUNITY_MODERATION_DISMISSED", "Community moderation dismissed"
        COMMUNITY_CONTENT_REMOVED = "COMMUNITY_CONTENT_REMOVED", "Community content removed"
        COMMUNITY_CONTENT_RESTORED = "COMMUNITY_CONTENT_RESTORED", "Community content restored"
        IMPORT_RECORD_MATCH_CONFIRMED = "IMPORT_RECORD_MATCH_CONFIRMED", "Import record match confirmed"
        IMPORT_RECORD_CREATE_NEW_CONFIRMED = "IMPORT_RECORD_CREATE_NEW_CONFIRMED", "Import record create-new confirmed"
        IMPORT_BATCH_IMPORTED = "IMPORT_BATCH_IMPORTED", "Import batch imported"
        EXTERNAL_PERSON_REFERENCE_LINKED = "EXTERNAL_PERSON_REFERENCE_LINKED", "External person reference linked"
        EXTERNAL_PERSON_REFERENCE_REACTIVATED = "EXTERNAL_PERSON_REFERENCE_REACTIVATED", "External person reference reactivated"
        EXTERNAL_PERSON_REFERENCE_REVOKED = "EXTERNAL_PERSON_REFERENCE_REVOKED", "External person reference revoked"
        MARKETING_PREFERENCE_OPTED_IN = "MARKETING_PREFERENCE_OPTED_IN", "Marketing preference opted in"
        MARKETING_PREFERENCE_OPTED_OUT = "MARKETING_PREFERENCE_OPTED_OUT", "Marketing preference opted out"
        CAMPAIGN_CREATED = "CAMPAIGN_CREATED", "Campaign created"
        CAMPAIGN_ARCHIVED = "CAMPAIGN_ARCHIVED", "Campaign archived"
        CAMPAIGN_RESTORED = "CAMPAIGN_RESTORED", "Campaign restored"
        CAMPAIGN_DELETED = "CAMPAIGN_DELETED", "Campaign deleted"
        CAMPAIGN_PREPARATION_STARTED = "CAMPAIGN_PREPARATION_STARTED", "Campaign preparation started"
        CAMPAIGN_RECIPIENT_SNAPSHOT_CREATED = "CAMPAIGN_RECIPIENT_SNAPSHOT_CREATED", "Campaign recipient snapshot created"
        CAMPAIGN_PREPARATION_SNAPSHOT_READY = "CAMPAIGN_PREPARATION_SNAPSHOT_READY", "Campaign preparation snapshot ready"
        CAMPAIGN_PROVIDER_PREPARATION_STARTED = "CAMPAIGN_PROVIDER_PREPARATION_STARTED", "Campaign provider preparation started"
        CAMPAIGN_BREVO_LIST_CREATED = "CAMPAIGN_BREVO_LIST_CREATED", "Campaign Brevo list created"
        CAMPAIGN_LIST_POPULATED = "CAMPAIGN_LIST_POPULATED", "Campaign list populated"
        CAMPAIGN_BREVO_DRAFT_CREATED = "CAMPAIGN_BREVO_DRAFT_CREATED", "Campaign Brevo draft created"
        CAMPAIGN_PROVIDER_PREPARATION_COMPLETED = "CAMPAIGN_PROVIDER_PREPARATION_COMPLETED", "Campaign provider preparation completed"
        CAMPAIGN_RECONCILIATION_REQUIRED = "CAMPAIGN_RECONCILIATION_REQUIRED", "Campaign reconciliation required"
        CAMPAIGN_PROVIDER_FAILURE = "CAMPAIGN_PROVIDER_FAILURE", "Campaign provider failure"
        CAMPAIGN_PROVIDER_RETRY = "CAMPAIGN_PROVIDER_RETRY", "Campaign provider retry"
        PRIVACY_SUPERUSER_BYPASS = "PRIVACY_SUPERUSER_BYPASS", "Privacy capability superuser bypass"
        PRIVACY_PERSON_DISCLOSED = "PRIVACY_PERSON_DISCLOSED", "Protected Person data disclosed"
        PERSON_EXPORT_GENERATED = "PERSON_EXPORT_GENERATED", "Person data export generated"
        PERSON_EXPORT_DENIED = "PERSON_EXPORT_DENIED", "Person data export denied"
        CAPABILITY_PROPOSAL_CREATED = "CAPABILITY_PROPOSAL_CREATED", "Capability proposal created"
        CAPABILITY_PROPOSAL_APPROVED = "CAPABILITY_PROPOSAL_APPROVED", "Capability proposal approved"
        CAPABILITY_PROPOSAL_REJECTED = "CAPABILITY_PROPOSAL_REJECTED", "Capability proposal rejected"
        CAPABILITY_PROPOSAL_CANCELLED = "CAPABILITY_PROPOSAL_CANCELLED", "Capability proposal cancelled"
        CAPABILITY_PROPOSAL_EXPIRED = "CAPABILITY_PROPOSAL_EXPIRED", "Capability proposal expired"
        CAPABILITY_PROPOSAL_INVALIDATED = "CAPABILITY_PROPOSAL_INVALIDATED", "Capability proposal invalidated"
        CAPABILITY_PERMISSION_MUTATED = "CAPABILITY_PERMISSION_MUTATED", "Capability permission mutated"
        CAPABILITY_ASSIGNMENT_SUSPENDED = "CAPABILITY_ASSIGNMENT_SUSPENDED", "Capability assignment suspended"
        CAPABILITY_ASSIGNMENT_RESTORED = "CAPABILITY_ASSIGNMENT_RESTORED", "Capability assignment restored"
        CAPABILITY_EMERGENCY_REVOKED = "CAPABILITY_EMERGENCY_REVOKED", "Capability emergency revocation"
        CAPABILITY_EMERGENCY_RESTRICTED = "CAPABILITY_EMERGENCY_RESTRICTED", "Capability emergency restriction"
        CAPABILITY_EMERGENCY_SUSPENDED = "CAPABILITY_EMERGENCY_SUSPENDED", "Capability emergency suspension"
        CAPABILITY_RECOVERY_APPROVED = "CAPABILITY_RECOVERY_APPROVED", "Capability controlled recovery approved"
        CAPABILITY_POLICY_BASELINED = "CAPABILITY_POLICY_BASELINED", "Capability policy baseline captured"
        CAPABILITY_CATALOG_EXTENDED = "CAPABILITY_CATALOG_EXTENDED", "Capability catalogue extended"
        RETENTION_POLICY_VERSION_CREATED = "RETENTION_POLICY_VERSION_CREATED", "Retention policy version created"
        LEGAL_HOLD_PLACED = "LEGAL_HOLD_PLACED", "Legal hold placed"
        LEGAL_HOLD_RELEASE_REQUESTED = "LEGAL_HOLD_RELEASE_REQUESTED", "Legal hold release requested"
        LEGAL_HOLD_RELEASE_CANCELLED = "LEGAL_HOLD_RELEASE_CANCELLED", "Legal hold release request cancelled"
        LEGAL_HOLD_RELEASED = "LEGAL_HOLD_RELEASED", "Legal hold released"

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
    audit_domain = models.CharField(
        max_length=20,
        choices=Domain.choices,
        default=Domain.OPERATIONAL,
        db_index=True,
    )
    contract_version = models.PositiveSmallIntegerField(default=AUDIT_CONTRACT_VERSION, db_index=True)
    provenance_source = models.CharField(
        max_length=32,
        choices=ProvenanceSource.choices,
        default=ProvenanceSource.LEGACY,
        db_index=True,
    )
    provenance_id = models.CharField(max_length=64, null=True, blank=True, editable=False)
    provenance_trusted = models.BooleanField(default=False, editable=False)
    retention_class = models.CharField(max_length=40, null=True, blank=True, db_index=True)
    retention_policy_version = models.PositiveBigIntegerField(null=True, blank=True, db_index=True)
    occurred_at = models.DateTimeField(default=timezone.now, db_index=True)

    objects = AuditEventManager()

    class Meta:
        ordering = ["-occurred_at", "-id"]
        indexes = [
            models.Index(fields=["entity_type", "entity_id"], name="audit_event_entity_idx"),
            models.Index(fields=["audit_domain", "occurred_at"], name="audit_event_domain_time_idx"),
            models.Index(fields=["retention_class", "retention_policy_version", "occurred_at"], name="audit_event_retention_idx"),
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
        if self.audit_domain not in self.Domain.values:
            errors["audit_domain"] = ["Unsupported audit domain."]
        if self.provenance_source not in self.ProvenanceSource.values:
            errors["provenance_source"] = ["Unsupported provenance source."]
        if self.contract_version < 1:
            errors["contract_version"] = ["Contract version must be positive."]

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


class ImmutableRetentionQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError("Retention and legal-hold history is managed by services.")

    def delete(self):
        raise ValidationError("Retention and legal-hold history is append-only.")


class RetentionPolicyState(models.Model):
    singleton_key = models.PositiveSmallIntegerField(default=1, unique=True, editable=False)
    version = models.PositiveBigIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="retention_policy_state_updates",
    )
    reason = models.TextField(blank=True)

    class Meta:
        ordering = ["singleton_key"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(singleton_key=1),
                name="audit_retention_state_singleton_key",
            ),
        ]


class RetentionPolicyVersion(models.Model):
    audit_domain = models.CharField(max_length=20, choices=AuditEvent.Domain.choices)
    retention_class = models.CharField(max_length=40)
    version = models.PositiveBigIntegerField()
    retention_days = models.PositiveIntegerField(null=True, blank=True)
    effective_at = models.DateTimeField()
    reason = models.TextField()
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="retention_policy_versions_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    objects = ImmutableRetentionQuerySet.as_manager()

    class Meta:
        ordering = ["-version"]
        constraints = [
            models.UniqueConstraint(
                fields=["audit_domain", "retention_class", "version"],
                name="audit_retention_domain_version_unique",
            ),
            models.CheckConstraint(
                condition=models.Q(retention_days__isnull=True) | models.Q(retention_days__gt=0),
                name="audit_retention_days_positive_or_null",
            ),
        ]
        indexes = [
            models.Index(
                fields=["audit_domain", "retention_class", "effective_at"],
                name="audit_ret_domain_time_idx",
            ),
        ]

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValidationError("Retention policy versions are immutable.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Retention policy versions are immutable.")


class LegalHold(models.Model):
    class Scope(models.TextChoices):
        EVENT = "EVENT", "Audit event"
        PERSON = "PERSON", "Person"

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        RELEASED = "RELEASED", "Released"

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    scope = models.CharField(max_length=20, choices=Scope.choices)
    person = models.ForeignKey(
        "people.Person",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="legal_holds",
    )
    audit_event = models.ForeignKey(
        AuditEvent,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="legal_holds",
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    reason = models.TextField()
    placed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="legal_holds_placed",
    )
    placed_at = models.DateTimeField(default=timezone.now)
    released_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="legal_holds_released",
    )
    released_at = models.DateTimeField(null=True, blank=True)
    release_reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = ImmutableRetentionQuerySet.as_manager()

    class Meta:
        ordering = ["-placed_at", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(scope="PERSON", person__isnull=False, audit_event__isnull=True)
                    | models.Q(scope="EVENT", person__isnull=True, audit_event__isnull=False)
                ),
                name="audit_legal_hold_one_target",
            ),
        ]
        indexes = [
            models.Index(fields=["person", "status"], name="audit_legal_hold_person_idx"),
            models.Index(fields=["audit_event", "status"], name="audit_legal_hold_event_idx"),
        ]

    def save(self, *args, **kwargs):
        if self.pk is not None:
            previous = type(self).objects.get(pk=self.pk)
            immutable_fields = (
                "public_id", "scope", "person_id", "audit_event_id", "reason", "placed_by_id", "placed_at", "created_at"
            )
            changed = [field for field in immutable_fields if getattr(previous, field) != getattr(self, field)]
            if changed:
                raise ValidationError("Legal hold fields are immutable: " + ", ".join(changed))
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Legal holds are retained as history and cannot be deleted.")


class LegalHoldReleaseRequest(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        APPROVED = "APPROVED", "Approved"
        REJECTED = "REJECTED", "Rejected"
        CANCELLED = "CANCELLED", "Cancelled"

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    legal_hold = models.ForeignKey(
        LegalHold,
        on_delete=models.PROTECT,
        related_name="release_requests",
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    reason = models.TextField()
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="legal_hold_release_requests",
    )
    requested_at = models.DateTimeField(default=timezone.now)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="legal_hold_release_approvals",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    approval_reason = models.TextField(blank=True)
    rejected_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="legal_hold_release_rejections",
    )
    rejected_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True)
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="legal_hold_release_cancellations",
    )
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancellation_reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = ImmutableRetentionQuerySet.as_manager()

    class Meta:
        ordering = ["-requested_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["legal_hold"],
                condition=models.Q(status="PENDING"),
                name="audit_one_pending_hold_release",
            ),
        ]
        indexes = [
            models.Index(fields=["legal_hold", "status"], name="audit_hold_release_status_idx"),
        ]

    IMMUTABLE_FIELDS = (
        "public_id",
        "legal_hold_id",
        "reason",
        "requested_by_id",
        "requested_at",
        "created_at",
    )

    def save(self, *args, **kwargs):
        if self.pk is not None:
            previous = type(self).objects.get(pk=self.pk)
            changed = [field for field in self.IMMUTABLE_FIELDS if getattr(previous, field) != getattr(self, field)]
            if changed:
                raise ValidationError("Legal hold release fields are immutable: " + ", ".join(changed))
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Legal hold release history cannot be deleted.")
