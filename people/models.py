from django.db import models


class PersonQuerySet(models.QuerySet):
    def business(self):
        return self.filter(record_type=Person.RecordType.BUSINESS)

    def active_business(self):
        return self.business().filter(archived_at__isnull=True)

    def archived_business(self):
        return self.business().filter(archived_at__isnull=False)


class Person(models.Model):
    class RecordType(models.TextChoices):
        BUSINESS = "BUSINESS", "Business"
        TECHNICAL = "TECHNICAL", "Technical"

    record_type = models.CharField(
        max_length=20,
        choices=RecordType.choices,
        default=RecordType.BUSINESS,
    )
    first_name = models.CharField(max_length=150)
    last_name = models.CharField(max_length=150)
    primary_email = models.EmailField(blank=True, null=True)
    mobile = models.CharField(max_length=50, blank=True)
    location = models.CharField(max_length=255, blank=True)
    age_range = models.CharField(max_length=100, blank=True)
    gender = models.CharField(max_length=100, blank=True)
    archived_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["last_name", "first_name", "id"]

    objects = PersonQuerySet.as_manager()

    def __str__(self):
        return f"{self.first_name} {self.last_name}"
