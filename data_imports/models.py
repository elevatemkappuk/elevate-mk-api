from django.conf import settings
from django.db import models
from django.utils import timezone


class ImportBatch(models.Model):
    class SourceType(models.TextChoices):
        MEMBERSHIP_FORM = "MEMBERSHIP_FORM", "Membership Form"
        EVENTBRITE = "EVENTBRITE", "Eventbrite"

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        PROCESSING = "PROCESSING", "Processing"
        READY_FOR_REVIEW = "READY_FOR_REVIEW", "Ready for review"
        COMMITTING = "COMMITTING", "Committing"
        COMPLETED = "COMPLETED", "Completed"
        FAILED = "FAILED", "Failed"

    source_type = models.CharField(max_length=32, choices=SourceType.choices)
    source_filename = models.CharField(max_length=255)
    source_fingerprint = models.CharField(max_length=64, db_index=True)
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.PENDING)
    started_at = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="import_batches_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["source_type", "status"], name="import_batch_source_status_idx")]

    def __str__(self):
        return f"{self.source_type}: {self.source_filename} ({self.pk})"


class ImportRecord(models.Model):
    class Status(models.TextChoices):
        STAGED = "STAGED", "Staged"
        INVALID = "INVALID", "Invalid"
        ANALYZED = "ANALYZED", "Analyzed"
        REVIEW_REQUIRED = "REVIEW_REQUIRED", "Review required"
        RESOLVED = "RESOLVED", "Resolved"
        COMMITTED = "COMMITTED", "Committed"
        SKIPPED = "SKIPPED", "Skipped"
        FAILED = "FAILED", "Failed"

    class Outcome(models.TextChoices):
        CREATED = "CREATED", "Created"
        MATCHED = "MATCHED", "Matched"
        UPDATED = "UPDATED", "Updated"
        SKIPPED = "SKIPPED", "Skipped"

    class ResolutionMethod(models.TextChoices):
        AUTO_MATCH = "AUTO_MATCH", "Automatic match"
        STAFF_MATCH = "STAFF_MATCH", "Staff match"
        STAFF_CREATE_NEW = "STAFF_CREATE_NEW", "Staff create new"
        NO_MATCH = "NO_MATCH", "No match"
        NOT_RESOLVED = "NOT_RESOLVED", "Not resolved"

    batch = models.ForeignKey(ImportBatch, on_delete=models.PROTECT, related_name="records")
    source_row_identifier = models.CharField(max_length=255)
    source_fingerprint = models.CharField(max_length=64, db_index=True)
    raw_data = models.JSONField(default=dict)
    normalized_data = models.JSONField(default=dict)
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.STAGED)
    outcome = models.CharField(max_length=32, choices=Outcome.choices, null=True, blank=True)
    resolved_person = models.ForeignKey(
        "people.Person", on_delete=models.SET_NULL, null=True, blank=True, related_name="import_records_resolved"
    )
    resolution_method = models.CharField(max_length=32, choices=ResolutionMethod.choices, null=True, blank=True)
    resolution_reason = models.TextField(null=True, blank=True)
    match_candidates = models.JSONField(default=list, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="import_records_reviewed"
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    committed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["batch_id", "source_row_identifier", "id"]
        constraints = [
            models.UniqueConstraint(fields=["batch", "source_row_identifier"], name="import_record_batch_row_identifier_unique"),
        ]
        indexes = [
            models.Index(fields=["batch", "status"], name="import_record_batch_status_idx"),
        ]

    def __str__(self):
        return f"Batch {self.batch_id} row {self.source_row_identifier}"
