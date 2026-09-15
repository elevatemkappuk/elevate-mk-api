from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from people.models import Person


class MarketingPreference(models.Model):
    """Current provider-neutral marketing preference for one Person and channel."""

    class Channel(models.TextChoices):
        EMAIL = "EMAIL", "Email"

    class State(models.TextChoices):
        UNKNOWN = "UNKNOWN", "Unknown"
        OPTED_IN = "OPTED_IN", "Opted in"
        OPTED_OUT = "OPTED_OUT", "Opted out"

    class Source(models.TextChoices):
        MEMBERSHIP_FORM = "MEMBERSHIP_FORM", "Membership form"
        WEBSITE_SIGNUP = "WEBSITE_SIGNUP", "Website signup"
        STAFF_RECORDED = "STAFF_RECORDED", "Staff recorded"
        HISTORICAL_IMPORT = "HISTORICAL_IMPORT", "Historical import"
        MAILCHIMP = "MAILCHIMP", "Mailchimp"
        OTHER = "OTHER", "Other"

    person = models.ForeignKey(
        Person,
        on_delete=models.PROTECT,
        related_name="marketing_preferences",
    )
    channel = models.CharField(max_length=20, choices=Channel.choices)
    state = models.CharField(max_length=20, choices=State.choices)
    source = models.CharField(max_length=30, choices=Source.choices)
    recorded_at = models.DateTimeField(default=timezone.now)
    actor_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="recorded_marketing_preferences",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["person_id", "channel", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["person", "channel"],
                name="marketing_preference_person_channel_unique",
            ),
        ]
        indexes = [
            models.Index(fields=["channel", "state"], name="marketing_pref_chan_state_idx"),
        ]

    def clean(self):
        if self.state == self.State.UNKNOWN:
            raise ValidationError({"state": "UNKNOWN is represented by the absence of an explicit preference row."})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"Person {self.person_id} {self.channel}: {self.state}"


class MarketingPreferenceHistory(models.Model):
    """Immutable evidence of each meaningful explicit marketing-preference recording."""

    preference = models.ForeignKey(
        MarketingPreference,
        on_delete=models.PROTECT,
        related_name="history",
    )
    channel = models.CharField(max_length=20, choices=MarketingPreference.Channel.choices)
    state = models.CharField(max_length=20, choices=MarketingPreference.State.choices)
    source = models.CharField(max_length=30, choices=MarketingPreference.Source.choices)
    recorded_at = models.DateTimeField()
    actor_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="marketing_preference_history",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["recorded_at", "id"]
        indexes = [
            models.Index(fields=["preference", "recorded_at"], name="marketing_pref_hist_time_idx"),
        ]

    def clean(self):
        if self.state == MarketingPreference.State.UNKNOWN:
            raise ValidationError({"state": "Marketing preference history requires an explicit state."})

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Marketing preference history is append-only.")
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Marketing preference history is append-only.")

    def __str__(self):
        return f"{self.channel}: {self.state} ({self.recorded_at.isoformat()})"
