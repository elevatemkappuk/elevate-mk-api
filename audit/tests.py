from django.contrib import admin
from django.contrib.admin.sites import AdminSite
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import User
from audit.admin import AuditEventAdmin
from audit.models import AuditEvent, AuditEventImmutableError
from audit.services import record_audit_event


class AuditEventModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="auditor@example.com",
            password="testpass123",
            person_first_name="Audit",
            person_last_name="Actor",
        )

    def test_service_can_create_event(self):
        event = record_audit_event(
            action=AuditEvent.Action.LOGIN_SUCCEEDED,
            actor_user=self.user,
            entity_type="User",
            entity_id=self.user.id,
        )

        self.assertEqual(event.actor_user, self.user)
        self.assertEqual(event.entity_type, "User")
        self.assertEqual(event.entity_id, str(self.user.id))

    def test_actor_user_may_be_null(self):
        event = record_audit_event(
            action=AuditEvent.Action.LOGIN_FAILED,
            entity_type="Authentication",
        )

        self.assertIsNone(event.actor_user)

    def test_changes_and_metadata_default_to_empty_dicts(self):
        event = record_audit_event(
            action=AuditEvent.Action.LOGIN_FAILED,
            entity_type="Authentication",
        )

        self.assertEqual(event.changes, {})
        self.assertEqual(event.metadata, {})

    def test_occurred_at_is_populated(self):
        event = record_audit_event(
            action=AuditEvent.Action.LOGOUT,
            actor_user=self.user,
            entity_type="User",
            entity_id=self.user.id,
        )

        self.assertIsNotNone(event.occurred_at)

    def test_ordering_is_newest_first_then_id(self):
        first = record_audit_event(
            action=AuditEvent.Action.LOGIN_FAILED,
            entity_type="Authentication",
        )
        second = record_audit_event(
            action=AuditEvent.Action.LOGIN_SUCCEEDED,
            actor_user=self.user,
            entity_type="User",
            entity_id=self.user.id,
        )

        self.assertEqual(list(AuditEvent.objects.values_list("id", flat=True)[:2]), [second.id, first.id])

    def test_model_instance_cannot_be_edited_after_creation(self):
        event = record_audit_event(
            action=AuditEvent.Action.LOGIN_FAILED,
            entity_type="Authentication",
        )

        event.action = AuditEvent.Action.LOGOUT
        with self.assertRaises(AuditEventImmutableError):
            event.save()

    def test_model_instance_cannot_be_deleted(self):
        event = record_audit_event(
            action=AuditEvent.Action.LOGIN_FAILED,
            entity_type="Authentication",
        )

        with self.assertRaises(AuditEventImmutableError):
            event.delete()

    def test_queryset_update_is_blocked(self):
        record_audit_event(
            action=AuditEvent.Action.LOGIN_FAILED,
            entity_type="Authentication",
        )

        with self.assertRaises(AuditEventImmutableError):
            AuditEvent.objects.all().update(action=AuditEvent.Action.LOGOUT)

    def test_queryset_delete_is_blocked(self):
        record_audit_event(
            action=AuditEvent.Action.LOGIN_FAILED,
            entity_type="Authentication",
        )

        with self.assertRaises(AuditEventImmutableError):
            AuditEvent.objects.all().delete()

    def test_service_normalizes_entity_id_to_string(self):
        event = record_audit_event(
            action=AuditEvent.Action.LOGIN_SUCCEEDED,
            actor_user=self.user,
            entity_type="User",
            entity_id=17,
        )

        self.assertEqual(event.entity_id, "17")

    def test_service_rejects_unknown_action(self):
        with self.assertRaises(ValueError):
            record_audit_event(action="UNKNOWN", entity_type="User")

    def test_service_produces_exactly_one_event(self):
        record_audit_event(
            action=AuditEvent.Action.LOGIN_FAILED,
            entity_type="Authentication",
        )

        self.assertEqual(AuditEvent.objects.count(), 1)


class AuditEventAdminTests(TestCase):
    def setUp(self):
        self.site = AdminSite()
        self.admin = AuditEventAdmin(AuditEvent, self.site)

    def test_audit_event_is_registered_read_only(self):
        self.assertEqual(
            self.admin.list_display,
            ("occurred_at", "action", "actor_user", "entity_type", "entity_id"),
        )
        self.assertEqual(self.admin.list_filter, ("action", "occurred_at", "entity_type"))
        self.assertEqual(self.admin.search_fields, ("actor_user__email", "entity_id", "action"))

    def test_add_change_delete_are_denied(self):
        request = type("Request", (), {"user": None})()

        self.assertFalse(self.admin.has_add_permission(request))
        self.assertFalse(self.admin.has_change_permission(request))
        self.assertFalse(self.admin.has_delete_permission(request))

    def test_audit_event_is_registered(self):
        self.assertIs(admin.site._registry[AuditEvent].__class__, AuditEventAdmin)


@override_settings(ROOT_URLCONF="config.urls")
class AuthenticationAuditIntegrationTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.password = "testpass123"
        self.user = User.objects.create_user(
            email="member@example.com",
            password=self.password,
            person_first_name="Member",
            person_last_name="Example",
        )
        self.login_url = "/api/v1/auth/login/"
        self.logout_url = "/api/v1/auth/logout/"
        self.me_url = "/api/v1/auth/me/"
        self.csrf_url = "/api/v1/auth/csrf/"

    def test_successful_login_creates_exactly_one_login_succeeded_event(self):
        response = self.client.post(
            self.login_url,
            {"email": "member@example.com", "password": self.password},
            format="json",
        )

        event = AuditEvent.objects.get()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(event.action, AuditEvent.Action.LOGIN_SUCCEEDED)
        self.assertEqual(event.actor_user, self.user)
        self.assertEqual(event.entity_type, "User")
        self.assertEqual(event.entity_id, str(self.user.id))
        self.assertEqual(event.changes, {})
        self.assertEqual(event.metadata, {})

    def test_wrong_credentials_create_exactly_one_login_failed_event(self):
        response = self.client.post(
            self.login_url,
            {"email": "member@example.com", "password": "wrongpass"},
            format="json",
        )

        event = AuditEvent.objects.get()
        self.assertEqual(response.status_code, 400)
        self.assertEqual(event.action, AuditEvent.Action.LOGIN_FAILED)
        self.assertIsNone(event.actor_user)
        self.assertEqual(event.entity_type, "Authentication")
        self.assertIsNone(event.entity_id)

    def test_unknown_credentials_create_exactly_one_login_failed_event(self):
        response = self.client.post(
            self.login_url,
            {"email": "unknown@example.com", "password": self.password},
            format="json",
        )

        event = AuditEvent.objects.get()
        self.assertEqual(response.status_code, 400)
        self.assertEqual(event.action, AuditEvent.Action.LOGIN_FAILED)
        self.assertIsNone(event.actor_user)
        self.assertEqual(event.entity_type, "Authentication")
        self.assertIsNone(event.entity_id)

    def test_malformed_login_request_does_not_create_login_failed_event(self):
        response = self.client.post(self.login_url, {"email": "member@example.com"}, format="json")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(AuditEvent.objects.count(), 0)

    def test_authenticated_logout_creates_exactly_one_logout_event(self):
        self.client.post(
            self.login_url,
            {"email": "member@example.com", "password": self.password},
            format="json",
        )

        response = self.client.post(self.logout_url, {}, format="json")

        event = AuditEvent.objects.filter(action=AuditEvent.Action.LOGOUT).get()
        self.assertEqual(response.status_code, 204)
        self.assertEqual(event.action, AuditEvent.Action.LOGOUT)
        self.assertEqual(event.actor_user, self.user)
        self.assertEqual(event.entity_type, "User")
        self.assertEqual(event.entity_id, str(self.user.id))

    def test_auth_me_endpoint_remains_unchanged_by_audit(self):
        self.client.post(
            self.login_url,
            {"email": "member@example.com", "password": self.password},
            format="json",
        )

        response = self.client.get(self.me_url)

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("audit", response.data)

    def test_csrf_endpoint_remains_unchanged_by_audit(self):
        response = self.client.get(self.csrf_url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(AuditEvent.objects.count(), 0)
