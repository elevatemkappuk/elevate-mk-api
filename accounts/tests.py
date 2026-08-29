from django.db import IntegrityError
from django.test import TestCase

from accounts.models import User


class UserModelTests(TestCase):
    def test_normal_user_creation_creates_and_links_one_person(self):
        user = User.objects.create_user(
            email="Casey@example.com",
            password="testpass123",
            person_first_name="Casey",
            person_last_name="Morgan",
        )

        self.assertIsNotNone(user.person_id)
        self.assertEqual(user.person.first_name, "Casey")
        self.assertEqual(user.person.last_name, "Morgan")
        self.assertEqual(User.objects.count(), 1)

    def test_email_is_the_username_field(self):
        self.assertEqual(User.USERNAME_FIELD, "email")

    def test_username_is_not_required(self):
        user = User.objects.create_user(
            email="nousername@example.com",
            password="testpass123",
            person_first_name="No",
            person_last_name="Username",
        )

        field_names = {field.name for field in User._meta.get_fields()}
        self.assertNotIn("username", field_names)
        self.assertEqual(user.email, "nousername@example.com")

    def test_duplicate_authentication_emails_differing_only_by_case_are_rejected(self):
        User.objects.create_user(
            email="Duplicate@Example.com",
            password="testpass123",
            person_first_name="First",
            person_last_name="User",
        )

        with self.assertRaises(IntegrityError):
            User.objects.create_user(
                email="duplicate@example.com",
                password="testpass123",
                person_first_name="Second",
                person_last_name="User",
            )

    def test_superuser_creation_produces_a_valid_linked_person(self):
        user = User.objects.create_superuser(
            email="admin@example.com",
            password="testpass123",
            person_first_name="Admin",
            person_last_name="User",
        )

        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_superuser)
        self.assertIsNotNone(user.person_id)
        self.assertEqual(user.person.first_name, "Admin")

    def test_user_names_are_sourced_from_person(self):
        user = User.objects.create_user(
            email="personname@example.com",
            password="testpass123",
            person_first_name="Jordan",
            person_last_name="Lee",
        )

        field_names = {field.name for field in User._meta.get_fields()}
        self.assertNotIn("first_name", field_names)
        self.assertNotIn("last_name", field_names)
        self.assertEqual(user.get_full_name(), "Jordan Lee")
        self.assertEqual(user.get_short_name(), "Jordan")
