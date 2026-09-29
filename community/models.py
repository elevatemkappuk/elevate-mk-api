from django.db import models


class JoinSubmissionReceipt(models.Model):
    class Status(models.TextChoices):
        ACCEPTED = "ACCEPTED", "Accepted"

    key_hash = models.CharField(max_length=64, unique=True)
    request_digest = models.CharField(max_length=64)
    status = models.CharField(max_length=20, choices=Status.choices)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]

