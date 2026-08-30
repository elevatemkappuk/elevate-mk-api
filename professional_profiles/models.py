from django.db import models

from people.models import Person


class Industry(models.Model):
    name = models.CharField(max_length=255)
    slug = models.SlugField(unique=True)
    is_active = models.BooleanField(default=True)
    display_order = models.PositiveIntegerField(default=100)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["display_order", "name", "id"]

    def __str__(self):
        return self.name


class ProfessionalProfile(models.Model):
    class CareerStage(models.TextChoices):
        STUDENT = "STUDENT", "Student"
        EARLY_CAREER = "EARLY_CAREER", "Early Career"
        MID_CAREER = "MID_CAREER", "Mid Career"
        SENIOR = "SENIOR", "Senior"
        LEADERSHIP = "LEADERSHIP", "Leadership"
        FOUNDER_BUSINESS_OWNER = "FOUNDER_BUSINESS_OWNER", "Founder / Business Owner"
        OTHER = "OTHER", "Other"

    person = models.OneToOneField(
        Person,
        on_delete=models.PROTECT,
        related_name="professional_profile",
    )
    job_title = models.CharField(max_length=255, blank=True)
    company = models.CharField(max_length=255, blank=True)
    industry = models.ForeignKey(
        Industry,
        on_delete=models.PROTECT,
        related_name="professional_profiles",
        blank=True,
        null=True,
    )
    career_stage = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        choices=CareerStage.choices,
    )
    linkedin_url = models.URLField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["person_id"]

    def __str__(self):
        return f"{self.person} professional profile"
