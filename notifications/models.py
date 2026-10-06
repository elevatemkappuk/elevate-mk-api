from django.db import models
from django.utils import timezone


class TransactionalEmailJob(models.Model):
    """Durable non-secret delivery work item for transactional email."""

    class JobType(models.TextChoices):
        COMMUNITY_ACTIVATION = "COMMUNITY_ACTIVATION", "Community activation"
        COMMUNITY_EMAIL_CHANGE = "COMMUNITY_EMAIL_CHANGE", "Community email change verification"

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        PROCESSING = "PROCESSING", "Processing"
        SENT = "SENT", "Sent"
        FAILED = "FAILED", "Failed"
        DELIVERY_UNCERTAIN = "DELIVERY_UNCERTAIN", "Delivery uncertain"
        CANCELLED = "CANCELLED", "Cancelled"

    invitation = models.OneToOneField(
        "community.CommunityAccountInvitation",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="transactional_email_job",
    )
    email_change_request = models.OneToOneField(
        "community.CommunityEmailChangeRequest",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="transactional_email_job",
    )
    template_id = models.CharField(max_length=100)
    recipient_email = models.EmailField()
    recipient_name = models.CharField(max_length=255, blank=True)
    first_name = models.CharField(max_length=150)
    expires_in_hours = models.PositiveIntegerField()
    expires_in_minutes = models.PositiveIntegerField(null=True, blank=True)
    job_type = models.CharField(max_length=50, choices=JobType.choices, default=JobType.COMMUNITY_ACTIVATION)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    available_at = models.DateTimeField(default=timezone.now)
    attempts = models.PositiveIntegerField(default=0)
    max_attempts = models.PositiveIntegerField(default=5)
    locked_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    provider_message_id = models.CharField(max_length=255, blank=True)
    last_error_code = models.CharField(max_length=100, blank=True)
    last_error_message = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["available_at", "id"]
        indexes = [
            models.Index(fields=["status", "available_at"], name="tx_email_job_ready_idx"),
            models.Index(fields=["job_type", "status"], name="tx_email_job_type_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(invitation__isnull=False, email_change_request__isnull=True)
                    | models.Q(invitation__isnull=True, email_change_request__isnull=False)
                ),
                name="transactional_email_job_single_resource",
            ),
        ]

    def __str__(self):
        return f"{self.job_type} #{self.pk} ({self.status})"
