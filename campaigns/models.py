from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from people.models import Person


class Campaign(models.Model):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        PREPARING = "PREPARING", "Preparing"
        SNAPSHOT_READY = "SNAPSHOT_READY", "CRM snapshot ready"
        PREPARED = "PREPARED", "Prepared"
        RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED", "Reconciliation required"
        PROVIDER_FAILED = "PROVIDER_FAILED", "Provider failed"
        NO_READY_RECIPIENTS = "NO_READY_RECIPIENTS", "No ready recipients"

    name = models.CharField(max_length=255)
    status = models.CharField(max_length=40, choices=Status.choices, default=Status.DRAFT, db_index=True)
    audience_selection = models.JSONField(default=dict)
    audience_ordering = models.CharField(max_length=40, default="last_name")
    audience_schema_version = models.PositiveSmallIntegerField(default=1)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_campaigns")
    current_preparation = models.ForeignKey(
        "CampaignPreparation",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="current_for_campaigns",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.name} (#{self.pk})"


class CampaignPreparation(models.Model):
    class Status(models.TextChoices):
        PREPARING = "PREPARING", "Preparing"
        SNAPSHOT_READY = "SNAPSHOT_READY", "CRM snapshot ready"
        FAILED = "FAILED", "Failed"
        PROVIDER_PREPARING = "PROVIDER_PREPARING", "Provider preparing"
        PREPARED = "PREPARED", "Prepared"
        RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED", "Reconciliation required"
        PROVIDER_FAILED = "PROVIDER_FAILED", "Provider failed"
        NO_READY_RECIPIENTS = "NO_READY_RECIPIENTS", "No ready recipients"

    campaign = models.ForeignKey(Campaign, on_delete=models.PROTECT, related_name="preparations")
    attempt_number = models.PositiveIntegerField()
    status = models.CharField(max_length=30, choices=Status.choices, default=Status.PREPARING, db_index=True)
    started_at = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(null=True, blank=True)
    selected_count = models.PositiveIntegerField(default=0)
    included_count = models.PositiveIntegerField(default=0)
    excluded_count = models.PositiveIntegerField(default=0)
    brevo_list_id = models.PositiveBigIntegerField(null=True, blank=True)
    brevo_campaign_id = models.PositiveBigIntegerField(null=True, blank=True)
    brevo_editor_url = models.URLField(max_length=1000, null=True, blank=True)
    provider_error_code = models.CharField(max_length=100, null=True, blank=True)
    provider_error_message = models.CharField(max_length=500, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-attempt_number", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["campaign", "attempt_number"], name="campaign_prep_campaign_attempt_unique"),
        ]

    def __str__(self):
        return f"Campaign {self.campaign_id} preparation {self.attempt_number} ({self.status})"


class CampaignRecipientSnapshot(models.Model):
    class Decision(models.TextChoices):
        INCLUDED = "INCLUDED", "Included"
        EXCLUDED = "EXCLUDED", "Excluded"

    preparation = models.ForeignKey(CampaignPreparation, on_delete=models.PROTECT, related_name="recipient_snapshots")
    person = models.ForeignKey(Person, on_delete=models.PROTECT, related_name="campaign_recipient_snapshots")
    email_snapshot = models.EmailField(max_length=254, blank=True)
    first_name_snapshot = models.CharField(max_length=150)
    last_name_snapshot = models.CharField(max_length=150)
    consent_state_snapshot = models.CharField(max_length=20)
    decision = models.CharField(max_length=20, choices=Decision.choices)
    exclusion_reason = models.CharField(max_length=50, null=True, blank=True)
    captured_at = models.DateTimeField(default=timezone.now)
    brevo_contact_id = models.PositiveBigIntegerField(null=True, blank=True)
    provider_outcome = models.CharField(max_length=100, null=True, blank=True)
    provider_error_code = models.CharField(max_length=100, null=True, blank=True)
    provider_error_message = models.CharField(max_length=500, null=True, blank=True)

    class Meta:
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(fields=["preparation", "person"], name="campaign_snapshot_preparation_person_unique"),
        ]
        indexes = [
            models.Index(fields=["preparation", "decision"], name="camp_snap_prep_decision_idx"),
        ]

    IMMUTABLE_FIELDS = (
        "preparation_id", "person_id", "email_snapshot", "first_name_snapshot",
        "last_name_snapshot", "consent_state_snapshot", "decision", "exclusion_reason", "captured_at",
    )

    def save(self, *args, **kwargs):
        if self.pk:
            previous = type(self).objects.filter(pk=self.pk).values(*self.IMMUTABLE_FIELDS).first()
            if previous:
                changed = [field for field in self.IMMUTABLE_FIELDS if previous[field] != getattr(self, field)]
                if changed:
                    raise ValidationError("Campaign recipient snapshot evidence is immutable.")
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Campaign recipient snapshots are immutable and cannot be deleted.")

    def __str__(self):
        return f"Campaign preparation {self.preparation_id}, Person {self.person_id} ({self.decision})"
