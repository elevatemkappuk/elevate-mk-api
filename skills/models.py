from django.db import models

from people.models import Person


class Skill(models.Model):
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


class PersonSkill(models.Model):
    person = models.ForeignKey(
        Person,
        on_delete=models.PROTECT,
        related_name="person_skills",
    )
    skill = models.ForeignKey(
        Skill,
        on_delete=models.PROTECT,
        related_name="person_skills",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["person_id", "skill_id", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["person", "skill"],
                name="skills_person_skill_unique",
            ),
        ]

    def __str__(self):
        return f"{self.person} -> {self.skill}"

