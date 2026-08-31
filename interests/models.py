from django.db import models

from people.models import Person


class Interest(models.Model):
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


class PersonInterest(models.Model):
    person = models.ForeignKey(
        Person,
        on_delete=models.PROTECT,
        related_name="person_interests",
    )
    interest = models.ForeignKey(
        Interest,
        on_delete=models.PROTECT,
        related_name="person_interests",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["person_id", "interest_id", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["person", "interest"],
                name="interests_person_interest_unique",
            ),
        ]

    def __str__(self):
        return f"{self.person} -> {self.interest}"

