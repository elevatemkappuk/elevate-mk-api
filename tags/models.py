from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from people.models import Person


class Tag(models.Model):
    name = models.CharField(max_length=255)
    slug = models.SlugField(unique=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    display_order = models.PositiveIntegerField(default=100)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["display_order", "name", "id"]

    def __str__(self):
        return self.name


class PersonTag(models.Model):
    person = models.ForeignKey(
        Person,
        on_delete=models.PROTECT,
        related_name="person_tags",
    )
    tag = models.ForeignKey(
        Tag,
        on_delete=models.PROTECT,
        related_name="person_tags",
    )
    is_active = models.BooleanField(default=True)
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="assigned_person_tags",
    )
    assigned_at = models.DateTimeField(default=timezone.now)
    removed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="removed_person_tags",
    )
    removed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["person_id", "tag_id", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["person", "tag"],
                name="tags_person_tag_unique",
            ),
        ]

    def clean(self):
        errors = {}

        if self.is_active:
            if self.removed_by is not None:
                errors["removed_by"] = ["Active tag assignments must not have removed_by set."]
            if self.removed_at is not None:
                errors["removed_at"] = ["Active tag assignments must not have removed_at set."]
        else:
            if self.removed_by is None:
                errors["removed_by"] = ["Inactive tag assignments must record removed_by."]
            if self.removed_at is None:
                errors["removed_at"] = ["Inactive tag assignments must record removed_at."]

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.person} -> {self.tag}"

