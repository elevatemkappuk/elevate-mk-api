import time
from datetime import date

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.authentication import CRM_SESSION_CREATED_AT, CRM_SESSION_LAST_ACTIVITY
from accounts.models import User
from accounts.revocation import revoke_user_credentials
from audit.models import AuditEvent
from memberships.models import Membership
from staff_access.models import StaffRole, StaffRoleAssignment


@override_settings(ROOT_URLCONF="config.urls")
class AuthenticationAuditTests(TestCase):
    def setUp(self):
        self.password = "Password-123!"
        self.bootstrap = User.objects.create_superuser(
            email="audit-bootstrap@example.com",
            password=self.password,
            person_first_name="Audit",
            person_last_name="Bootstrap",
        )
        self.user = User.objects.create_user(
            email="member@example.com",
            password=self.password,
            person_first_name="Member",
            person_last_name="User",
        )
        Membership.objects.create(
            person=self.user.person,
            status=Membership.Status.ACTIVE,
            joined_at=date(2024, 1, 1),
            membership_source=Membership.Source.COMMUNITY_PLATFORM,
        )
        self.staff_user = User.objects.create_user(
            email="staff-audit@example.com",
            password=self.password,
            person_first_name="Staff",
            person_last_name="Audit",
            person_record_type="STAFF",
        )
        StaffRoleAssignment.objects.assign_role(
            user=self.staff_user,
            role=StaffRole.objects.get(code=StaffRole.CRM_MANAGER),
            assigned_by=self.bootstrap,
        )
        self.client = APIClient()

    def test_login_audit_includes_request_context_without_credentials(self):
        response = self.client.post(
            "/api/v1/auth/token/",
            {"email": self.user.email, "password": self.password},
            format="json",
            HTTP_X_REQUEST_ID="request-123",
            REMOTE_ADDR="192.0.2.10",
        )

        self.assertEqual(response.status_code, 200)
        event = AuditEvent.objects.get(action=AuditEvent.Action.LOGIN_SUCCEEDED)
        self.assertTrue(event.request_id)
        self.assertNotEqual(event.request_id, "request-123")
        self.assertIsNone(event.ip_address)
        self.assertEqual(event.metadata, {"authentication": "jwt"})
        self.assertNotIn(response.data["access"], str({"metadata": event.metadata, "changes": event.changes}))
        self.assertNotIn(response.data["refresh"], str({"metadata": event.metadata, "changes": event.changes}))

    def test_failed_login_is_audited_with_context_and_no_password(self):
        response = self.client.post(
            "/api/v1/auth/token/",
            {"email": self.user.email, "password": self.password + "wrong"},
            format="json",
            HTTP_X_REQUEST_ID="request-failed",
            REMOTE_ADDR="192.0.2.11",
        )

        self.assertEqual(response.status_code, 400)
        event = AuditEvent.objects.get(action=AuditEvent.Action.LOGIN_FAILED)
        self.assertTrue(event.request_id)
        self.assertNotEqual(event.request_id, "request-failed")
        self.assertIsNone(event.ip_address)
        self.assertNotIn(self.password, str({"metadata": event.metadata, "changes": event.changes}))

    def test_mobile_logout_audit_contains_only_an_opaque_session_reference(self):
        login_response = self.client.post(
            "/api/v1/auth/token/",
            {"email": self.user.email, "password": self.password},
            format="json",
        )
        access = login_response.data["access"]
        refresh = login_response.data["refresh"]

        response = self.client.post(
            "/api/v1/auth/token/logout/",
            {"refresh": refresh},
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {access}",
        )

        self.assertEqual(response.status_code, 204)
        event = AuditEvent.objects.get(action=AuditEvent.Action.LOGOUT)
        self.assertTrue(event.metadata["mobile_session_id"])
        self.assertEqual(event.metadata["revocation_reason"], "mobile_logout")
        self.assertNotIn(access, str({"metadata": event.metadata, "changes": event.changes}))
        self.assertNotIn(refresh, str({"metadata": event.metadata, "changes": event.changes}))

    def test_session_expiry_is_audited_with_reason_and_no_session_secret(self):
        session_client = APIClient()
        login_response = session_client.post(
            "/api/v1/auth/login/",
            {"email": self.user.email, "password": self.password},
            format="json",
        )
        self.assertEqual(login_response.status_code, 200)
        session = session_client.session
        session[CRM_SESSION_CREATED_AT] = time.time()
        session[CRM_SESSION_LAST_ACTIVITY] = time.time() - (30 * 60 + 1)
        session.save()

        response = session_client.get("/api/v1/auth/me/", HTTP_X_REQUEST_ID="expired-request")

        self.assertEqual(response.status_code, 401)
        event = AuditEvent.objects.get(action=AuditEvent.Action.SESSION_EXPIRED)
        self.assertEqual(event.metadata["reason"], "idle_timeout")
        self.assertTrue(event.request_id)
        self.assertNotEqual(event.request_id, "expired-request")
        self.assertNotIn("sessionid", str({"metadata": event.metadata, "changes": event.changes}))

    def test_credential_revocation_audit_records_reason_and_count_only(self):
        token_response = self.client.post(
            "/api/v1/auth/token/",
            {"email": self.user.email, "password": self.password},
            format="json",
        )
        revoke_user_credentials(user=self.user, reason="password_reset")

        event = AuditEvent.objects.get(action=AuditEvent.Action.CREDENTIALS_REVOKED)
        self.assertEqual(event.metadata["reason"], "password_reset")
        self.assertEqual(event.metadata["mobile_sessions_revoked"], 1)
        self.assertNotIn(token_response.data["access"], str({"metadata": event.metadata, "changes": event.changes}))
        self.assertNotIn(token_response.data["refresh"], str({"metadata": event.metadata, "changes": event.changes}))

    def test_domain_staff_role_revocation_is_audited_without_token_data(self):
        assignment = StaffRoleAssignment.objects.get(user=self.staff_user)

        assignment.revoke(revoked_by=self.bootstrap)

        event = AuditEvent.objects.get(
            action=AuditEvent.Action.STAFF_ROLE_REVOKED,
            entity_type="StaffRoleAssignment",
            entity_id=str(assignment.id),
        )
        self.assertEqual(event.actor_user, self.bootstrap)
        self.assertTrue(event.metadata["target_user_id"])
        self.assertNotIn("token", str({"metadata": event.metadata, "changes": event.changes}).lower())
