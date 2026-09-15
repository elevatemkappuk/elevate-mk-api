from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from people.models import Person


class ExternalPersonReference(models.Model):
    """Provider-neutral identity link from a BUSINESS Person to an external person record."""

    class ReferenceType(models.TextChoices):
        MARKETING_CONTACT = "MARKETING_CONTACT", "Marketing contact"

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        REVOKED = "REVOKED", "Revoked"

    person = models.ForeignKey(
        Person,
        on_delete=models.PROTECT,
        related_name="external_person_references",
    )
    provider = models.CharField(max_length=100)
    reference_type = models.CharField(max_length=40, choices=ReferenceType.choices)
    external_id = models.CharField(max_length=255)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    linked_at = models.DateTimeField(default=timezone.now)
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["provider", "reference_type", "external_id", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "reference_type", "external_id"],
                name="external_person_ref_provider_type_id_unique",
            ),
            models.UniqueConstraint(
                fields=["person", "provider", "reference_type"],
                name="external_person_ref_person_provider_type_unique",
            ),
        ]
        indexes = [
            models.Index(fields=["person", "status"], name="ext_person_ref_p_status_idx"),
            models.Index(fields=["provider", "reference_type", "status"], name="ext_person_ref_prov_st_idx"),
        ]

    def clean(self):
        errors = {}
        if self.person_id and self.person.record_type != Person.RecordType.BUSINESS:
            errors["person"] = "External marketing references may only be attached to BUSINESS People."
        if not self.provider or not self.provider.strip():
            errors["provider"] = "Provider is required."
        if not self.external_id or not self.external_id.strip():
            errors["external_id"] = "External ID is required."
        if self.status == self.Status.REVOKED and self.revoked_at is None:
            errors["revoked_at"] = "Revoked references must have a revoked timestamp."
        if self.status == self.Status.ACTIVE and self.revoked_at is not None:
            errors["revoked_at"] = "Active references must not have a revoked timestamp."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.provider = self.provider.strip().upper()
        self.external_id = self.external_id.strip()
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.provider} {self.reference_type}: {self.external_id} -> Person {self.person_id}"
