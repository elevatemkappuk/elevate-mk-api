import os
from datetime import date
from io import StringIO
from unittest import mock

from django.core.management import call_command, get_commands
from django.db import IntegrityError
from django.test import TestCase, override_settings
from django.conf import settings
from django.contrib.auth.tokens import default_token_generator
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import User
from audit.models import AuditEvent
from memberships.models import Membership
from people.models import Person
from notifications.exceptions import TransactionalEmailError
from staff_access.models import StaffRole, StaffRoleAssignment


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
        self.assertEqual(user.person.record_type, Person.RecordType.BUSINESS)
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
        self.assertEqual(user.person.record_type, Person.RecordType.TECHNICAL)

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
        self.assertEqual(user.person.record_type, Person.RecordType.TECHNICAL)


@override_settings(ROOT_URLCONF="config.urls")
class AuthenticationApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.csrf_client = APIClient(enforce_csrf_checks=True)
        self.password = "testpass123"
        self.user = User.objects.create_user(
            email="Member@Example.com",
            password=self.password,
            person_first_name="Member",
            person_last_name="Example",
        )
        self.membership = Membership.objects.create(
            person=self.user.person,
            status=Membership.Status.ACTIVE,
            joined_at=date(2024, 1, 1),
            membership_source=Membership.Source.STAFF,
        )
        self.staff_user = User.objects.create_user(
            email="staff@example.com",
            password=self.password,
            person=Person.objects.create(
                first_name="Staff", last_name="Example", primary_email="staff@example.com",
                record_type=Person.RecordType.STAFF,
            ),
        )
        self.csrf_url = "/api/v1/auth/csrf/"
        self.login_url = "/api/v1/auth/login/"
        self.token_url = "/api/v1/auth/token/"
        self.token_refresh_url = "/api/v1/auth/token/refresh/"
        self.token_logout_url = "/api/v1/auth/token/logout/"
        self.logout_url = "/api/v1/auth/logout/"
        self.me_url = "/api/v1/auth/me/"
        self.admin_role = StaffRole.objects.get(code=StaffRole.CRM_ADMIN)
        self.manager_role = StaffRole.objects.get(code=StaffRole.CRM_MANAGER)
        self.viewer_role = StaffRole.objects.get(code=StaffRole.CRM_VIEWER)

    def test_csrf_bootstrap_returns_200(self):
        response = self.client.get(self.csrf_url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["detail"], "CSRF cookie set.")
        self.assertTrue(response.data["csrf_token"])

    def test_csrf_bootstrap_sets_csrftoken_cookie(self):
        response = self.client.get(self.csrf_url)

        self.assertIn("csrftoken", response.cookies)

    def test_csrf_bootstrap_does_not_require_authentication(self):
        response = self.client.get(self.csrf_url)

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_login_fails_with_403_when_csrf_enforcement_enabled_and_no_token_is_supplied(self):
        response = self.csrf_client.post(
            self.login_url,
            {"email": "member@example.com", "password": self.password},
            format="json",
        )

        self.assertEqual(response.status_code, 403)
        self.assertNotIn("_auth_user_id", self.csrf_client.session)

    def test_login_succeeds_with_matching_csrf_cookie_and_header(self):
        csrf_response = self.csrf_client.get(self.csrf_url)
        csrf_token = csrf_response.data["csrf_token"]

        response = self.csrf_client.post(
            self.login_url,
            {"email": "member@example.com", "password": self.password},
            format="json",
            HTTP_X_CSRFTOKEN=csrf_token,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["email"], "member@example.com")
        self.assertIn("_auth_user_id", self.csrf_client.session)

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
        self.assertEqual(response.data["staff_roles"], [])
        self.assertIn("_auth_user_id", self.client.session)

    def test_staff_user_receives_active_role_codes_in_login_response(self):
        StaffRoleAssignment.objects.assign_role(user=self.staff_user, role=self.admin_role)

        response = self.client.post(
            self.login_url,
            {"email": "staff@example.com", "password": self.password},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["staff_roles"], [StaffRole.CRM_ADMIN])

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

    def test_token_login_issues_access_and_refresh_tokens_without_creating_session(self):
        response = self.client.post(
            self.token_url,
            {"email": "MEMBER@EXAMPLE.COM", "password": self.password},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["access"])
        self.assertTrue(response.data["refresh"])
        self.assertNotIn("_auth_user_id", self.client.session)
        event = AuditEvent.objects.get(action=AuditEvent.Action.LOGIN_SUCCEEDED)
        self.assertEqual(event.actor_user, self.user)
        self.assertEqual(event.metadata, {"authentication": "jwt"})

    def test_token_login_rejects_invalid_credentials_and_records_failed_audit(self):
        response = self.client.post(
            self.token_url,
            {"email": "member@example.com", "password": "wrongpass"},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["detail"][0], "Invalid email or password.")
        self.assertTrue(AuditEvent.objects.filter(action=AuditEvent.Action.LOGIN_FAILED).exists())

    def test_token_login_rejects_inactive_user(self):
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])

        response = self.client.post(
            self.token_url,
            {"email": "member@example.com", "password": self.password},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["detail"][0], "Invalid email or password.")
        self.assertTrue(AuditEvent.objects.filter(action=AuditEvent.Action.LOGIN_FAILED).exists())

    def test_active_member_token_login_is_allowed(self):
        response = self.client.post(
            self.token_url,
            {"email": "member@example.com", "password": self.password},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["access"])
        self.assertTrue(response.data["refresh"])

    def test_contact_cannot_log_in_with_mobile_jwt(self):
        contact = User.objects.create_user(
            email="contact@example.com",
            password=self.password,
            person_first_name="Contact",
            person_last_name="User",
        )

        response = self.client.post(
            self.token_url,
            {"email": contact.email, "password": self.password},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["detail"][0], "Invalid email or password.")

    def test_former_member_cannot_log_in_with_mobile_jwt(self):
        self.membership.status = Membership.Status.FORMER
        self.membership.ended_at = date(2025, 1, 1)
        self.membership.save(update_fields=["status", "ended_at"])

        response = self.client.post(
            self.token_url,
            {"email": "member@example.com", "password": self.password},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["detail"][0], "Invalid email or password.")

    def test_archived_person_cannot_log_in_with_mobile_jwt(self):
        self.user.person.archived_at = timezone.now()
        self.user.person.save(update_fields=["archived_at"])

        response = self.client.post(
            self.token_url,
            {"email": "member@example.com", "password": self.password},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["detail"][0], "Invalid email or password.")

    def test_access_token_is_rejected_after_membership_revocation(self):
        login_response = self.client.post(
            self.token_url,
            {"email": "member@example.com", "password": self.password},
            format="json",
        )
        self.membership.status = Membership.Status.FORMER
        self.membership.ended_at = date(2025, 1, 1)
        self.membership.save(update_fields=["status", "ended_at"])

        response = self.client.get(
            self.me_url,
            HTTP_AUTHORIZATION=f"Bearer {login_response.data['access']}",
        )

        self.assertEqual(response.status_code, 401)

    def test_refresh_is_rejected_after_membership_revocation(self):
        login_response = self.client.post(
            self.token_url,
            {"email": "member@example.com", "password": self.password},
            format="json",
        )
        self.membership.status = Membership.Status.FORMER
        self.membership.ended_at = date(2025, 1, 1)
        self.membership.save(update_fields=["status", "ended_at"])

        response = self.client.post(
            self.token_refresh_url,
            {"refresh": login_response.data["refresh"]},
            format="json",
        )

        self.assertEqual(response.status_code, 401)

    def test_refresh_is_rejected_after_person_archival(self):
        login_response = self.client.post(
            self.token_url,
            {"email": "member@example.com", "password": self.password},
            format="json",
        )
        self.user.person.archived_at = timezone.now()
        self.user.person.save(update_fields=["archived_at"])

        response = self.client.post(
            self.token_refresh_url,
            {"refresh": login_response.data["refresh"]},
            format="json",
        )

        self.assertEqual(response.status_code, 401)

    def test_refresh_is_rejected_after_user_is_disabled(self):
        login_response = self.client.post(
            self.token_url,
            {"email": "member@example.com", "password": self.password},
            format="json",
        )
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])

        response = self.client.post(
            self.token_refresh_url,
            {"refresh": login_response.data["refresh"]},
            format="json",
        )

        self.assertEqual(response.status_code, 401)

    def test_jwt_logout_can_revoke_refresh_after_membership_revocation(self):
        login_response = self.client.post(
            self.token_url,
            {"email": "member@example.com", "password": self.password},
            format="json",
        )
        self.membership.status = Membership.Status.FORMER
        self.membership.ended_at = date(2025, 1, 1)
        self.membership.save(update_fields=["status", "ended_at"])

        logout_response = self.client.post(
            self.token_logout_url,
            {"refresh": login_response.data["refresh"]},
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {login_response.data['access']}",
        )

        self.assertEqual(logout_response.status_code, 204)
        refresh_response = self.client.post(
            self.token_refresh_url,
            {"refresh": login_response.data["refresh"]},
            format="json",
        )
        self.assertEqual(refresh_response.status_code, 401)

    def test_rotated_refresh_token_is_blacklisted(self):
        login_response = self.client.post(
            self.token_url,
            {"email": "member@example.com", "password": self.password},
            format="json",
        )
        original_refresh = login_response.data["refresh"]

        refresh_response = self.client.post(
            self.token_refresh_url,
            {"refresh": original_refresh},
            format="json",
        )

        self.assertEqual(refresh_response.status_code, 200)
        self.assertTrue(refresh_response.data["access"])
        self.assertTrue(refresh_response.data["refresh"])
        self.assertNotEqual(refresh_response.data["refresh"], original_refresh)

        reused_response = self.client.post(
            self.token_refresh_url,
            {"refresh": original_refresh},
            format="json",
        )

        self.assertEqual(reused_response.status_code, 401)

    def test_valid_jwt_access_token_authenticates_me(self):
        login_response = self.client.post(
            self.token_url,
            {"email": "member@example.com", "password": self.password},
            format="json",
        )

        response = self.client.get(
            self.me_url,
            HTTP_AUTHORIZATION=f"Bearer {login_response.data['access']}",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["id"], self.user.id)
        self.assertEqual(response.data["email"], "member@example.com")

    def test_refresh_token_is_rejected_as_bearer_access_credential(self):
        login_response = self.client.post(
            self.token_url,
            {"email": "member@example.com", "password": self.password},
            format="json",
        )

        response = self.client.get(
            self.me_url,
            HTTP_AUTHORIZATION=f"Bearer {login_response.data['refresh']}",
        )

        self.assertEqual(response.status_code, 401)

    def test_jwt_logout_blacklists_refresh_token_and_records_safe_audit_event(self):
        login_response = self.client.post(
            self.token_url,
            {"email": "member@example.com", "password": self.password},
            format="json",
        )
        access = login_response.data["access"]
        refresh = login_response.data["refresh"]

        response = self.client.post(
            self.token_logout_url,
            {"refresh": refresh},
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {access}",
        )

        self.assertEqual(response.status_code, 204)
        refresh_response = self.client.post(
            self.token_refresh_url,
            {"refresh": refresh},
            format="json",
        )
        self.assertEqual(refresh_response.status_code, 401)

        event = AuditEvent.objects.get(action=AuditEvent.Action.LOGOUT)
        self.assertEqual(event.actor_user, self.user)
        self.assertEqual(event.metadata["authentication"], "jwt")
        self.assertTrue(event.metadata["mobile_session_revoked"])
        self.assertTrue(event.metadata["mobile_session_id"])
        self.assertEqual(event.metadata["revocation_reason"], "mobile_logout")
        self.assertTrue(event.metadata["refresh_token_revoked"])
        self.assertNotIn(refresh, str(event.metadata))

    def test_jwt_logout_rejects_invalid_expired_and_already_blacklisted_refresh_tokens(self):
        login_response = self.client.post(
            self.token_url,
            {"email": "member@example.com", "password": self.password},
            format="json",
        )
        access = login_response.data["access"]
        refresh = login_response.data["refresh"]
        headers = {"HTTP_AUTHORIZATION": f"Bearer {access}"}

        invalid_response = self.client.post(
            self.token_logout_url,
            {"refresh": "not-a-refresh-token"},
            format="json",
            **headers,
        )
        self.assertEqual(invalid_response.status_code, 401)
        self.assertEqual(invalid_response.data["code"], "invalid_refresh_token")

        expired = RefreshToken.for_user(self.user)
        expired["exp"] = int(timezone.now().timestamp()) - 1
        expired_response = self.client.post(
            self.token_logout_url,
            {"refresh": str(expired)},
            format="json",
            **headers,
        )
        self.assertEqual(expired_response.status_code, 401)
        self.assertEqual(expired_response.data["code"], "invalid_refresh_token")

        first_logout = self.client.post(
            self.token_logout_url,
            {"refresh": refresh},
            format="json",
            **headers,
        )
        self.assertEqual(first_logout.status_code, 204)
        repeated_logout = self.client.post(
            self.token_logout_url,
            {"refresh": refresh},
            format="json",
            **headers,
        )
        self.assertEqual(repeated_logout.status_code, 401)
        self.assertEqual(repeated_logout.data["code"], "invalid_refresh_token")

    def test_jwt_logout_requires_jwt_access_authentication(self):
        login_response = self.client.post(
            self.token_url,
            {"email": "member@example.com", "password": self.password},
            format="json",
        )

        anonymous_response = self.client.post(
            self.token_logout_url,
            {"refresh": login_response.data["refresh"]},
            format="json",
        )
        self.assertEqual(anonymous_response.status_code, 401)

        self.client.post(
            self.login_url,
            {"email": "member@example.com", "password": self.password},
            format="json",
        )
        session_response = self.client.post(
            self.token_logout_url,
            {"refresh": login_response.data["refresh"]},
            format="json",
        )
        self.assertEqual(session_response.status_code, 401)

    def test_jwt_logout_rejects_a_refresh_token_owned_by_another_user(self):
        other_user = User.objects.create_user(
            email="other@example.com",
            password=self.password,
            person_first_name="Other",
            person_last_name="User",
        )
        other_refresh = RefreshToken.for_user(other_user)
        login_response = self.client.post(
            self.token_url,
            {"email": "member@example.com", "password": self.password},
            format="json",
        )

        response = self.client.post(
            self.token_logout_url,
            {"refresh": str(other_refresh)},
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {login_response.data['access']}",
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["code"], "refresh_token_user_mismatch")

    def test_user_linked_to_technical_person_can_still_authenticate(self):
        technical_user = User.objects.create_user(
            email="technical@example.com",
            password=self.password,
            person_first_name="Technical",
            person_last_name="User",
            person_record_type=Person.RecordType.TECHNICAL,
        )

        response = self.client.post(
            self.login_url,
            {"email": "technical@example.com", "password": self.password},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["id"], technical_user.id)
        self.assertEqual(response.data["person"]["id"], technical_user.person_id)
        self.assertEqual(response.data["staff_roles"], [])

        me_response = self.client.get(self.me_url)
        self.assertEqual(me_response.status_code, 200)
        self.assertIsNone(me_response.data["membership"])

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
        self.assertEqual(response.data["staff_roles"], [])
        self.assertEqual(response.data["membership"]["status"], Membership.Status.ACTIVE)
        self.assertNotIn("password", response.data)

    def test_staff_user_receives_active_role_codes_in_me(self):
        StaffRoleAssignment.objects.assign_role(user=self.staff_user, role=self.admin_role)
        self.client.post(
            self.login_url,
            {"email": "staff@example.com", "password": self.password},
            format="json",
        )

        response = self.client.get(self.me_url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["staff_roles"], [StaffRole.CRM_ADMIN])

    def test_multiple_active_roles_are_returned_deterministically(self):
        StaffRoleAssignment.objects.assign_role(user=self.staff_user, role=self.manager_role)
        StaffRoleAssignment.objects.assign_role(user=self.staff_user, role=self.admin_role)
        self.client.post(
            self.login_url,
            {"email": "staff@example.com", "password": self.password},
            format="json",
        )

        response = self.client.get(self.me_url)

        self.assertEqual(
            response.data["staff_roles"],
            [StaffRole.CRM_ADMIN, StaffRole.CRM_MANAGER],
        )

    def test_revoked_roles_are_excluded_from_me(self):
        assignment = StaffRoleAssignment.objects.assign_role(user=self.staff_user, role=self.admin_role)
        other_admin = User.objects.create_user(
            email="other-admin@example.com",
            password="testpass123",
            person=Person.objects.create(first_name="Other", last_name="Admin", record_type=Person.RecordType.STAFF),
        )
        StaffRoleAssignment.objects.assign_role(user=other_admin, role=self.admin_role)
        assignment.revoke()
        self.client.post(
            self.login_url,
            {"email": "staff@example.com", "password": self.password},
            format="json",
        )

        response = self.client.get(self.me_url)

        self.assertEqual(response.data["staff_roles"], [])

    def test_inactive_staff_roles_are_excluded_from_me(self):
        self.viewer_role.is_active = False
        self.viewer_role.save(update_fields=["is_active"])
        StaffRoleAssignment.objects.assign_role(user=self.staff_user, role=self.viewer_role)
        self.client.post(
            self.login_url,
            {"email": "staff@example.com", "password": self.password},
            format="json",
        )

        response = self.client.get(self.me_url)

        self.assertEqual(response.data["staff_roles"], [])

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
        self.assertIn("/api/v1/auth/csrf/", response.json()["paths"])
        me_schema = response.json()["components"]["schemas"]["CurrentUser"]
        self.assertIn("staff_roles", me_schema["properties"])
        login_schema = response.json()["paths"]["/api/v1/auth/login/"]["post"]["responses"]["200"][
            "content"
        ]["application/json"]["schema"]["$ref"]
        self.assertEqual(login_schema, "#/components/schemas/CurrentUser")

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


@override_settings(
    ROOT_URLCONF="config.urls",
    CORS_ALLOWED_ORIGINS=["http://localhost:4200", "http://localhost:4201"],
    CORS_ALLOW_CREDENTIALS=True,
    CSRF_TRUSTED_ORIGINS=["http://localhost:4200", "http://localhost:4201"],
    SESSION_COOKIE_HTTPONLY=True,
    CSRF_COOKIE_HTTPONLY=False,
    SESSION_COOKIE_SAMESITE="Lax",
    CSRF_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=False,
    CSRF_COOKIE_SECURE=False,
)
class LocalBrowserDevelopmentConfigurationTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.login_url = "/api/v1/auth/login/"

    def test_angular_localhost_origin_is_permitted_by_cors(self):
        response = self.client.options(
            self.login_url,
            HTTP_ORIGIN="http://localhost:4200",
            HTTP_ACCESS_CONTROL_REQUEST_METHOD="POST",
            HTTP_ACCESS_CONTROL_REQUEST_HEADERS="content-type,x-csrftoken",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Access-Control-Allow-Origin"], "http://localhost:4200")
        self.assertEqual(response["Access-Control-Allow-Credentials"], "true")

    def test_community_join_preflight_allows_required_headers(self):
        response = self.client.options(
            "/api/v1/community/join/",
            HTTP_ORIGIN="http://localhost:4201",
            HTTP_ACCESS_CONTROL_REQUEST_METHOD="POST",
            HTTP_ACCESS_CONTROL_REQUEST_HEADERS="content-type,x-csrftoken,idempotency-key",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Access-Control-Allow-Origin"], "http://localhost:4201")
        self.assertEqual(response["Access-Control-Allow-Credentials"], "true")
        self.assertIn("idempotency-key", response["Access-Control-Allow-Headers"])
        self.assertIn("x-csrftoken", response["Access-Control-Allow-Headers"])

    def test_community_post_error_remains_readable_to_allowed_origin(self):
        csrf_client = APIClient(enforce_csrf_checks=True)
        response = csrf_client.post(
            "/api/v1/community/join/",
            {},
            format="json",
            HTTP_ORIGIN="http://localhost:4201",
            HTTP_IDEMPOTENCY_KEY="cors-check",
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response["Access-Control-Allow-Origin"], "http://localhost:4201")
        self.assertEqual(response["Access-Control-Allow-Credentials"], "true")

    def test_unrelated_origin_is_not_permitted_by_cors(self):
        response = self.client.options(
            self.login_url,
            HTTP_ORIGIN="http://malicious.example",
            HTTP_ACCESS_CONTROL_REQUEST_METHOD="POST",
            HTTP_ACCESS_CONTROL_REQUEST_HEADERS="content-type,x-csrftoken",
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Access-Control-Allow-Origin", response)
        self.assertNotIn("Access-Control-Allow-Credentials", response)

    def test_local_csrf_and_cookie_settings_match_browser_contract(self):
        self.assertEqual(settings.CORS_ALLOWED_ORIGINS, ["http://localhost:4200", "http://localhost:4201"])
        self.assertTrue(settings.CORS_ALLOW_CREDENTIALS)
        self.assertEqual(settings.CSRF_TRUSTED_ORIGINS, ["http://localhost:4200", "http://localhost:4201"])
        self.assertTrue(settings.SESSION_COOKIE_HTTPONLY)
        self.assertFalse(settings.CSRF_COOKIE_HTTPONLY)
        self.assertEqual(settings.SESSION_COOKIE_SAMESITE, "Lax")
        self.assertEqual(settings.CSRF_COOKIE_SAMESITE, "Lax")
        self.assertFalse(settings.SESSION_COOKIE_SECURE)
        self.assertFalse(settings.CSRF_COOKIE_SECURE)


@override_settings(
    ROOT_URLCONF="config.urls",
    CRM_FRONTEND_URL="http://localhost:4200/",
    BREVO_PASSWORD_RESET_TEMPLATE_ID="42",
)
class PasswordResetApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            email="reset@example.com",
            password="Old-password-123!",
            person_first_name="Reset",
            person_last_name="User",
        )
        self.request_url = "/api/v1/auth/password-reset/"
        self.confirm_url = "/api/v1/auth/password-reset/confirm/"

    @mock.patch("accounts.views.send_transactional_email")
    def test_eligible_user_receives_generic_response_and_template_notification(self, send_email):
        response = self.client.post(self.request_url, {"email": " RESET@EXAMPLE.COM "}, format="json")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["detail"], "If an account exists for that email address, password reset instructions have been sent.")
        kwargs = send_email.call_args.kwargs
        self.assertEqual(kwargs["recipient_email"], self.user.email)
        self.assertEqual(kwargs["template_id"], "42")
        self.assertTrue(kwargs["template_params"]["reset_url"].startswith("http://localhost:4200/reset-password/"))
        self.assertNotIn("reset_url", response.data)

    @mock.patch("accounts.views.send_transactional_email")
    def test_ineligible_or_missing_users_have_the_same_response_without_delivery(self, send_email):
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        inactive = self.client.post(self.request_url, {"email": self.user.email}, format="json")
        missing = self.client.post(self.request_url, {"email": "missing@example.com"}, format="json")

        self.assertEqual(inactive.data, missing.data)
        send_email.assert_not_called()

    @mock.patch("accounts.views.send_transactional_email", side_effect=TransactionalEmailError("delivery failed"))
    def test_delivery_failure_is_not_exposed(self, _send_email):
        response = self.client.post(self.request_url, {"email": self.user.email}, format="json")

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("delivery", str(response.data).lower())

    def test_valid_token_resets_password_once_and_writes_safe_audit_event(self):
        uid = urlsafe_base64_encode(force_bytes(self.user.pk))
        token = default_token_generator.make_token(self.user)
        response = self.client.post(self.confirm_url, {
            "uid": uid, "token": token, "new_password": "New-password-123!", "confirm_password": "New-password-123!",
        }, format="json")

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertFalse(self.user.check_password("Old-password-123!"))
        self.assertTrue(self.user.check_password("New-password-123!"))
        event = AuditEvent.objects.get(action=AuditEvent.Action.PASSWORD_RESET)
        self.assertIsNone(event.actor_user)
        self.assertNotIn("password", str(event.metadata).lower())
        self.assertNotIn(token, str(event.metadata))
        repeated = self.client.post(self.confirm_url, {
            "uid": uid, "token": token, "new_password": "Another-password-123!", "confirm_password": "Another-password-123!",
        }, format="json")
        self.assertEqual(repeated.data["code"], "invalid_password_reset_token")
        self.assertEqual(AuditEvent.objects.filter(action=AuditEvent.Action.PASSWORD_RESET).count(), 1)

    def test_invalid_token_and_inactive_account_use_controlled_response(self):
        uid = urlsafe_base64_encode(force_bytes(self.user.pk))
        invalid = self.client.post(self.confirm_url, {"uid": uid, "token": "invalid", "new_password": "New-password-123!", "confirm_password": "New-password-123!"}, format="json")
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        inactive = self.client.post(self.confirm_url, {"uid": uid, "token": default_token_generator.make_token(self.user), "new_password": "New-password-123!", "confirm_password": "New-password-123!"}, format="json")

        self.assertEqual(invalid.data, inactive.data)
        self.assertEqual(invalid.data["code"], "invalid_password_reset_token")

    def test_password_mismatch_and_validators_are_enforced(self):
        uid = urlsafe_base64_encode(force_bytes(self.user.pk))
        token = default_token_generator.make_token(self.user)
        mismatch = self.client.post(self.confirm_url, {"uid": uid, "token": token, "new_password": "New-password-123!", "confirm_password": "different"}, format="json")
        weak = self.client.post(self.confirm_url, {"uid": uid, "token": token, "new_password": "123", "confirm_password": "123"}, format="json")

        self.assertIn("confirm_password", mismatch.data)
        self.assertIn("new_password", weak.data)
