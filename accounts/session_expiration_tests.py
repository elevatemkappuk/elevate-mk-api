import time
from datetime import date

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.authentication import (
    CRM_SESSION_ABSOLUTE_TIMEOUT_SECONDS,
    CRM_SESSION_CREATED_AT,
    CRM_SESSION_IDLE_TIMEOUT_SECONDS,
    CRM_SESSION_LAST_ACTIVITY,
)
from accounts.models import User
from memberships.models import Membership
from staff_access.models import StaffRole, StaffRoleAssignment


@override_settings(ROOT_URLCONF="config.urls")
class CrmSessionExpirationTests(TestCase):
    def setUp(self):
        self.password = "Password-123!"
        self.user = User.objects.create_user(
            email="staff@example.com",
            password=self.password,
            person_first_name="Staff",
            person_last_name="User",
        )
        Membership.objects.create(
            person=self.user.person,
            status=Membership.Status.ACTIVE,
            joined_at=date(2024, 1, 1),
            membership_source=Membership.Source.COMMUNITY_PLATFORM,
        )
        StaffRoleAssignment.objects.assign_role(
            user=self.user,
            role=StaffRole.objects.get(code=StaffRole.CRM_MANAGER),
        )

    def login_session(self, path="/api/v1/auth/login/"):
        client = APIClient()
        response = client.post(
            path,
            {"email": self.user.email, "password": self.password},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        return client

    def set_session_times(self, client, *, created_at, last_activity):
        session = client.session
        session[CRM_SESSION_CREATED_AT] = created_at
        session[CRM_SESSION_LAST_ACTIVITY] = last_activity
        session.save()

    def test_crm_activity_extends_idle_window_without_extending_absolute_lifetime(self):
        client = self.login_session()
        session_before = client.session
        created_at = session_before[CRM_SESSION_CREATED_AT]
        last_activity = session_before[CRM_SESSION_LAST_ACTIVITY]

        response = client.get("/api/v1/auth/me/")

        self.assertEqual(response.status_code, 200, response.data)
        session_after = client.session
        self.assertEqual(session_after[CRM_SESSION_CREATED_AT], created_at)
        self.assertGreaterEqual(session_after[CRM_SESSION_LAST_ACTIVITY], last_activity)

    def test_idle_expiration_invalidates_crm_session(self):
        client = self.login_session()
        now = time.time()
        self.set_session_times(
            client,
            created_at=now,
            last_activity=now - CRM_SESSION_IDLE_TIMEOUT_SECONDS - 1,
        )

        response = client.get("/api/v1/auth/me/")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(client.get("/api/v1/people/").status_code, 401)

    def test_absolute_expiration_invalidates_crm_session_even_when_recently_active(self):
        client = self.login_session()
        now = time.time()
        self.set_session_times(
            client,
            created_at=now - CRM_SESSION_ABSOLUTE_TIMEOUT_SECONDS - 1,
            last_activity=now,
        )

        response = client.get("/api/v1/auth/me/")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(client.get("/api/v1/people/").status_code, 401)

    def test_community_session_is_not_subject_to_crm_expiration(self):
        client = self.login_session("/api/v1/community/login/")
        now = time.time()
        self.set_session_times(
            client,
            created_at=now - CRM_SESSION_ABSOLUTE_TIMEOUT_SECONDS - 1,
            last_activity=now - CRM_SESSION_IDLE_TIMEOUT_SECONDS - 1,
        )

        response = client.get("/api/v1/community/me/")

        self.assertEqual(response.status_code, 200, response.data)
