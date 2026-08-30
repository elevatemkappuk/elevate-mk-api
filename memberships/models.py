from django.core.exceptions import ValidationError
from django.db import models

from people.models import Person


class Membership(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        FORMER = "FORMER", "Former"

    class Source(models.TextChoices):
        WEBSITE_FORM = "WEBSITE_FORM", "Website form"
        STAFF = "STAFF", "Staff"
        COMMUNITY_PLATFORM = "COMMUNITY_PLATFORM", "Community platform"
        OTHER = "OTHER", "Other"

    person = models.OneToOneField(
        Person,
        on_delete=models.PROTECT,
        related_name="membership",
    )
    status = models.CharField(max_length=20, choices=Status.choices)
    joined_at = models.DateField()
    ended_at = models.DateField(blank=True, null=True)
    membership_source = models.CharField(max_length=30, choices=Source.choices)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-joined_at", "id"]

    def __str__(self):
        return f"{self.person} membership ({self.status})"

    def clean(self):
        errors = {}

        if self.ended_at and self.ended_at < self.joined_at:
            errors["ended_at"] = "ended_at cannot be before joined_at."

        if self.status == self.Status.ACTIVE and self.ended_at is not None:
            errors["ended_at"] = "ACTIVE membership cannot have ended_at."

        if self.status == self.Status.FORMER and self.ended_at is None:
            errors["ended_at"] = "FORMER membership requires ended_at."

        if errors:
            raise ValidationError(errors)
