from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from community.views import CommunityLoginView
from memberships.models import Membership
from people.models import Person
from staff_access.models import StaffRole, StaffRoleAssignment


@override_settings(COMMUNITY_LOGIN_THROTTLE_RATE="10/hour")
class CommunityLoginApiTests(TestCase):
    login_url = "/api/v1/community/login/"

    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.csrf_client = APIClient(enforce_csrf_checks=True)
        self.password = "Community-password-123!"
        self.person = Person.objects.create(
            first_name="Amina",
            last_name="Zulu",
            primary_email="member@example.com",
        )
        Membership.objects.create(
            person=self.person,
            status=Membership.Status.ACTIVE,
            joined_at=timezone.localdate(),
            membership_source=Membership.Source.COMMUNITY_PLATFORM,
        )
        self.user = User.objects.create_user(
            email=self.person.primary_email,
            password=self.password,
            person=self.person,
        )

    def login(self, client=None, email="member@example.com", password=None):
        return (client or self.client).post(
            self.login_url,
            {"email": email, "password": password or self.password},
            format="json",
        )

    def test_active_community_user_logs_in_with_minimal_dto_and_session(self):
        response = self.login()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.data), {"id", "first_name", "last_name"})
        self.assertEqual(response.data["id"], self.user.id)
        self.assertNotIn("staff_roles", response.data)
        self.assertIn("_auth_user_id", self.client.session)
        self.assertEqual(self.client.get("/api/v1/community/me/").status_code, 200)

    def test_login_normalizes_email_case_and_whitespace(self):
        response = self.login(email="  MEMBER@EXAMPLE.COM ")

        self.assertEqual(response.status_code, 200)

    def test_wrong_email_and_password_use_same_generic_failure_without_session(self):
        wrong_email = self.login(email="unknown@example.com")
        wrong_password = self.login(password="wrong-password")

        self.assertEqual(wrong_email.status_code, 400)
        self.assertEqual(wrong_password.status_code, 400)
        self.assertEqual(wrong_email.data, {"code": "INVALID_CREDENTIALS", "detail": "Email or password is incorrect."})
        self.assertEqual(wrong_password.data, wrong_email.data)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_inactive_user_uses_generic_failure_without_session(self):
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])

        response = self.login()

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "INVALID_CREDENTIALS")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_joined_but_not_activated_person_uses_generic_failure(self):
        self.user.delete()

        response = self.login()

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "INVALID_CREDENTIALS")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_ineligible_authenticated_user_receives_access_unavailable_without_session(self):
        self.person.membership.status = Membership.Status.FORMER
        self.person.membership.save(update_fields=["status"])

        response = self.login()

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data, {"code": "COMMUNITY_ACCESS_UNAVAILABLE", "detail": "Community access is not available for this account."})
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_archived_person_is_not_admitted_to_community(self):
        self.person.archived_at = timezone.now()
        self.person.save(update_fields=["archived_at"])

        response = self.login()

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["code"], "COMMUNITY_ACCESS_UNAVAILABLE")

    def test_non_business_person_is_not_admitted_to_community(self):
        self.person.record_type = Person.RecordType.TECHNICAL
        self.person.save(update_fields=["record_type"])

        response = self.login()

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["code"], "COMMUNITY_ACCESS_UNAVAILABLE")

    def test_staff_user_with_active_membership_receives_no_staff_roles(self):
        role = StaffRole.objects.get(code=StaffRole.CRM_ADMIN)
        StaffRoleAssignment.objects.assign_role(user=self.user, role=role)

        response = self.login()

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("staff_roles", response.data)

    def test_community_eligibility_remains_dynamic_after_login(self):
        self.assertEqual(self.login().status_code, 200)
        self.person.membership.status = Membership.Status.FORMER
        self.person.membership.save(update_fields=["status"])

        response = self.client.get("/api/v1/community/me/")

        self.assertEqual(response.status_code, 403)

    def test_ordinary_community_user_remains_forbidden_from_crm_people_endpoint(self):
        self.assertEqual(self.login().status_code, 200)

        response = self.client.get("/api/v1/people/")

        self.assertEqual(response.status_code, 403)

    def test_login_requires_csrf(self):
        response = self.login(client=self.csrf_client)

        self.assertEqual(response.status_code, 403)
        self.assertNotIn("_auth_user_id", self.csrf_client.session)

    def test_login_uses_dedicated_throttle_scope(self):
        self.assertEqual(CommunityLoginView.throttle_scope, "community_login")
