import uuid

from django.core.exceptions import ValidationError
from django.core.validators import MaxLengthValidator
from django.db import models
from django.utils import timezone

from people.models import Person

from community.photos import community_profile_photo_upload_to


def validate_non_empty_text(value):
    if not value or not value.strip():
        raise ValidationError("This field cannot be empty.")


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


class CommunityConnection(models.Model):
    """A mutual Community relationship between one unordered Person pair."""

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        ACCEPTED = "ACCEPTED", "Accepted"
        DECLINED = "DECLINED", "Declined"
        DISCONNECTED = "DISCONNECTED", "Disconnected"

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    person_low = models.ForeignKey(
        Person,
        on_delete=models.PROTECT,
        related_name="community_connections_low",
    )
    person_high = models.ForeignKey(
        Person,
        on_delete=models.PROTECT,
        related_name="community_connections_high",
    )
    requester = models.ForeignKey(
        Person,
        on_delete=models.PROTECT,
        related_name="community_connection_requests",
    )
    status = models.CharField(max_length=20, choices=Status.choices)
    requested_at = models.DateTimeField(default=timezone.now)
    accepted_at = models.DateTimeField(null=True, blank=True)
    declined_at = models.DateTimeField(null=True, blank=True)
    disconnected_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["person_low", "person_high"],
                name="community_connection_pair_unique",
            ),
            models.CheckConstraint(
                condition=models.Q(person_low__lt=models.F("person_high")),
                name="community_connection_pair_canonical",
            ),
            models.CheckConstraint(
                condition=~models.Q(person_low=models.F("person_high")),
                name="community_connection_no_self_pair",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(requester=models.F("person_low"))
                    | models.Q(requester=models.F("person_high"))
                ),
                name="community_connection_requester_in_pair",
            ),
        ]
        indexes = [
            models.Index(fields=["person_low", "status"], name="community_conn_low_status_idx"),
            models.Index(fields=["person_high", "status"], name="community_conn_high_status_idx"),
            models.Index(fields=["requester", "status"], name="community_conn_req_status_idx"),
        ]

    @property
    def recipient(self):
        """Return the participant other than the persisted requester."""
        return self.person_high if self.requester_id == self.person_low_id else self.person_low

    def __str__(self):
        return f"Community connection {self.public_id} ({self.status})"


class CommunityPost(models.Model):
    """A text-first, member-authored Community publication."""

    class Purpose(models.TextChoices):
        ASK = "ASK", "Ask"
        OFFER = "OFFER", "Offer"
        OPPORTUNITY = "OPPORTUNITY", "Opportunity"
        UPDATE = "UPDATE", "Update"

    class Audience(models.TextChoices):
        ELEVATE_COMMUNITY = "ELEVATE_COMMUNITY", "Elevate Community"
        CONNECTIONS = "CONNECTIONS", "Connections"

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        AUTHOR_DELETED = "AUTHOR_DELETED", "Author deleted"
        MODERATOR_REMOVED = "MODERATOR_REMOVED", "Moderator removed"

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    author = models.ForeignKey(
        Person,
        on_delete=models.PROTECT,
        related_name="community_posts",
    )
    purpose = models.CharField(max_length=20, choices=Purpose.choices)
    headline = models.CharField(
        max_length=120,
        validators=[validate_non_empty_text, MaxLengthValidator(120)],
    )
    body = models.TextField(
        max_length=2000,
        validators=[validate_non_empty_text, MaxLengthValidator(2000)],
    )
    audience = models.CharField(max_length=20, choices=Audience.choices)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    edited_at = models.DateTimeField(null=True, blank=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    removed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["status", "-created_at", "-id"], name="cpost_status_created_idx"),
            models.Index(fields=["purpose", "status", "-created_at", "-id"], name="cpost_purpose_idx"),
            models.Index(fields=["audience", "status", "-created_at", "-id"], name="cpost_audience_idx"),
            models.Index(fields=["author", "status", "-created_at", "-id"], name="cpost_author_idx"),
        ]

    def clean(self):
        errors = {}
        if self.author_id and getattr(self.author, "record_type", None) != Person.RecordType.BUSINESS:
            errors["author"] = "Community posts must be authored by a BUSINESS Person."
        if isinstance(self.headline, str) and not self.headline.strip():
            errors["headline"] = "Headline cannot be empty."
        if isinstance(self.body, str) and not self.body.strip():
            errors["body"] = "Body cannot be empty."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"Community post {self.public_id}"


class CommunityPostIdempotencyReceipt(models.Model):
    """Bounded replay record for an authenticated Community post creation."""

    author = models.ForeignKey(
        Person,
        on_delete=models.PROTECT,
        related_name="community_post_idempotency_receipts",
    )
    key_hash = models.CharField(max_length=64)
    request_digest = models.CharField(max_length=64)
    post = models.OneToOneField(
        CommunityPost,
        on_delete=models.PROTECT,
        related_name="idempotency_receipt",
    )
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["author", "key_hash"],
                name="community_post_idempotency_unique",
            ),
        ]
        indexes = [
            models.Index(fields=["author", "expires_at"], name="cpost_receipt_expiry_idx"),
        ]


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


class CommunityEmailChangeRequest(models.Model):
    """Pending, request-only lifecycle for a member email change."""

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    person = models.ForeignKey(Person, on_delete=models.PROTECT, related_name="community_email_change_requests")
    user = models.ForeignKey("accounts.User", on_delete=models.PROTECT, related_name="community_email_change_requests")
    previous_email = models.EmailField()
    requested_email = models.EmailField()
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
            models.Index(fields=["token_hash"], name="comm_email_change_token_idx"),
            models.Index(fields=["person", "expires_at", "used_at", "revoked_at", "superseded_at"], name="comm_email_change_state_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["person"],
                condition=models.Q(used_at__isnull=True, revoked_at__isnull=True, superseded_at__isnull=True),
                name="community_one_current_email_change",
            ),
        ]

    @property
    def is_current(self):
        return self.used_at is None and self.revoked_at is None and self.superseded_at is None

    @property
    def is_usable(self):
        return self.is_current and self.token_hash is not None and self.expires_at > timezone.now()

    def __str__(self):
        return f"Community email change {self.public_id} for Person {self.person_id}"
