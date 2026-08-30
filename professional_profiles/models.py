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
    # Controlled career-stage taxonomy is intentionally deferred until values are confirmed.
    career_stage = models.CharField(max_length=255, blank=True, null=True)
    linkedin_url = models.URLField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["person_id"]

    def __str__(self):
        return f"{self.person} professional profile"
