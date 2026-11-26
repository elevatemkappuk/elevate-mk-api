from django.contrib.auth.models import Permission
from django.test import Client, TestCase, override_settings

from accounts.models import User
from people.models import Person


class ApiDocumentationAccessTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            email="technical-docs@example.invalid",
            password="testpass123",
            person_first_name="Technical",
            person_last_name="Docs",
            person_record_type=Person.RecordType.TECHNICAL,
        )
        self.permission = Permission.objects.get(
            content_type__app_label="accounts",
            codename="access_api_documentation",
        )

    @override_settings(ELEVATE_ENVIRONMENT="local")
    def test_local_documentation_is_public(self):
        response = self.client.get("/api/schema/")
        self.assertEqual(response.status_code, 200)

    @override_settings(ELEVATE_ENVIRONMENT="staging")
    def test_staging_requires_active_technical_permission(self):
        self.assertEqual(self.client.get("/api/schema/").status_code, 403)
        self.client.force_login(self.user)
        self.assertEqual(self.client.get("/api/schema/").status_code, 403)
        self.user.user_permissions.add(self.permission)
        self.assertEqual(self.client.get("/api/schema/").status_code, 200)

    @override_settings(ELEVATE_ENVIRONMENT="staging")
    def test_inactive_technical_user_is_denied(self):
        self.user.user_permissions.add(self.permission)
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        self.client.force_login(self.user)
        self.assertEqual(self.client.get("/api/schema/").status_code, 403)

    @override_settings(ELEVATE_ENVIRONMENT="production", API_DOCUMENTATION_PRODUCTION_ENABLED=False)
    def test_production_documentation_is_disabled_by_default(self):
        self.assertEqual(self.client.get("/api/schema/").status_code, 404)
        self.assertEqual(self.client.get("/api/docs/").status_code, 404)
        self.assertEqual(self.client.get("/api/redoc/").status_code, 404)

    @override_settings(ELEVATE_ENVIRONMENT="production", API_DOCUMENTATION_PRODUCTION_ENABLED=True)
    def test_enabled_production_requires_permission_or_superuser(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get("/api/schema/").status_code, 403)
        self.user.is_superuser = True
        self.user.is_staff = True
        self.user.save(update_fields=["is_superuser", "is_staff"])
        self.assertEqual(self.client.get("/api/schema/").status_code, 200)

    def test_documentation_uses_session_authentication_only(self):
        from config.api_documentation import ProtectedSpectacularAPIView
        from rest_framework.authentication import SessionAuthentication

        self.assertEqual(ProtectedSpectacularAPIView.authentication_classes, (SessionAuthentication,))
