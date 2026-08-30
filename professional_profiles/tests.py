from django.contrib.admin.sites import AdminSite
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.db.models import ProtectedError
from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from people.models import Person
from professional_profiles.admin import IndustryAdmin, ProfessionalProfileAdmin
from professional_profiles.models import Industry, ProfessionalProfile
from professional_profiles.taxonomy import CANONICAL_INDUSTRIES
from staff_access.models import StaffRole, StaffRoleAssignment


class IndustryModelTests(TestCase):
    def test_all_29_canonical_industries_exist_after_migrations(self):
        seeded = list(Industry.objects.order_by("display_order", "name", "id").values("name", "slug", "display_order", "is_active"))
        expected = [{**industry, "is_active": True} for industry in CANONICAL_INDUSTRIES]

        self.assertEqual(len(seeded), 29)
        self.assertEqual(seeded, expected)

    def test_slug_must_be_unique(self):
        Industry.objects.create(name="Custom Technology", slug="custom-technology")

        with self.assertRaises(IntegrityError):
            Industry.objects.create(name="Duplicate Custom Technology", slug="custom-technology")

    def test_seeded_industries_are_active(self):
        self.assertFalse(Industry.objects.filter(is_active=False).exists())

    def test_default_ordering_is_display_order_name_id(self):
        third = Industry.objects.create(name="Zoology", slug="zoology-extra", display_order=305)
        second = Industry.objects.create(name="Finance Annex", slug="finance-annex", display_order=300)
        first = Industry.objects.create(name="Agriculture Annex", slug="agriculture-annex", display_order=300)

        self.assertEqual(list(Industry.objects.order_by("display_order", "name", "id").values_list("id", flat=True))[-3:], [first.id, second.id, third.id])

    def test_other_is_last_in_seeded_order(self):
        last_industry = Industry.objects.order_by("display_order", "name", "id").last()
        self.assertEqual(last_industry.slug, "other")


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
        industry = Industry.objects.get(slug="technology")
        ProfessionalProfile.objects.create(person=person, industry=industry)

        with self.assertRaises(ProtectedError):
            industry.delete()

    def test_optional_professional_fields_are_accepted(self):
        person = Person.objects.create(first_name="Amina", last_name="Zulu")
        industry = Industry.objects.get(slug="technology")
        profile = ProfessionalProfile.objects.create(
            person=person,
            job_title="Software Engineer",
            company="Example Ltd",
            industry=industry,
            career_stage=ProfessionalProfile.CareerStage.SENIOR,
            linkedin_url="https://www.linkedin.com/in/example",
        )

        self.assertEqual(profile.job_title, "Software Engineer")
        self.assertEqual(profile.company, "Example Ltd")
        self.assertEqual(profile.industry, industry)
        self.assertEqual(profile.career_stage, ProfessionalProfile.CareerStage.SENIOR)
        self.assertEqual(profile.linkedin_url, "https://www.linkedin.com/in/example")

    def test_null_career_stage_remains_valid(self):
        person = Person.objects.create(first_name="Amina", last_name="Zulu")
        profile = ProfessionalProfile(person=person, career_stage=None)

        profile.full_clean()

    def test_every_approved_career_stage_code_is_valid(self):
        for index, career_stage in enumerate(ProfessionalProfile.CareerStage.values, start=1):
            with self.subTest(career_stage=career_stage):
                person = Person.objects.create(first_name=f"Amina{index}", last_name="Zulu")
                profile = ProfessionalProfile(person=person, career_stage=career_stage)
                profile.full_clean()

    def test_arbitrary_career_stage_value_fails_validation(self):
        person = Person.objects.create(first_name="Amina", last_name="Zulu")
        profile = ProfessionalProfile(person=person, career_stage="Experienced")

        with self.assertRaisesMessage(ValidationError, "Value 'Experienced' is not a valid choice."):
            profile.full_clean()

    def test_founder_business_owner_code_is_stored_exactly(self):
        person = Person.objects.create(first_name="Amina", last_name="Zulu")
        profile = ProfessionalProfile.objects.create(
            person=person,
            career_stage=ProfessionalProfile.CareerStage.FOUNDER_BUSINESS_OWNER,
        )

        self.assertEqual(profile.career_stage, "FOUNDER_BUSINESS_OWNER")

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

    def test_professional_profile_admin_uses_career_stage_choices(self):
        form_class = self.profile_admin.get_form(self.build_request())

        self.assertEqual(
            list(form_class.base_fields["career_stage"].choices),
            [("", "- Select an option -"), *list(ProfessionalProfile.CareerStage.choices)],
        )


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

        self.first_active = Industry.objects.get(slug="accounting")
        self.second_active = Industry.objects.get(slug="advertising-marketing")
        self.third_active = Industry.objects.get(slug="architecture-design")
        self.inactive = Industry.objects.create(name="Legacy", slug="legacy", display_order=5, is_active=False)

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
            len(response.data),
            29,
        )
        self.assertEqual(response.data[0], {"id": self.first_active.id, "name": "Accounting", "slug": "accounting"})
        self.assertEqual(response.data[1], {"id": self.second_active.id, "name": "Advertising & Marketing", "slug": "advertising-marketing"})
        self.assertEqual(response.data[2], {"id": self.third_active.id, "name": "Architecture & Design", "slug": "architecture-design"})
        self.assertEqual(response.data[-1]["slug"], "other")

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

        self.industry = Industry.objects.get(slug="technology")
        self.business_person = Person.objects.create(first_name="Amina", last_name="Zulu")
        self.business_profile = ProfessionalProfile.objects.create(
            person=self.business_person,
            job_title="Software Engineer",
            company="Example Ltd",
            industry=self.industry,
            career_stage=ProfessionalProfile.CareerStage.SENIOR,
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

        self.assertEqual(response.data["career_stage"], ProfessionalProfile.CareerStage.SENIOR)
