import re

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

    class AgeRange(models.TextChoices):
        UNDER_25 = "UNDER_25", "Under 25"
        AGE_25_29 = "25_29", "25 - 29"
        AGE_30_34 = "30_34", "30 - 34"
        AGE_35_39 = "35_39", "35 - 39"
        AGE_40_45 = "40_45", "40 - 45"
        OVER_45 = "OVER_45", "Over 45"

    class Gender(models.TextChoices):
        MALE = "MALE", "Male"
        FEMALE = "FEMALE", "Female"
        NON_BINARY = "NON_BINARY", "Non-Binary"
        TRANSGENDER = "TRANSGENDER", "Transgender"
        OTHER = "OTHER", "Other"

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
    age_range = models.CharField(max_length=100, choices=AgeRange.choices, blank=True)
    gender = models.CharField(max_length=100, choices=Gender.choices, blank=True)
    archived_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["last_name", "first_name", "id"]

    objects = PersonQuerySet.as_manager()

    def __str__(self):
        return f"{self.first_name} {self.last_name}"

    @staticmethod
    def _normalize_demographic_input(value):
        """Normalize only known Membership Form presentation variations."""
        value = str(value).strip().casefold()
        value = value.replace("\u2013", "-").replace("\u2014", "-")
        value = value.replace("\u00e2\u20ac\u201c", "-")
        value = re.sub(r"\s*-\s*", "-", value)
        return re.sub(r"\s+", " ", value)

    @classmethod
    def normalize_age_range(cls, value):
        if value is None or not str(value).strip():
            return None
        values = {
            "under 25": cls.AgeRange.UNDER_25,
            "25-29": cls.AgeRange.AGE_25_29,
            "30-34": cls.AgeRange.AGE_30_34,
            "35-39": cls.AgeRange.AGE_35_39,
            "40-45": cls.AgeRange.AGE_40_45,
            "over 45": cls.AgeRange.OVER_45,
        }
        return values.get(cls._normalize_demographic_input(value))

    @classmethod
    def normalize_gender(cls, value):
        if value is None or not str(value).strip():
            return None
        values = {
            "male": cls.Gender.MALE,
            "female": cls.Gender.FEMALE,
            "non-binary": cls.Gender.NON_BINARY,
            "non binary": cls.Gender.NON_BINARY,
            "transgender": cls.Gender.TRANSGENDER,
            "other": cls.Gender.OTHER,
        }
        return values.get(cls._normalize_demographic_input(value))
