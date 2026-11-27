from datetime import date

from django.contrib.admin.sites import AdminSite
from django.core.exceptions import ValidationError
from django.test import RequestFactory, TestCase

from accounts.models import User
from memberships.models import Membership
from people.admin import PersonAdmin
from people.models import Person
from staff_access.models import StaffRole, StaffRoleAssignment
from config.test_utils import test_actor
from people.services import correct_person_classification


class IdentityBoundaryTests(TestCase):
    def setUp(self):
        for code, name in StaffRole.CANONICAL_ROLES:
            StaffRole.objects.update_or_create(code=code, defaults={"name": name, "is_active": True})

    def test_new_people_default_to_community(self):
        person = Person.objects.create(first_name="New", last_name="Member")
        self.assertEqual(person.record_type, Person.RecordType.COMMUNITY)

    def test_staff_and_technical_people_cannot_have_memberships(self):
        for record_type in (Person.RecordType.STAFF, Person.RecordType.TECHNICAL):
            person = Person.objects.create(
                first_name="Restricted",
                last_name=record_type.value,
                record_type=record_type,
            )
            with self.assertRaises(ValidationError):
                Membership.objects.create(
                    person=person,
                    status=Membership.Status.ACTIVE,
                    joined_at=date(2026, 1, 1),
                    membership_source=Membership.Source.STAFF,
                )

    def test_community_user_cannot_receive_staff_role(self):
        user = User.objects.create_user(
            email="community@example.com",
            password="testpass123",
            person_first_name="Community",
            person_last_name="User",
        )
        role = StaffRole.objects.get(code=StaffRole.CRM_VIEWER)
        with self.assertRaises(ValidationError):
            StaffRoleAssignment.objects.assign_role(user=user, role=role, assigned_by=test_actor())

    def test_person_admin_cannot_change_classification_directly(self):
        admin_user = User.objects.create_superuser(
            email="admin@example.com",
            password="testpass123",
            person_first_name="Admin",
            person_last_name="User",
        )
        person = Person.objects.create(first_name="Community", last_name="Person")
        person.record_type = Person.RecordType.TECHNICAL
        request = RequestFactory().get("/admin/people/person/")
        request.user = admin_user
        PersonAdmin(Person, AdminSite()).save_model(request, person, form=None, change=True)
        person.refresh_from_db()
        self.assertEqual(person.record_type, Person.RecordType.COMMUNITY)

    def test_crm_admin_classification_correction_revokes_linked_credentials(self):
        actor = User.objects.create_user(
            email="classification-admin@example.com",
            password="testpass123",
            person=Person.objects.create(
                first_name="Classification", last_name="Admin", record_type=Person.RecordType.STAFF,
            ),
        )
        StaffRoleAssignment.objects.assign_role(
            user=actor,
            role=StaffRole.objects.get(code=StaffRole.CRM_ADMIN),
            assigned_by=test_actor(),
        )
        target = User.objects.create_user(
            email="target@example.com",
            password="testpass123",
            person_first_name="Target",
            person_last_name="Person",
        )
        previous_auth_version = target.auth_version

        corrected = correct_person_classification(
            person_id=target.person_id,
            target_record_type=Person.RecordType.STAFF.value,
            actor_user=actor,
            reason="Verified separate Staff identity.",
        )

        self.assertEqual(corrected.record_type, Person.RecordType.STAFF.value)
        target.refresh_from_db()
        self.assertGreater(target.auth_version, previous_auth_version)
