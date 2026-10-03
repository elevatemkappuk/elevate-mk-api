import uuid

from django.core.validators import MaxLengthValidator
from django.db import models
from django.utils import timezone

from people.models import Person

from community.photos import community_profile_photo_upload_to


class JoinSubmissionReceipt(models.Model):
    class Status(models.TextChoices):
        ACCEPTED = "ACCEPTED", "Accepted"

    key_hash = models.CharField(max_length=64, unique=True)
    request_digest = models.CharField(max_length=64)
    status = models.CharField(max_length=20, choices=Status.choices)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]


class CommunityProfile(models.Model):
    """Community-owned presentation and onboarding state for a Person."""

    person = models.OneToOneField(
        Person,
        on_delete=models.PROTECT,
        related_name="community_profile",
    )
    asset_namespace_id = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        editable=False,
    )
    directory_id = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        editable=False,
    )
    directory_visible = models.BooleanField(default=True)
    email_visible = models.BooleanField(default=False)
    mobile_visible = models.BooleanField(default=False)
    bio = models.TextField(
        blank=True,
        max_length=400,
        validators=[MaxLengthValidator(400)],
    )
    photo = models.ImageField(
        upload_to=community_profile_photo_upload_to,
        blank=True,
        null=True,
        max_length=500,
    )
    person_preexisted_community = models.BooleanField(default=False, editable=False)
    review_acknowledged_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def review_required(self):
        return self.person_preexisted_community and self.review_acknowledged_at is None

    def __str__(self):
        return f"Community profile for {self.person}"


class CommunityAccountInvitation(models.Model):
    """Single Community account-activation lifecycle for one Person."""

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    person = models.ForeignKey(
        Person,
        on_delete=models.PROTECT,
        related_name="community_account_invitations",
    )
    intended_email = models.EmailField()
    token_hash = models.CharField(max_length=64, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    superseded_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["token_hash"], name="community_invite_token_idx"),
            models.Index(fields=["person", "expires_at", "used_at", "revoked_at", "superseded_at"], name="community_invite_state_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["person"],
                condition=models.Q(used_at__isnull=True, revoked_at__isnull=True, superseded_at__isnull=True),
                name="community_one_current_invitation",
            ),
        ]

    @property
    def is_current(self):
        return self.used_at is None and self.revoked_at is None and self.superseded_at is None

    @property
    def is_usable(self):
        return (
            self.is_current
            and self.token_hash is not None
            and self.expires_at > timezone.now()
        )

    def __str__(self):
        return f"Community activation for Person {self.person_id} ({self.intended_email})"
