from django.contrib.admin.sites import AdminSite
from django.db import IntegrityError
from django.db.models import ProtectedError
from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from people.models import Person
from professional_profiles.admin import IndustryAdmin, ProfessionalProfileAdmin
from professional_profiles.models import Industry, ProfessionalProfile
from staff_access.models import StaffRole, StaffRoleAssignment


class IndustryModelTests(TestCase):
    def test_slug_must_be_unique(self):
        Industry.objects.create(name="Technology", slug="technology")

        with self.assertRaises(IntegrityError):
            Industry.objects.create(name="Different Technology", slug="technology")

    def test_is_active_defaults_to_true(self):
        industry = Industry.objects.create(name="Technology", slug="technology")

        self.assertTrue(industry.is_active)

    def test_default_ordering_is_display_order_name_id(self):
        third = Industry.objects.create(name="Zoology", slug="zoology", display_order=2)
        second = Industry.objects.create(name="Finance", slug="finance", display_order=1)
        first = Industry.objects.create(name="Agriculture", slug="agriculture", display_order=1)

        self.assertEqual(list(Industry.objects.values_list("id", flat=True)), [first.id, second.id, third.id])


class ProfessionalProfileModelTests(TestCase):
    def test_person_may_exist_without_professional_profile(self):
        person = Person.objects.create(first_name="Amina", last_name="Zulu")

        self.assertFalse(hasattr(person, "professional_profile"))

    def test_professional_profile_requires_person(self):
        with self.assertRaises(IntegrityError):
            ProfessionalProfile.objects.create(job_title="Engineer")

    def test_only_one_professional_profile_per_person(self):
        person = Person.objects.create(first_name="Amina", last_name="Zulu")
        ProfessionalProfile.objects.create(person=person)

        with self.assertRaises(IntegrityError):
            ProfessionalProfile.objects.create(person=person)

    def test_deleting_person_with_professional_profile_is_protected(self):
        person = Person.objects.create(first_name="Amina", last_name="Zulu")
        ProfessionalProfile.objects.create(person=person)

        with self.assertRaises(ProtectedError):
            person.delete()

    def test_industry_is_optional(self):
        person = Person.objects.create(first_name="Amina", last_name="Zulu")
        profile = ProfessionalProfile.objects.create(person=person)

        self.assertIsNone(profile.industry)

    def test_deleting_referenced_industry_is_protected(self):
        person = Person.objects.create(first_name="Amina", last_name="Zulu")
        industry = Industry.objects.create(name="Technology", slug="technology")
        ProfessionalProfile.objects.create(person=person, industry=industry)

        with self.assertRaises(ProtectedError):
            industry.delete()

    def test_optional_professional_fields_are_accepted(self):
        person = Person.objects.create(first_name="Amina", last_name="Zulu")
        industry = Industry.objects.create(name="Technology", slug="technology")
        profile = ProfessionalProfile.objects.create(
            person=person,
            job_title="Software Engineer",
            company="Example Ltd",
            industry=industry,
            career_stage="Senior individual contributor",
            linkedin_url="https://www.linkedin.com/in/example",
        )

        self.assertEqual(profile.job_title, "Software Engineer")
        self.assertEqual(profile.company, "Example Ltd")
        self.assertEqual(profile.industry, industry)
        self.assertEqual(profile.career_stage, "Senior individual contributor")
        self.assertEqual(profile.linkedin_url, "https://www.linkedin.com/in/example")

    def test_timestamps_are_recorded_and_updated_normally(self):
        person = Person.objects.create(first_name="Amina", last_name="Zulu")
        profile = ProfessionalProfile.objects.create(person=person, company="Example Ltd")
        original_updated_at = profile.updated_at

        profile.company = "Example Group"
        profile.save()
        profile.refresh_from_db()

        self.assertIsNotNone(profile.created_at)
        self.assertGreaterEqual(profile.updated_at, original_updated_at)


class ProfessionalProfileAdminTests(TestCase):
    def setUp(self):
        self.site = AdminSite()
        self.factory = RequestFactory()
        self.admin_user = User.objects.create_superuser(
            email="admin@example.com",
            password="testpass123",
            person_first_name="Admin",
            person_last_name="User",
        )
        self.industry_admin = IndustryAdmin(Industry, self.site)
        self.profile_admin = ProfessionalProfileAdmin(ProfessionalProfile, self.site)

    def build_request(self):
        request = self.factory.get("/admin/professional_profiles/")
        request.user = self.admin_user
        return request

    def test_industry_admin_supports_taxonomy_management_fields(self):
        self.assertEqual(self.industry_admin.list_display, ("name", "slug", "is_active", "display_order"))
        self.assertIn("is_active", self.industry_admin.list_filter)
        self.assertEqual(self.industry_admin.search_fields, ("name", "slug"))
        self.assertIn("created_at", self.industry_admin.readonly_fields)
        self.assertIn("updated_at", self.industry_admin.readonly_fields)

    def test_professional_profile_admin_supports_person_and_industry_lookup(self):
        self.assertIn("person", self.profile_admin.autocomplete_fields)
        self.assertIn("industry", self.profile_admin.autocomplete_fields)
        self.assertIn("industry", self.profile_admin.list_filter)
        self.assertIn("person__first_name", self.profile_admin.search_fields)
        self.assertIn("person__primary_email", self.profile_admin.search_fields)
        self.assertIn("created_at", self.profile_admin.readonly_fields)
        self.assertIn("updated_at", self.profile_admin.readonly_fields)


@override_settings(ROOT_URLCONF="config.urls")
class IndustryApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.url = "/api/v1/industries/"

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

        self.first_active = Industry.objects.create(name="Agriculture", slug="agriculture", display_order=1)
        self.second_active = Industry.objects.create(name="Finance", slug="finance", display_order=1)
        self.third_active = Industry.objects.create(name="Technology", slug="technology", display_order=2)
        self.inactive = Industry.objects.create(name="Legacy", slug="legacy", display_order=0, is_active=False)

    def authenticate(self, user):
        self.client.force_authenticate(user=user)

    def test_anonymous_receives_401(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 401)

    def test_authenticated_nonstaff_receives_403(self):
        self.authenticate(self.non_staff_user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 403)

    def test_crm_viewer_receives_200(self):
        self.authenticate(self.viewer_user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)

    def test_crm_manager_receives_200(self):
        self.authenticate(self.manager_user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)

    def test_crm_admin_receives_200(self):
        self.authenticate(self.admin_user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)

    def test_inactive_industries_are_excluded(self):
        self.authenticate(self.admin_user)
        response = self.client.get(self.url)

        returned_ids = [item["id"] for item in response.data]
        self.assertNotIn(self.inactive.id, returned_ids)

    def test_active_industries_are_returned_in_deterministic_order(self):
        self.authenticate(self.admin_user)
        response = self.client.get(self.url)

        self.assertEqual(
            [item["id"] for item in response.data],
            [self.first_active.id, self.second_active.id, self.third_active.id],
        )

    def test_representation_contains_only_intended_fields(self):
        self.authenticate(self.admin_user)
        response = self.client.get(self.url)

        self.assertEqual(set(response.data[0].keys()), {"id", "name", "slug"})


@override_settings(ROOT_URLCONF="config.urls")
class ProfessionalProfileApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.url_template = "/api/v1/people/{person_id}/professional-profile/"

        self.non_staff_user = User.objects.create_user(
            email="nonstaff-profile@example.com",
            password="testpass123",
            person_first_name="Non",
            person_last_name="Staff",
        )
        self.admin_user = User.objects.create_user(
            email="admin-profile@example.com",
            password="testpass123",
            person_first_name="Admin",
            person_last_name="User",
        )
        self.manager_user = User.objects.create_user(
            email="manager-profile@example.com",
            password="testpass123",
            person_first_name="Manager",
            person_last_name="User",
        )
        self.viewer_user = User.objects.create_user(
            email="viewer-profile@example.com",
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

        self.industry = Industry.objects.create(name="Technology", slug="technology")
        self.business_person = Person.objects.create(first_name="Amina", last_name="Zulu")
        self.business_profile = ProfessionalProfile.objects.create(
            person=self.business_person,
            job_title="Software Engineer",
            company="Example Ltd",
            industry=self.industry,
            career_stage="Senior individual contributor",
            linkedin_url="https://www.linkedin.com/in/example",
        )
        self.archived_business_person = Person.objects.create(
            first_name="Archived",
            last_name="Profile",
            archived_at=timezone.now(),
        )
        self.archived_business_profile = ProfessionalProfile.objects.create(
            person=self.archived_business_person,
            job_title="Advisor",
            company="Archive Ltd",
        )
        self.business_person_without_profile = Person.objects.create(first_name="No", last_name="Profile")
        self.technical_person = Person.objects.create(
            first_name="Root",
            last_name="Operator",
            record_type=Person.RecordType.TECHNICAL,
        )
        self.technical_profile = ProfessionalProfile.objects.create(
            person=self.technical_person,
            job_title="System Administrator",
        )
        self.business_profile_without_industry = ProfessionalProfile.objects.create(
            person=Person.objects.create(first_name="Null", last_name="Industry"),
            job_title="Freelancer",
        )

    def authenticate(self, user):
        self.client.force_authenticate(user=user)

    def get_url(self, person_id):
        return self.url_template.format(person_id=person_id)

    def test_anonymous_receives_401(self):
        response = self.client.get(self.get_url(self.business_person.id))
        self.assertEqual(response.status_code, 401)

    def test_authenticated_nonstaff_receives_403(self):
        self.authenticate(self.non_staff_user)
        response = self.client.get(self.get_url(self.business_person.id))
        self.assertEqual(response.status_code, 403)

    def test_crm_viewer_receives_200(self):
        self.authenticate(self.viewer_user)
        response = self.client.get(self.get_url(self.business_person.id))
        self.assertEqual(response.status_code, 200)

    def test_crm_manager_receives_200(self):
        self.authenticate(self.manager_user)
        response = self.client.get(self.get_url(self.business_person.id))
        self.assertEqual(response.status_code, 200)

    def test_crm_admin_receives_200(self):
        self.authenticate(self.admin_user)
        response = self.client.get(self.get_url(self.business_person.id))
        self.assertEqual(response.status_code, 200)

    def test_business_person_with_profile_returns_200(self):
        self.authenticate(self.admin_user)
        response = self.client.get(self.get_url(self.business_person.id))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["id"], self.business_profile.id)

    def test_archived_business_person_with_profile_returns_200(self):
        self.authenticate(self.admin_user)
        response = self.client.get(self.get_url(self.archived_business_person.id))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["id"], self.archived_business_profile.id)

    def test_business_person_without_profile_returns_404(self):
        self.authenticate(self.admin_user)
        response = self.client.get(self.get_url(self.business_person_without_profile.id))
        self.assertEqual(response.status_code, 404)

    def test_technical_person_returns_404(self):
        self.authenticate(self.admin_user)
        response = self.client.get(self.get_url(self.technical_person.id))
        self.assertEqual(response.status_code, 404)

    def test_nonexistent_person_returns_404(self):
        self.authenticate(self.admin_user)
        response = self.client.get(self.get_url(999999))
        self.assertEqual(response.status_code, 404)

    def test_nested_industry_representation_is_correct(self):
        self.authenticate(self.admin_user)
        response = self.client.get(self.get_url(self.business_person.id))

        self.assertEqual(
            response.data["industry"],
            {
                "id": self.industry.id,
                "name": "Technology",
                "slug": "technology",
            },
        )

    def test_null_industry_representation_is_returned_when_absent(self):
        self.authenticate(self.admin_user)
        response = self.client.get(self.get_url(self.business_profile_without_industry.person_id))

        self.assertIsNone(response.data["industry"])

    def test_response_does_not_repeat_person(self):
        self.authenticate(self.admin_user)
        response = self.client.get(self.get_url(self.business_person.id))

        self.assertNotIn("person", response.data)

    def test_career_stage_remains_plain_optional_text(self):
        self.authenticate(self.admin_user)
        response = self.client.get(self.get_url(self.business_person.id))

        self.assertEqual(response.data["career_stage"], "Senior individual contributor")
