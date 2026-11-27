import uuid

from django.contrib.auth.models import AbstractUser, UserManager as DjangoUserManager
from django.db import models, transaction
from django.utils import timezone

from people.models import Person


def normalize_email_address(email):
    return DjangoUserManager.normalize_email(email).strip().lower()


class UserManager(DjangoUserManager):
    use_in_migrations = True

    def get_by_natural_key(self, username):
        return self.get(**{self.model.USERNAME_FIELD: normalize_email_address(username)})

    def _create_user(self, email, password, **extra_fields):
        if not email:
            raise ValueError("The email address must be set.")

        email = normalize_email_address(email)
        person = extra_fields.pop("person", None)
        person_first_name = extra_fields.pop("person_first_name", None)
        person_last_name = extra_fields.pop("person_last_name", None)
        person_primary_email = extra_fields.pop("person_primary_email", None)
        person_record_type = extra_fields.pop("person_record_type", Person.RecordType.COMMUNITY)
        if person_primary_email:
            person_primary_email = normalize_email_address(person_primary_email)

        if person is None:
            if not person_first_name or not person_last_name:
                raise ValueError("User creation requires a linked person or person name details.")
            person_kwargs = {
                "first_name": person_first_name,
                "last_name": person_last_name,
                "record_type": person_record_type,
            }
            if person_primary_email:
                person_kwargs["primary_email"] = person_primary_email
            person = Person.objects.create(
                **person_kwargs,
            )

        user = self.model(email=email, person=person, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("person_primary_email", normalize_email_address(email))
        extra_fields.setdefault("person_record_type", Person.RecordType.TECHNICAL)

        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")

        return self._create_user(email, password, **extra_fields)


class User(AbstractUser):
    class AccountLifecycleState(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        PENDING_ACTIVATION = "PENDING_ACTIVATION", "Pending activation"
        DISABLED = "DISABLED", "Disabled"

    username = None
    first_name = None
    last_name = None
    email = models.EmailField("email address", unique=True)
    person = models.OneToOneField(Person, on_delete=models.PROTECT, related_name="user")
    auth_version = models.PositiveBigIntegerField(default=1)
    crm_session_version = models.PositiveBigIntegerField(default=1)
    account_lifecycle_state = models.CharField(
        max_length=24,
        choices=AccountLifecycleState.choices,
        default=AccountLifecycleState.ACTIVE,
    )

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    class Meta:
        permissions = [
            ("access_api_documentation", "Can access protected API documentation"),
        ]

    objects = UserManager()

    def save(self, *args, **kwargs):
        previous_is_active = None
        if self.pk:
            previous_is_active = type(self).objects.filter(pk=self.pk).values_list("is_active", flat=True).first()
        if previous_is_active is True and self.is_active is False and self.account_lifecycle_state == self.AccountLifecycleState.ACTIVE:
            self.account_lifecycle_state = self.AccountLifecycleState.DISABLED
            if kwargs.get("update_fields") is not None:
                kwargs["update_fields"] = set(kwargs["update_fields"]) | {"account_lifecycle_state"}
        self.email = normalize_email_address(self.email)
        with transaction.atomic(using=kwargs.get("using")):
            super().save(*args, **kwargs)
            if previous_is_active is not None and previous_is_active != self.is_active:
                from accounts.revocation import handle_user_active_state_change

                handle_user_active_state_change(
                    user=self,
                    was_active=previous_is_active,
                    actor_user=getattr(self, "_audit_actor_user", None),
                    request=getattr(self, "_audit_request", None),
                )

    def get_full_name(self):
        return f"{self.person.first_name} {self.person.last_name}".strip()

    def get_short_name(self):
        return self.person.first_name

    @property
    def person_display_name(self):
        return self.get_full_name()

    def __str__(self):
        return self.email


class MobileAuthSession(models.Model):
    """Server-authoritative lifecycle record for one mobile token pair."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="mobile_auth_sessions",
    )
    created_at = models.DateTimeField(default=timezone.now)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_reason = models.CharField(max_length=100, null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["user", "revoked_at"], name="mobile_session_active_idx"),
        ]
