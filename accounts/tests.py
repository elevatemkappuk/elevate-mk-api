import os
from io import StringIO
from unittest import mock

from django.core.management import call_command, get_commands
from django.db import IntegrityError
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

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
        self.assertEqual(user.person.primary_email, "admin@example.com")

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

    def test_createsuperuser_command_is_overridden_by_accounts_app(self):
        self.assertEqual(get_commands()["createsuperuser"], "accounts")

    def test_createsuperuser_command_creates_linked_person_without_person_id(self):
        stdout = StringIO()

        with mock.patch.dict(os.environ, {"DJANGO_SUPERUSER_PASSWORD": "testpass123"}, clear=False):
            call_command(
                "createsuperuser",
                interactive=False,
                email="CliAdmin@Example.com",
                person_first_name="Cli",
                person_last_name="Admin",
                stdout=stdout,
            )

        user = User.objects.get(email="cliadmin@example.com")
        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_superuser)
        self.assertEqual(user.person.first_name, "Cli")
        self.assertEqual(user.person.last_name, "Admin")
        self.assertEqual(user.person.primary_email, "cliadmin@example.com")


@override_settings(ROOT_URLCONF="config.urls")
class AuthenticationApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.password = "testpass123"
        self.user = User.objects.create_user(
            email="Member@Example.com",
            password=self.password,
            person_first_name="Member",
            person_last_name="Example",
        )
        self.login_url = "/api/v1/auth/login/"
        self.logout_url = "/api/v1/auth/logout/"
        self.me_url = "/api/v1/auth/me/"

    def test_successful_login_with_valid_email_and_password(self):
        response = self.client.post(
            self.login_url,
            {"email": "member@example.com", "password": self.password},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["email"], "member@example.com")
        self.assertEqual(response.data["person"]["id"], self.user.person_id)
        self.assertEqual(response.data["person"]["first_name"], "Member")
        self.assertIn("_auth_user_id", self.client.session)

    def test_invalid_password_is_rejected(self):
        response = self.client.post(
            self.login_url,
            {"email": "member@example.com", "password": "wrongpass"},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["detail"][0], "Invalid email or password.")

    def test_unknown_email_is_rejected(self):
        response = self.client.post(
            self.login_url,
            {"email": "unknown@example.com", "password": self.password},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["detail"][0], "Invalid email or password.")

    def test_case_normalized_email_login(self):
        response = self.client.post(
            self.login_url,
            {"email": "MEMBER@EXAMPLE.COM", "password": self.password},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["email"], "member@example.com")

    def test_inactive_user_cannot_log_in(self):
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])

        response = self.client.post(
            self.login_url,
            {"email": "member@example.com", "password": self.password},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["detail"][0], "Invalid email or password.")

    def test_authenticated_me_returns_user_and_person(self):
        self.client.post(
            self.login_url,
            {"email": "member@example.com", "password": self.password},
            format="json",
        )

        response = self.client.get(self.me_url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["id"], self.user.id)
        self.assertEqual(response.data["person"]["first_name"], "Member")
        self.assertNotIn("password", response.data)

    def test_anonymous_me_returns_401(self):
        response = self.client.get(self.me_url)

        self.assertEqual(response.status_code, 401)

    def test_logout_invalidates_authenticated_session(self):
        self.client.post(
            self.login_url,
            {"email": "member@example.com", "password": self.password},
            format="json",
        )

        response = self.client.post(self.logout_url, {}, format="json")

        self.assertEqual(response.status_code, 204)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_authenticated_access_fails_after_logout(self):
        self.client.post(
            self.login_url,
            {"email": "member@example.com", "password": self.password},
            format="json",
        )
        self.client.post(self.logout_url, {}, format="json")

        response = self.client.get(self.me_url)

        self.assertEqual(response.status_code, 401)

    def test_openapi_schema_endpoint_is_reachable(self):
        response = self.client.get("/api/schema/?format=json")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"].split(";")[0], "application/vnd.oai.openapi+json")
        self.assertIn("openapi", response.json())
        self.assertIn("/api/v1/auth/login/", response.json()["paths"])

    def test_swagger_ui_endpoint_is_reachable(self):
        response = self.client.get("/api/docs/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "swagger-ui")

    def test_redoc_endpoint_is_reachable(self):
        response = self.client.get("/api/redoc/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "redoc")

    def test_openapi_schema_generation_command_succeeds(self):
        stdout = StringIO()

        call_command("spectacular", validate=True, stdout=stdout)

        output = stdout.getvalue()
        self.assertIn("openapi: 3.0.3", output)
        self.assertIn("/api/v1/auth/login/", output)
