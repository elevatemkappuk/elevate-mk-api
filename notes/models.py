from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class InternalNoteImmutableDeleteError(ValidationError):
    pass


class InternalNoteQuerySet(models.QuerySet):
    def active(self):
        return self.filter(archived_at__isnull=True)

    def archived(self):
        return self.filter(archived_at__isnull=False)

    def delete(self):
        raise InternalNoteImmutableDeleteError("InternalNote records cannot be hard-deleted.")


class InternalNote(models.Model):
    person = models.ForeignKey(
        "people.Person",
        on_delete=models.PROTECT,
        related_name="internal_notes",
    )
    body = models.TextField()
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="internal_notes_created",
    )
    archived_at = models.DateTimeField(null=True, blank=True)
    archived_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="internal_notes_archived",
        null=True,
        blank=True,
    )
    archive_reason = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = InternalNoteQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at", "-id"]

    def clean(self):
        errors = {}

        if not self.body or not self.body.strip():
            errors["body"] = ["Body cannot be blank."]

        if self.archived_at is None and self.archived_by is not None:
            errors["archived_by"] = ["archived_by must be null when archived_at is null."]
        if self.archived_at is None and self.archive_reason != "":
            errors["archive_reason"] = ["Active notes must not retain archive_reason."]
        if self.archived_at is not None and self.archived_by is None:
            errors["archived_by"] = ["archived_by is required when the note is archived."]

        if errors:
            raise ValidationError(errors)

    def archive(self, *, archived_by, archive_reason=""):
        self.archived_at = timezone.now()
        self.archived_by = archived_by
        self.archive_reason = archive_reason
        self.full_clean()
        self.save(update_fields=["archived_at", "archived_by", "archive_reason", "updated_at"])
        return self

    def restore(self):
        self.archived_at = None
        self.archived_by = None
        self.archive_reason = ""
        self.full_clean()
        self.save(update_fields=["archived_at", "archived_by", "archive_reason", "updated_at"])
        return self

    def delete(self, *args, **kwargs):
        raise InternalNoteImmutableDeleteError("InternalNote records cannot be hard-deleted.")

    def __str__(self):
        return f"Note {self.pk} for {self.person}"

