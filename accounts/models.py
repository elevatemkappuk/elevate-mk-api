from django.contrib.auth.models import AbstractUser, UserManager as DjangoUserManager
from django.db import models

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
        if person_primary_email:
            person_primary_email = normalize_email_address(person_primary_email)

        if person is None:
            if not person_first_name or not person_last_name:
                raise ValueError("User creation requires a linked person or person name details.")
            person_kwargs = {
                "first_name": person_first_name,
                "last_name": person_last_name,
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

        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")

        return self._create_user(email, password, **extra_fields)


class User(AbstractUser):
    username = None
    first_name = None
    last_name = None
    email = models.EmailField("email address", unique=True)
    person = models.OneToOneField(Person, on_delete=models.PROTECT, related_name="user")

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    objects = UserManager()

    def save(self, *args, **kwargs):
        self.email = normalize_email_address(self.email)
        super().save(*args, **kwargs)

    def get_full_name(self):
        return f"{self.person.first_name} {self.person.last_name}".strip()

    def get_short_name(self):
        return self.person.first_name

    @property
    def person_display_name(self):
        return self.get_full_name()

    def __str__(self):
        return self.email
