from datetime import date

from django.contrib.auth.tokens import default_token_generator
from django.test import TestCase, override_settings
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from rest_framework.test import APIClient

from accounts.models import MobileAuthSession, User
from memberships.models import Membership
from staff_access.models import StaffRole, StaffRoleAssignment


@override_settings(ROOT_URLCONF="config.urls")
class AuthenticationRevocationTests(TestCase):
    def setUp(self):
        self.password = "Old-password-123!"
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
        self.client = APIClient()

    def token_pair(self, client=None):
        response = (client or self.client).post(
            "/api/v1/auth/token/",
            {"email": self.user.email, "password": self.password},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def login_session(self, path="/api/v1/auth/login/"):
        client = APIClient()
        response = client.post(
            path,
            {"email": self.user.email, "password": self.password},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        return client

    def reset_password(self, new_password="New-password-123!"):
        self.user.refresh_from_db()
        uid = urlsafe_base64_encode(force_bytes(self.user.pk))
        token = default_token_generator.make_token(self.user)
        return self.client.post(
            "/api/v1/auth/password-reset/confirm/",
            {
                "uid": uid,
                "token": token,
                "new_password": new_password,
                "confirm_password": new_password,
            },
            format="json",
        )

    def test_password_reset_revokes_mobile_access_refresh_and_browser_sessions(self):
        tokens = self.token_pair()
        crm_client = self.login_session()
        community_client = self.login_session("/api/v1/community/login/")
        self.assertEqual(crm_client.get("/api/v1/auth/me/").status_code, 200)
        self.assertEqual(community_client.get("/api/v1/community/me/").status_code, 200)

        response = self.reset_password()

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(
            self.client.get(
                "/api/v1/auth/me/",
                HTTP_AUTHORIZATION=f"Bearer {tokens['access']}",
            ).status_code,
            401,
        )
        self.assertEqual(
            self.client.post(
                "/api/v1/auth/token/refresh/",
                {"refresh": tokens["refresh"]},
                format="json",
            ).status_code,
            401,
        )
        self.assertEqual(crm_client.get("/api/v1/auth/me/").status_code, 401)
        self.assertEqual(community_client.get("/api/v1/community/me/").status_code, 401)
        self.assertEqual(
            self.client.post(
                "/api/v1/auth/login/",
                {"email": self.user.email, "password": "New-password-123!"},
                format="json",
            ).status_code,
            200,
        )

    def test_account_deactivation_revokes_credentials_and_reactivation_requires_fresh_login(self):
        tokens = self.token_pair()
        crm_client = self.login_session()

        self.user.is_active = False
        self.user.save(update_fields=["is_active"])

        self.assertEqual(
            self.client.get(
                "/api/v1/auth/me/",
                HTTP_AUTHORIZATION=f"Bearer {tokens['access']}",
            ).status_code,
            401,
        )
        self.assertEqual(
            self.client.post(
                "/api/v1/auth/token/refresh/",
                {"refresh": tokens["refresh"]},
                format="json",
            ).status_code,
            401,
        )
        self.assertEqual(crm_client.get("/api/v1/auth/me/").status_code, 401)

        self.user.is_active = True
        self.user.save(update_fields=["is_active"])
        self.assertEqual(
            self.client.get(
                "/api/v1/auth/me/",
                HTTP_AUTHORIZATION=f"Bearer {tokens['access']}",
            ).status_code,
            401,
        )
        self.assertEqual(
            self.client.post(
                "/api/v1/auth/login/",
                {"email": self.user.email, "password": self.password},
                format="json",
            ).status_code,
            200,
        )

    def test_staff_role_removal_invalidates_crm_session_until_fresh_login(self):
        role = StaffRole.objects.get(code=StaffRole.CRM_MANAGER)
        assignment = StaffRoleAssignment.objects.assign_role(user=self.user, role=role)
        crm_client = self.login_session()
        self.assertEqual(crm_client.get("/api/v1/people/").status_code, 200)

        assignment.revoke()

        self.assertEqual(crm_client.get("/api/v1/people/").status_code, 401)
        assignment.reactivate()
        self.assertEqual(crm_client.get("/api/v1/people/").status_code, 401)

        fresh_client = self.login_session()
        self.assertEqual(fresh_client.get("/api/v1/people/").status_code, 200)

    def test_mobile_logout_revokes_only_the_current_device_session(self):
        first = self.token_pair()
        second = self.token_pair()

        response = self.client.post(
            "/api/v1/auth/token/logout/",
            {"refresh": first["refresh"]},
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {first['access']}",
        )

        self.assertEqual(response.status_code, 204)
        self.assertEqual(
            self.client.get(
                "/api/v1/auth/me/",
                HTTP_AUTHORIZATION=f"Bearer {first['access']}",
            ).status_code,
            401,
        )
        self.assertEqual(
            self.client.post(
                "/api/v1/auth/token/refresh/",
                {"refresh": first["refresh"]},
                format="json",
            ).status_code,
            401,
        )
        self.assertEqual(
            self.client.get(
                "/api/v1/auth/me/",
                HTTP_AUTHORIZATION=f"Bearer {second['access']}",
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.post(
                "/api/v1/auth/token/refresh/",
                {"refresh": second["refresh"]},
                format="json",
            ).status_code,
            200,
        )

    def test_revocation_records_are_server_side_and_do_not_store_raw_tokens(self):
        tokens = self.token_pair()
        self.client.post(
            "/api/v1/auth/token/logout/",
            {"refresh": tokens["refresh"]},
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {tokens['access']}",
        )

        session = MobileAuthSession.objects.get(user=self.user)
        self.assertEqual(session.revoked_reason, "mobile_logout")
        self.assertNotIn(tokens["access"], str(session.__dict__))
        self.assertNotIn(tokens["refresh"], str(session.__dict__))
