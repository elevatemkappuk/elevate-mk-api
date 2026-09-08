from datetime import date

from django.utils import timezone
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory, APITestCase

from accounts.models import User
from memberships.models import Membership
from people.models import Person
from people.serializers import PersonDirectoryListSerializer
from people.views import PeopleListView
from professional_profiles.models import ProfessionalProfile
from staff_access.models import StaffRole, StaffRoleAssignment


class PeopleDirectoryProjectionTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="directory-admin@example.com", password="testpass123",
            person_first_name="Directory", person_last_name="Admin",
            person_record_type=Person.RecordType.TECHNICAL,
        )
        StaffRoleAssignment.objects.assign_role(
            user=self.user, role=StaffRole.objects.get(code=StaffRole.CRM_ADMIN),
        )
        self.client.force_authenticate(self.user)
        self.contact = Person.objects.create(first_name="Contact", last_name="Directory")
        self.member = Person.objects.create(first_name="Member", last_name="Directory")
        self.former = Person.objects.create(first_name="Former", last_name="Directory")
        self.profile = ProfessionalProfile.objects.create(person=self.member, job_title="Programme Manager")
        ProfessionalProfile.objects.create(person=self.former, job_title="")
        Membership.objects.create(
            person=self.member, status=Membership.Status.ACTIVE,
            joined_at=date(2024, 1, 1), membership_source=Membership.Source.STAFF,
        )
        Membership.objects.create(
            person=self.former, status=Membership.Status.FORMER,
            joined_at=date(2024, 1, 1), ended_at=date(2025, 1, 1),
            membership_source=Membership.Source.STAFF,
        )

    def rows(self, **params):
        response = self.client.get("/api/v1/people/", params)
        self.assertEqual(response.status_code, 200)
        return {row["id"]: row for row in response.data["results"]}

    def test_list_projects_current_profile_and_all_membership_types(self):
        rows = self.rows()
        self.assertEqual(rows[self.member.id]["job_title"], "Programme Manager")
        self.assertIsNone(rows[self.contact.id]["job_title"])
        self.assertIsNone(rows[self.former.id]["job_title"])
        self.assertEqual(rows[self.contact.id]["relationship"], "CONTACT")
        self.assertEqual(rows[self.member.id]["relationship"], "ACTIVE_MEMBER")
        self.assertEqual(rows[self.former.id]["relationship"], "FORMER_MEMBER")
        self.assertNotIn(self.user.person_id, rows)

        ProfessionalProfile.objects.filter(pk=self.profile.pk).update(job_title="Director")
        Membership.objects.filter(person=self.member).update(
            status=Membership.Status.FORMER, ended_at=date(2026, 1, 1),
        )
        current = self.rows()[self.member.id]
        self.assertEqual(current["job_title"], "Director")
        self.assertEqual(current["relationship"], "FORMER_MEMBER")

    def test_archive_state_does_not_change_membership_type(self):
        Person.objects.filter(pk__in=[self.member.pk, self.contact.pk]).update(archived_at=timezone.now())
        rows = self.rows(record_state="archived")
        self.assertEqual(rows[self.member.id]["relationship"], "ACTIVE_MEMBER")
        self.assertEqual(rows[self.member.id]["job_title"], "Programme Manager")
        self.assertEqual(rows[self.contact.id]["relationship"], "CONTACT")

    def test_projection_is_available_to_each_existing_read_role(self):
        for code in (StaffRole.CRM_ADMIN, StaffRole.CRM_MANAGER, StaffRole.CRM_VIEWER):
            with self.subTest(role=code):
                reader = User.objects.create_user(
                    email=f"{code.lower()}@example.com", password="testpass123",
                    person_first_name="Reader", person_last_name=code,
                    person_record_type=Person.RecordType.TECHNICAL,
                )
                StaffRoleAssignment.objects.assign_role(user=reader, role=StaffRole.objects.get(code=code))
                self.client.force_authenticate(reader)
                row = self.rows()[self.member.id]
                self.assertEqual(row["relationship"], "ACTIVE_MEMBER")
                self.assertEqual(row["job_title"], "Programme Manager")

    def test_projections_do_not_become_person_write_fields_or_detail_fields(self):
        response = self.client.post("/api/v1/people/", {
            "first_name": "New", "last_name": "Contact", "job_title": "Invented",
            "relationship": "ACTIVE_MEMBER",
        }, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("job_title", response.data)
        self.assertIn("relationship", response.data)
        detail = self.client.get(f"/api/v1/people/{self.member.pk}/")
        self.assertEqual(detail.status_code, 200)
        self.assertNotIn("job_title", detail.data)
        self.assertNotIn("relationship", detail.data)

    def test_directory_serialization_does_not_add_per_person_queries(self):
        for index in range(10):
            person = Person.objects.create(first_name=f"Extra{index}", last_name="Directory")
            ProfessionalProfile.objects.create(person=person, job_title=f"Role {index}")
            Membership.objects.create(
                person=person, status=Membership.Status.ACTIVE,
                joined_at=date(2024, 1, 1), membership_source=Membership.Source.STAFF,
            )
        view = PeopleListView()
        view.request = Request(APIRequestFactory().get("/api/v1/people/"))
        with self.assertNumQueries(1):
            rows = PersonDirectoryListSerializer(view.get_queryset(), many=True).data
        self.assertEqual(len(rows), 13)
