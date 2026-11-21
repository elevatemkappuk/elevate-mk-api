from datetime import date

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import User
from memberships.models import Membership
from people.models import Person
from staff_access.models import StaffRole, StaffRoleAssignment


@override_settings(ROOT_URLCONF="config.urls")
class CrmAuthenticationIsolationTests(TestCase):
    def setUp(self):
        self.password = "testpass123"
        self.staff_user = User.objects.create_user(
            email="crm-staff@example.com",
            password=self.password,
            person_first_name="CRM",
            person_last_name="Staff",
        )
        Membership.objects.create(
            person=self.staff_user.person,
            status=Membership.Status.ACTIVE,
            joined_at=date(2024, 1, 1),
            membership_source=Membership.Source.STAFF,
        )
        self.no_membership_staff = User.objects.create_user(
            email="crm-contact@example.com",
            password=self.password,
            person_first_name="CRM",
            person_last_name="Contact",
        )
        self.non_staff_member = User.objects.create_user(
            email="member@example.com",
            password=self.password,
            person_first_name="Community",
            person_last_name="Member",
        )
        Membership.objects.create(
            person=self.non_staff_member.person,
            status=Membership.Status.ACTIVE,
            joined_at=date(2024, 1, 1),
            membership_source=Membership.Source.COMMUNITY_PLATFORM,
        )
        self.target_person = Person.objects.create(
            first_name="Target",
            last_name="Person",
            primary_email="target@example.com",
        )
        self.admin_role = StaffRole.objects.get(code=StaffRole.CRM_ADMIN)
        self.viewer_role = StaffRole.objects.get(code=StaffRole.CRM_VIEWER)
        StaffRoleAssignment.objects.assign_role(user=self.staff_user, role=self.admin_role)
        StaffRoleAssignment.objects.assign_role(user=self.no_membership_staff, role=self.viewer_role)

        self.people_url = "/api/v1/people/"
        self.dashboard_url = "/api/v1/dashboard/"
        self.archive_url = f"/api/v1/people/{self.target_person.id}/archive/"
        self.me_url = "/api/v1/auth/me/"

    def mobile_access_token(self, user):
        response = self.client.post(
            "/api/v1/auth/token/",
            {"email": user.email, "password": self.password},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        return response.data["access"]

    def session_client(self, user, *, enforce_csrf_checks=False):
        client = APIClient(enforce_csrf_checks=enforce_csrf_checks)
        response = client.post(
            "/api/v1/auth/login/",
            {"email": user.email, "password": self.password},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        return client

    def test_staff_session_can_access_crm_endpoint(self):
        response = self.session_client(self.staff_user).get(self.people_url)

        self.assertEqual(response.status_code, 200)

    def test_mobile_jwt_is_rejected_by_crm_endpoint_even_with_staff_role(self):
        access = self.mobile_access_token(self.staff_user)

        response = self.client.get(
            self.people_url,
            HTTP_AUTHORIZATION=f"Bearer {access}",
        )

        self.assertIn(response.status_code, (401, 403))

    def test_mobile_jwt_without_staff_role_is_rejected_by_crm_endpoint(self):
        access = self.mobile_access_token(self.non_staff_member)

        response = self.client.get(
            self.people_url,
            HTTP_AUTHORIZATION=f"Bearer {access}",
        )

        self.assertIn(response.status_code, (401, 403))

    def test_staff_without_membership_retains_authorised_crm_session_access(self):
        response = self.session_client(self.no_membership_staff).get(self.people_url)

        self.assertEqual(response.status_code, 200)

    def test_authenticated_session_without_required_staff_role_is_denied(self):
        response = self.session_client(self.non_staff_member).get(self.people_url)

        self.assertEqual(response.status_code, 403)

    def test_anonymous_request_is_denied(self):
        response = self.client.get(self.people_url)

        self.assertEqual(response.status_code, 401)

    def test_session_authenticated_crm_mutation_still_requires_csrf(self):
        authenticated_client = self.session_client(self.staff_user)
        client = APIClient(enforce_csrf_checks=True)
        client.cookies.update(authenticated_client.cookies)

        response = client.post(self.archive_url, {}, format="json")

        self.assertEqual(response.status_code, 403)

    def test_shared_mobile_identity_endpoint_still_accepts_mobile_jwt(self):
        access = self.mobile_access_token(self.staff_user)

        response = self.client.get(
            self.me_url,
            HTTP_AUTHORIZATION=f"Bearer {access}",
        )

        self.assertEqual(response.status_code, 200)

    def test_crm_moderation_endpoint_rejects_mobile_jwt(self):
        access = self.mobile_access_token(self.staff_user)

        response = self.client.get(
            "/api/v1/community/moderation/reports/",
            HTTP_AUTHORIZATION=f"Bearer {access}",
        )

        self.assertIn(response.status_code, (401, 403))
