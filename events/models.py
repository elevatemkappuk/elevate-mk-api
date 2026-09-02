from django.db import models

from people.models import Person


class Event(models.Model):
    class Status(models.TextChoices):
        SCHEDULED = "SCHEDULED", "Scheduled"
        COMPLETED = "COMPLETED", "Completed"
        CANCELLED = "CANCELLED", "Cancelled"

    name = models.CharField(max_length=255)
    start_at = models.DateTimeField()
    end_at = models.DateTimeField(null=True, blank=True)
    timezone = models.CharField(max_length=64)
    location_name = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.SCHEDULED)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-start_at", "id"]

    def __str__(self):
        return self.name


class EventParticipation(models.Model):
    class Status(models.TextChoices):
        REGISTERED = "REGISTERED", "Registered"
        ATTENDED = "ATTENDED", "Attended"
        CANCELLED = "CANCELLED", "Cancelled"

    event = models.ForeignKey(Event, on_delete=models.PROTECT, related_name="participations")
    person = models.ForeignKey(Person, on_delete=models.PROTECT, related_name="event_participations")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.REGISTERED)
    ticket_quantity = models.PositiveIntegerField(null=True, blank=True)
    registered_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["event_id", "person_id", "id"]
        constraints = [
            models.UniqueConstraint(fields=["event", "person"], name="event_participation_event_person_unique"),
        ]
        indexes = [
            models.Index(fields=["person", "event"], name="event_part_person_event_idx"),
        ]

    def __str__(self):
        return f"{self.person} at {self.event}"


class ExternalEventReference(models.Model):
    """Provider-neutral source identity for an Event or its participation."""

    class ReferenceType(models.TextChoices):
        EVENT = "EVENT", "Event"
        PARTICIPATION = "PARTICIPATION", "Participation"

    provider = models.CharField(max_length=100)
    reference_type = models.CharField(max_length=20, choices=ReferenceType.choices)
    external_id = models.CharField(max_length=255)
    event = models.ForeignKey(Event, on_delete=models.PROTECT, related_name="external_references")
    participation = models.ForeignKey(
        EventParticipation,
        on_delete=models.PROTECT,
        related_name="external_references",
        null=True,
        blank=True,
    )
    import_record = models.ForeignKey(
        "data_imports.ImportRecord",
        on_delete=models.PROTECT,
        related_name="event_external_references",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "reference_type", "external_id"],
                name="external_event_reference_provider_type_id_unique",
            ),
        ]
        indexes = [
            models.Index(fields=["event", "reference_type"], name="ext_event_ref_event_type_idx"),
            models.Index(fields=["participation", "reference_type"], name="ext_event_ref_part_type_idx"),
        ]

    def __str__(self):
        return f"{self.provider} {self.reference_type}: {self.external_id}"
