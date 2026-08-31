from datetime import datetime, timezone as dt_timezone
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


@override_settings(ROOT_URLCONF="config.urls")
class PersonAuditHistoryApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.url_template = "/api/v1/people/{person_id}/audit-history/"
        self.overview_url_template = "/api/v1/people/{person_id}/overview/"

        self.nonstaff_user = User.objects.create_user(
            email="nonstaff-audit@example.com",
            password="testpass123",
            person_first_name="Nonstaff",
            person_last_name="Audit",
        )
        self.admin_user = User.objects.create_user(
            email="admin-audit@example.com",
            password="testpass123",
            person_first_name="Admin",
            person_last_name="Audit",
        )
        self.manager_user = User.objects.create_user(
            email="manager-audit@example.com",
            password="testpass123",
            person_first_name="Manager",
            person_last_name="Audit",
        )
        self.viewer_user = User.objects.create_user(
            email="viewer-audit@example.com",
            password="testpass123",
            person_first_name="Viewer",
            person_last_name="Audit",
        )
        self.staff_without_crm_role = User.objects.create_user(
            email="django-staff-only@example.com",
            password="testpass123",
            person_first_name="Django",
            person_last_name="Staff",
            is_staff=True,
            is_superuser=True,
        )

        admin_role = StaffRole.objects.get(code=StaffRole.CRM_ADMIN)
        manager_role = StaffRole.objects.get(code=StaffRole.CRM_MANAGER)
        viewer_role = StaffRole.objects.get(code=StaffRole.CRM_VIEWER)

        StaffRoleAssignment.objects.assign_role(user=self.admin_user, role=admin_role)
        StaffRoleAssignment.objects.assign_role(user=self.manager_user, role=manager_role)
        StaffRoleAssignment.objects.assign_role(user=self.viewer_user, role=viewer_role)

        self.business_person = Person.objects.create(first_name="Amina", last_name="Zulu")
        self.archived_business_person = Person.objects.create(
            first_name="Archived",
            last_name="Person",
            archived_at=timezone.now(),
        )
        self.technical_person = Person.objects.create(
            first_name="Root",
            last_name="Operator",
            record_type=Person.RecordType.TECHNICAL,
        )
        self.other_person = Person.objects.create(first_name="Other", last_name="Person")

    def authenticate(self, user):
        self.client.force_authenticate(user=user)

    def get_url(self, person_id):
        return self.url_template.format(person_id=person_id)

    def get_overview_url(self, person_id):
        return self.overview_url_template.format(person_id=person_id)

    def create_event(self, **kwargs):
        defaults = {
            "actor_user": self.admin_user,
            "action": AuditEvent.Action.MEMBERSHIP_CREATED,
            "entity_type": "Membership",
            "entity_id": "1",
            "changes": {},
            "metadata": {"person_id": str(self.business_person.id)},
        }
        defaults.update(kwargs)
        return AuditEvent.objects.create(**defaults)

    def test_admin_can_view_person_audit_history(self):
        self.authenticate(self.admin_user)
        event = self.create_event(changes={"status": {"from": None, "to": "ACTIVE"}})

        response = self.client.get(self.get_url(self.business_person.id))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["id"], event.id)

    def test_manager_can_view_person_audit_history(self):
        self.authenticate(self.manager_user)
        event = self.create_event()

        response = self.client.get(self.get_url(self.business_person.id))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["results"][0]["id"], event.id)

    def test_viewer_can_view_permission_filtered_person_audit_history(self):
        self.authenticate(self.viewer_user)
        visible_event = self.create_event(
            action=AuditEvent.Action.TAG_ASSIGNED,
            entity_type="PersonTag",
            entity_id="17",
            changes={"is_active": {"from": None, "to": True}},
        )
        self.create_event(
            action=AuditEvent.Action.NOTE_CREATED,
            entity_type="InternalNote",
            entity_id="18",
            changes={"created": {"from": False, "to": True}},
        )

        response = self.client.get(self.get_url(self.business_person.id))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual([item["id"] for item in response.data["results"]], [visible_event.id])

    def test_nonstaff_is_forbidden(self):
        self.authenticate(self.nonstaff_user)

        response = self.client.get(self.get_url(self.business_person.id))

        self.assertEqual(response.status_code, 403)

    def test_anonymous_is_unauthorized(self):
        response = self.client.get(self.get_url(self.business_person.id))

        self.assertEqual(response.status_code, 401)

    def test_django_staff_without_operational_crm_role_is_forbidden(self):
        self.authenticate(self.staff_without_crm_role)

        response = self.client.get(self.get_url(self.business_person.id))

        self.assertEqual(response.status_code, 403)

    def test_archived_business_person_remains_readable(self):
        self.authenticate(self.admin_user)
        event = self.create_event(
            entity_id="22",
            metadata={"person_id": str(self.archived_business_person.id)},
        )

        response = self.client.get(self.get_url(self.archived_business_person.id))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["results"][0]["id"], event.id)

    def test_technical_person_returns_404(self):
        self.authenticate(self.admin_user)

        response = self.client.get(self.get_url(self.technical_person.id))

        self.assertEqual(response.status_code, 404)

    def test_missing_person_returns_404(self):
        self.authenticate(self.admin_user)

        response = self.client.get(self.get_url(999999))

        self.assertEqual(response.status_code, 404)

    def test_person_history_uses_metadata_person_id_and_direct_person_entity_linkage(self):
        self.authenticate(self.admin_user)
        metadata_event = self.create_event(
            action=AuditEvent.Action.PROFESSIONAL_PROFILE_CREATED,
            entity_type="ProfessionalProfile",
            entity_id="31",
            changes={"job_title": {"from": None, "to": "Engineer"}},
        )
        direct_person_event = self.create_event(
            action=AuditEvent.Action.ACCOUNT_DISABLED,
            entity_type="Person",
            entity_id=str(self.business_person.id),
            changes={"archived": {"from": False, "to": True}},
            metadata={},
        )
        self.create_event(
            action=AuditEvent.Action.TAG_REMOVED,
            entity_type="PersonTag",
            entity_id="99",
            changes={"is_active": {"from": True, "to": False}},
            metadata={"person_id": str(self.other_person.id)},
        )

        response = self.client.get(self.get_url(self.business_person.id))

        self.assertEqual(response.status_code, 200)
        self.assertCountEqual(
            [item["id"] for item in response.data["results"]],
            [metadata_event.id, direct_person_event.id],
        )

    def test_unrelated_auth_and_staff_access_events_are_excluded(self):
        self.authenticate(self.admin_user)
        visible_event = self.create_event(
            action=AuditEvent.Action.MEMBERSHIP_ENDED,
            changes={"status": {"from": "ACTIVE", "to": "FORMER"}},
        )
        self.create_event(
            action=AuditEvent.Action.LOGIN_SUCCEEDED,
            entity_type="User",
            entity_id=str(self.admin_user.id),
            metadata={},
        )
        self.create_event(
            action=AuditEvent.Action.STAFF_ROLE_ASSIGNED,
            entity_type="StaffRoleAssignment",
            entity_id="11",
            metadata={},
        )

        response = self.client.get(self.get_url(self.business_person.id))

        self.assertEqual([item["id"] for item in response.data["results"]], [visible_event.id])

    def test_actor_person_identity_does_not_cause_event_inclusion(self):
        self.authenticate(self.admin_user)
        self.create_event(
            action=AuditEvent.Action.TAG_ASSIGNED,
            entity_type="PersonTag",
            entity_id="17",
            actor_user=self.admin_user,
            metadata={"person_id": str(self.other_person.id)},
        )

        response = self.client.get(self.get_url(self.business_person.id))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 0)

    def test_viewer_note_events_are_excluded_before_count_and_pagination(self):
        self.authenticate(self.viewer_user)
        visible_events = [
            self.create_event(
                action=AuditEvent.Action.MEMBERSHIP_CREATED,
                entity_type="Membership",
                entity_id="101",
                metadata={"person_id": str(self.business_person.id)},
            ),
            self.create_event(
                action=AuditEvent.Action.TAG_ASSIGNED,
                entity_type="PersonTag",
                entity_id="102",
                metadata={"person_id": str(self.business_person.id)},
            ),
        ]
        for index in range(30):
            self.create_event(
                action=AuditEvent.Action.NOTE_UPDATED,
                entity_type="InternalNote",
                entity_id=str(200 + index),
                changes={"body": {"changed": True}},
                metadata={"person_id": str(self.business_person.id)},
            )

        response = self.client.get(self.get_url(self.business_person.id))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 2)
        self.assertIsNone(response.data["next"])
        self.assertIsNone(response.data["previous"])
        self.assertCountEqual([item["id"] for item in response.data["results"]], [event.id for event in visible_events])

    def test_admin_can_see_note_events_without_sensitive_note_content(self):
        self.authenticate(self.admin_user)
        event = self.create_event(
            action=AuditEvent.Action.NOTE_UPDATED,
            entity_type="InternalNote",
            entity_id="61",
            changes={
                "body": {"changed": True},
                "archive_reason": {"from": None, "to": "Sensitive"},
                "unexpected": {"from": "x", "to": "y"},
            },
            metadata={"person_id": str(self.business_person.id), "body_changed": True},
            request_id="req-123",
            ip_address="127.0.0.1",
        )

        response = self.client.get(self.get_url(self.business_person.id))

        self.assertEqual(response.status_code, 200)
        result = response.data["results"][0]
        self.assertEqual(result["id"], event.id)
        self.assertEqual(result["description"], "Internal note updated")
        self.assertEqual(result["actor"], {"id": self.admin_user.id, "email": self.admin_user.email})
        self.assertEqual(result["entity_type"], "InternalNote")
        self.assertEqual(result["changes"], {})
        self.assertNotIn("metadata", result)
        self.assertNotIn("request_id", result)
        self.assertNotIn("ip_address", result)
        self.assertNotIn("entity_id", result)
        self.assertNotIn("body", str(result["changes"]))
        self.assertNotIn("archive_reason", str(result["changes"]))

    def test_null_actor_is_supported(self):
        self.authenticate(self.admin_user)
        event = self.create_event(
            actor_user=None,
            action=AuditEvent.Action.MEMBERSHIP_CREATED,
        )

        response = self.client.get(self.get_url(self.business_person.id))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["results"][0]["id"], event.id)
        self.assertIsNone(response.data["results"][0]["actor"])

    def test_safe_changes_projection_omits_unrecognized_keys(self):
        self.authenticate(self.admin_user)
        self.create_event(
            action=AuditEvent.Action.PROFESSIONAL_PROFILE_UPDATED,
            entity_type="ProfessionalProfile",
            entity_id="44",
            changes={
                "job_title": {"from": "Analyst", "to": "Manager"},
                "linkedin_url": {"from": "", "to": "https://www.linkedin.com/in/example"},
                "token": {"from": None, "to": "secret"},
                "nested": {"from": {"bad": "shape"}, "to": {"bad": "shape"}},
            },
        )

        response = self.client.get(self.get_url(self.business_person.id))

        self.assertEqual(
            response.data["results"][0]["changes"],
            {
                "job_title": {"from": "Analyst", "to": "Manager"},
                "linkedin_url": {"from": "", "to": "https://www.linkedin.com/in/example"},
            },
        )

    def test_action_description_falls_back_for_supported_but_unmapped_actions(self):
        self.authenticate(self.admin_user)
        self.create_event(
            action=AuditEvent.Action.ACCOUNT_REENABLED,
            entity_type="Person",
            entity_id=str(self.business_person.id),
            metadata={},
        )

        response = self.client.get(self.get_url(self.business_person.id))

        self.assertEqual(response.data["results"][0]["description"], "Account reenabled")

    def test_default_pagination_is_25_newest_first_then_id(self):
        self.authenticate(self.admin_user)
        older_event = self.create_event(
            action=AuditEvent.Action.SKILL_ASSIGNED,
            entity_type="PersonSkill",
            entity_id="201",
            changes={"assigned": {"from": False, "to": True}},
            occurred_at=datetime(2026, 8, 30, 9, 0, tzinfo=dt_timezone.utc),
        )
        newest_same_time_lower_id = self.create_event(
            action=AuditEvent.Action.TAG_ASSIGNED,
            entity_type="PersonTag",
            entity_id="202",
            changes={"is_active": {"from": None, "to": True}},
            occurred_at=datetime(2026, 8, 31, 9, 0, tzinfo=dt_timezone.utc),
        )
        newest_same_time_higher_id = self.create_event(
            action=AuditEvent.Action.INTEREST_ASSIGNED,
            entity_type="PersonInterest",
            entity_id="203",
            changes={"assigned": {"from": False, "to": True}},
            occurred_at=datetime(2026, 8, 31, 9, 0, tzinfo=dt_timezone.utc),
        )

        response = self.client.get(self.get_url(self.business_person.id))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [item["id"] for item in response.data["results"]],
            [newest_same_time_higher_id.id, newest_same_time_lower_id.id, older_event.id],
        )

    def test_read_only_endpoint_rejects_write_methods(self):
        self.authenticate(self.admin_user)

        self.assertEqual(self.client.post(self.get_url(self.business_person.id), {}, format="json").status_code, 405)
        self.assertEqual(self.client.put(self.get_url(self.business_person.id), {}, format="json").status_code, 405)
        self.assertEqual(self.client.patch(self.get_url(self.business_person.id), {}, format="json").status_code, 405)
        self.assertEqual(self.client.delete(self.get_url(self.business_person.id)).status_code, 405)

    def test_person_overview_response_remains_unchanged(self):
        self.authenticate(self.admin_user)

        response = self.client.get(self.get_overview_url(self.business_person.id))

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("audit_history", response.data)
        self.assertNotIn("notes", response.data)
