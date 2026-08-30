from datetime import date

from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from memberships.models import Membership
from people.models import Person
from staff_access.models import StaffRole, StaffRoleAssignment


class MembershipModelTests(TestCase):
    def test_person_may_exist_without_membership(self):
        person = Person.objects.create(first_name="Taylor", last_name="Jordan")

        self.assertEqual(person.first_name, "Taylor")
        self.assertFalse(hasattr(person, "membership"))

    def test_membership_links_to_exactly_one_person(self):
        person = Person.objects.create(first_name="Amina", last_name="Zulu")
        membership = Membership.objects.create(
            person=person,
            status=Membership.Status.ACTIVE,
            joined_at=date(2024, 4, 1),
            membership_source=Membership.Source.STAFF,
        )

        self.assertEqual(membership.person, person)
        self.assertEqual(person.membership, membership)

    def test_second_membership_for_same_person_is_rejected(self):
        person = Person.objects.create(first_name="Casey", last_name="Morgan")
        Membership.objects.create(
            person=person,
            status=Membership.Status.ACTIVE,
            joined_at=date(2024, 1, 1),
            membership_source=Membership.Source.WEBSITE_FORM,
        )

        with self.assertRaises(IntegrityError):
            Membership.objects.create(
                person=person,
                status=Membership.Status.FORMER,
                joined_at=date(2023, 1, 1),
                ended_at=date(2023, 12, 31),
                membership_source=Membership.Source.OTHER,
            )

    def test_active_membership_valid_case(self):
        membership = Membership(
            person=Person.objects.create(first_name="Active", last_name="Member"),
            status=Membership.Status.ACTIVE,
            joined_at=date(2024, 6, 1),
            membership_source=Membership.Source.WEBSITE_FORM,
        )

        membership.full_clean()

    def test_former_membership_valid_case(self):
        membership = Membership(
            person=Person.objects.create(first_name="Former", last_name="Member"),
            status=Membership.Status.FORMER,
            joined_at=date(2020, 6, 1),
            ended_at=date(2024, 6, 1),
            membership_source=Membership.Source.COMMUNITY_PLATFORM,
        )

        membership.full_clean()

    def test_allowed_sources_are_supported(self):
        self.assertEqual(
            {choice for choice, _label in Membership.Source.choices},
            {"WEBSITE_FORM", "STAFF", "COMMUNITY_PLATFORM", "OTHER"},
        )

    def test_invalid_status_is_rejected(self):
        membership = Membership(
            person=Person.objects.create(first_name="Invalid", last_name="Status"),
            status="PENDING",
            joined_at=date(2024, 1, 1),
            membership_source=Membership.Source.STAFF,
        )

        with self.assertRaises(ValidationError):
            membership.full_clean()

    def test_invalid_source_is_rejected(self):
        membership = Membership(
            person=Person.objects.create(first_name="Invalid", last_name="Source"),
            status=Membership.Status.ACTIVE,
            joined_at=date(2024, 1, 1),
            membership_source="IMPORT",
        )

        with self.assertRaises(ValidationError):
            membership.full_clean()

    def test_ended_at_before_joined_at_is_rejected(self):
        membership = Membership(
            person=Person.objects.create(first_name="Date", last_name="Mismatch"),
            status=Membership.Status.FORMER,
            joined_at=date(2024, 6, 1),
            ended_at=date(2024, 5, 31),
            membership_source=Membership.Source.STAFF,
        )

        with self.assertRaises(ValidationError) as error:
            membership.full_clean()

        self.assertIn("ended_at", error.exception.message_dict)

    def test_active_membership_cannot_have_ended_at(self):
        membership = Membership(
            person=Person.objects.create(first_name="Still", last_name="Active"),
            status=Membership.Status.ACTIVE,
            joined_at=date(2024, 6, 1),
            ended_at=date(2024, 6, 30),
            membership_source=Membership.Source.STAFF,
        )

        with self.assertRaises(ValidationError) as error:
            membership.full_clean()

        self.assertIn("ended_at", error.exception.message_dict)

    def test_former_membership_requires_ended_at(self):
        membership = Membership(
            person=Person.objects.create(first_name="Former", last_name="MissingDate"),
            status=Membership.Status.FORMER,
            joined_at=date(2024, 6, 1),
            membership_source=Membership.Source.STAFF,
        )

        with self.assertRaises(ValidationError) as error:
            membership.full_clean()

        self.assertIn("ended_at", error.exception.message_dict)


class MembershipIndependenceTests(TestCase):
    def test_membership_does_not_require_user(self):
        person = Person.objects.create(first_name="No", last_name="Account")
        membership = Membership.objects.create(
            person=person,
            status=Membership.Status.ACTIVE,
            joined_at=date(2024, 4, 1),
            membership_source=Membership.Source.STAFF,
        )

        self.assertEqual(membership.person, person)
        self.assertFalse(hasattr(person, "user"))

    def test_user_existence_does_not_imply_membership(self):
        user = User.objects.create_user(
            email="memberless@example.com",
            password="testpass123",
            person_first_name="Memberless",
            person_last_name="User",
        )

        self.assertFalse(hasattr(user.person, "membership"))

    def test_crm_staff_role_does_not_imply_membership(self):
        user = User.objects.create_user(
            email="staff@example.com",
            password="testpass123",
            person_first_name="Staff",
            person_last_name="Only",
        )
        role = StaffRole.objects.get(code=StaffRole.CRM_ADMIN)
        StaffRoleAssignment.objects.assign_role(user=user, role=role)

        self.assertFalse(hasattr(user.person, "membership"))


@override_settings(ROOT_URLCONF="config.urls")
class MembershipApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.url_template = "/api/v1/people/{person_id}/membership/"

        self.non_staff_user = User.objects.create_user(
            email="nonstaff@example.com",
            password="testpass123",
            person_first_name="Non",
            person_last_name="Staff",
        )
        self.admin_user = User.objects.create_user(
            email="admin@example.com",
            password="testpass123",
            person_first_name="Admin",
            person_last_name="User",
        )
        self.manager_user = User.objects.create_user(
            email="manager@example.com",
            password="testpass123",
            person_first_name="Manager",
            person_last_name="User",
        )
        self.viewer_user = User.objects.create_user(
            email="viewer@example.com",
            password="testpass123",
            person_first_name="Viewer",
            person_last_name="User",
        )

        admin_role = StaffRole.objects.get(code=StaffRole.CRM_ADMIN)
        manager_role = StaffRole.objects.get(code=StaffRole.CRM_MANAGER)
        viewer_role = StaffRole.objects.get(code=StaffRole.CRM_VIEWER)
        StaffRoleAssignment.objects.assign_role(user=self.admin_user, role=admin_role)
        StaffRoleAssignment.objects.assign_role(user=self.manager_user, role=manager_role)
        StaffRoleAssignment.objects.assign_role(user=self.viewer_user, role=viewer_role)

        self.business_person = Person.objects.create(
            first_name="Amina",
            last_name="Zulu",
            primary_email="amina@example.com",
        )
        self.business_membership = Membership.objects.create(
            person=self.business_person,
            status=Membership.Status.ACTIVE,
            joined_at=date(2024, 4, 1),
            membership_source=Membership.Source.WEBSITE_FORM,
        )
        self.business_without_membership = Person.objects.create(
            first_name="Contact",
            last_name="Only",
            primary_email="contact@example.com",
        )
        self.archived_business_person = Person.objects.create(
            first_name="Archived",
            last_name="Member",
            primary_email="archived@example.com",
            archived_at=timezone.now(),
        )
        self.archived_business_membership = Membership.objects.create(
            person=self.archived_business_person,
            status=Membership.Status.FORMER,
            joined_at=date(2020, 1, 10),
            ended_at=date(2024, 5, 31),
            membership_source=Membership.Source.STAFF,
        )
        self.technical_person = Person.objects.create(
            first_name="Root",
            last_name="Operator",
            record_type=Person.RecordType.TECHNICAL,
        )
        Membership.objects.create(
            person=self.technical_person,
            status=Membership.Status.ACTIVE,
            joined_at=date(2024, 1, 1),
            membership_source=Membership.Source.OTHER,
        )
        self.technical_admin_user = User.objects.create_user(
            email="technical-admin@example.com",
            password="testpass123",
            person_first_name="Tech",
            person_last_name="Admin",
            person_record_type=Person.RecordType.TECHNICAL,
        )
        StaffRoleAssignment.objects.assign_role(user=self.technical_admin_user, role=admin_role)

    def authenticate(self, user):
        self.client.force_authenticate(user=user)

    def get_url(self, person_id):
        return self.url_template.format(person_id=person_id)

    def test_anonymous_user_receives_401(self):
        response = self.client.get(self.get_url(self.business_person.id))
        self.assertEqual(response.status_code, 401)

    def test_authenticated_non_staff_user_receives_403(self):
        self.authenticate(self.non_staff_user)
        response = self.client.get(self.get_url(self.business_person.id))
        self.assertEqual(response.status_code, 403)

    def test_crm_admin_receives_200(self):
        self.authenticate(self.admin_user)
        response = self.client.get(self.get_url(self.business_person.id))
        self.assertEqual(response.status_code, 200)

    def test_crm_manager_receives_200(self):
        self.authenticate(self.manager_user)
        response = self.client.get(self.get_url(self.business_person.id))
        self.assertEqual(response.status_code, 200)

    def test_crm_viewer_receives_200(self):
        self.authenticate(self.viewer_user)
        response = self.client.get(self.get_url(self.business_person.id))
        self.assertEqual(response.status_code, 200)

    def test_business_person_with_membership_returns_200(self):
        self.authenticate(self.admin_user)
        response = self.client.get(self.get_url(self.business_person.id))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["id"], self.business_membership.id)

    def test_business_person_without_membership_returns_404(self):
        self.authenticate(self.admin_user)
        response = self.client.get(self.get_url(self.business_without_membership.id))
        self.assertEqual(response.status_code, 404)

    def test_archived_business_person_with_membership_is_still_readable(self):
        self.authenticate(self.admin_user)
        response = self.client.get(self.get_url(self.archived_business_person.id))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["id"], self.archived_business_membership.id)

    def test_technical_person_returns_404_even_if_membership_exists(self):
        self.authenticate(self.admin_user)
        response = self.client.get(self.get_url(self.technical_person.id))
        self.assertEqual(response.status_code, 404)

    def test_technical_person_linked_to_crm_admin_returns_404(self):
        membership = Membership.objects.create(
            person=self.technical_admin_user.person,
            status=Membership.Status.ACTIVE,
            joined_at=date(2024, 2, 1),
            membership_source=Membership.Source.STAFF,
        )

        self.authenticate(self.technical_admin_user)
        response = self.client.get(self.get_url(self.technical_admin_user.person_id))

        self.assertEqual(response.status_code, 404)
        membership.refresh_from_db()

    def test_nonexistent_person_returns_404(self):
        self.authenticate(self.admin_user)
        response = self.client.get(self.get_url(999999))
        self.assertEqual(response.status_code, 404)

    def test_response_returns_expected_membership_fields(self):
        self.authenticate(self.admin_user)
        response = self.client.get(self.get_url(self.business_person.id))

        self.assertEqual(
            set(response.data.keys()),
            {
                "id",
                "status",
                "joined_at",
                "ended_at",
                "membership_source",
                "created_at",
                "updated_at",
            },
        )

    def test_response_does_not_return_auth_or_staff_internals(self):
        self.authenticate(self.admin_user)
        response = self.client.get(self.get_url(self.business_person.id))

        self.assertNotIn("person", response.data)
        self.assertNotIn("user", response.data)
        self.assertNotIn("is_staff", response.data)
        self.assertNotIn("is_superuser", response.data)
        self.assertNotIn("staff_roles", response.data)
