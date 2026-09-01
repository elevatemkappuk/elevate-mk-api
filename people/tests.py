from django.contrib.admin.sites import AdminSite
from unittest import mock
from django.test import override_settings
from django.test import RequestFactory
from django.utils import timezone

from django.test import TestCase

from accounts.models import User
from audit.models import AuditEvent
from interests.models import Interest, PersonInterest
from memberships.models import Membership
from people.admin import PersonAdmin
from people.models import Person
from professional_profiles.models import Industry, ProfessionalProfile
from rest_framework.test import APIClient
from staff_access.models import StaffRole, StaffRoleAssignment
from skills.models import PersonSkill, Skill
from tags.models import PersonTag, Tag


class PersonModelTests(TestCase):
    def test_person_can_exist_without_user(self):
        person = Person.objects.create(first_name="Taylor", last_name="Jordan")

        self.assertEqual(person.first_name, "Taylor")
        self.assertFalse(hasattr(person, "user"))

    def test_person_defaults_to_business_record_type(self):
        person = Person.objects.create(first_name="Casey", last_name="Morgan")

        self.assertEqual(person.record_type, Person.RecordType.BUSINESS)

    def test_person_can_be_explicitly_classified_as_technical(self):
        person = Person.objects.create(
            first_name="Root",
            last_name="Operator",
            record_type=Person.RecordType.TECHNICAL,
        )

        self.assertEqual(person.record_type, Person.RecordType.TECHNICAL)

    def test_record_type_and_archived_at_are_independent(self):
        archived_at = timezone.now()
        person = Person.objects.create(
            first_name="Technical",
            last_name="Archive",
            record_type=Person.RecordType.TECHNICAL,
            archived_at=archived_at,
        )

        self.assertEqual(person.record_type, Person.RecordType.TECHNICAL)
        self.assertEqual(person.archived_at, archived_at)

    def test_existing_person_can_be_reclassified_safely(self):
        archived_at = timezone.now()
        person = Person.objects.create(
            first_name="Jamie",
            last_name="Operator",
            archived_at=archived_at,
        )

        person.record_type = Person.RecordType.TECHNICAL
        person.save(update_fields=["record_type", "updated_at"])
        person.refresh_from_db()

        self.assertEqual(person.record_type, Person.RecordType.TECHNICAL)
        self.assertEqual(person.archived_at, archived_at)

    def test_person_exposes_canonical_demographic_choices(self):
        self.assertEqual(
            Person.AgeRange.choices,
            [
                ("UNDER_25", "Under 25"),
                ("25_29", "25 - 29"),
                ("30_34", "30 - 34"),
                ("35_39", "35 - 39"),
                ("40_45", "40 - 45"),
                ("OVER_45", "Over 45"),
            ],
        )
        self.assertEqual(
            Person.Gender.choices,
            [
                ("MALE", "Male"),
                ("FEMALE", "Female"),
                ("NON_BINARY", "Non-Binary"),
                ("TRANSGENDER", "Transgender"),
                ("OTHER", "Other"),
            ],
        )


class PersonAdminTests(TestCase):
    def setUp(self):
        self.site = AdminSite()
        self.factory = RequestFactory()
        self.admin_user = User.objects.create_superuser(
            email="admin@example.com",
            password="testpass123",
            person_first_name="Admin",
            person_last_name="User",
        )
        self.person_admin = PersonAdmin(Person, self.site)

    def build_request(self):
        request = self.factory.get("/admin/people/person/")
        request.user = self.admin_user
        return request

    def test_person_admin_list_displays_and_filters_record_type(self):
        self.assertIn("record_type", self.person_admin.list_display)
        self.assertIn("record_type", self.person_admin.list_filter)

    def test_person_admin_form_exposes_record_type_as_choice_field(self):
        form_class = self.person_admin.get_form(self.build_request())
        record_type_field = form_class.base_fields["record_type"]

        self.assertEqual(record_type_field.choices, Person.RecordType.choices)

    def test_person_admin_can_reclassify_existing_person(self):
        person = Person.objects.create(first_name="Taylor", last_name="Jordan")
        request = self.build_request()

        person.record_type = Person.RecordType.TECHNICAL
        self.person_admin.save_model(request, person, form=None, change=True)
        person.refresh_from_db()

        self.assertEqual(person.record_type, Person.RecordType.TECHNICAL)

    def test_person_admin_reclassification_keeps_archived_at_independent(self):
        archived_at = timezone.now()
        person = Person.objects.create(
            first_name="Casey",
            last_name="Archive",
            archived_at=archived_at,
        )
        request = self.build_request()

        person.record_type = Person.RecordType.TECHNICAL
        self.person_admin.save_model(request, person, form=None, change=True)
        person.refresh_from_db()

        self.assertEqual(person.record_type, Person.RecordType.TECHNICAL)
        self.assertEqual(person.archived_at, archived_at)


@override_settings(ROOT_URLCONF="config.urls")
class PeopleApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.url = "/api/v1/people/"
        self.detail_url = "/api/v1/people/{person_id}/"

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

        self.admin_role = StaffRole.objects.get(code=StaffRole.CRM_ADMIN)
        self.manager_role = StaffRole.objects.get(code=StaffRole.CRM_MANAGER)
        self.viewer_role = StaffRole.objects.get(code=StaffRole.CRM_VIEWER)

        StaffRoleAssignment.objects.assign_role(user=self.admin_user, role=self.admin_role)
        StaffRoleAssignment.objects.assign_role(user=self.manager_user, role=self.manager_role)
        StaffRoleAssignment.objects.assign_role(user=self.viewer_user, role=self.viewer_role)

        self.active_business = Person.objects.create(
            first_name="Amina",
            last_name="Zulu",
            primary_email="amina@example.com",
            mobile="991000001",
            location="Lilongwe",
        )
        self.business_admin_person = self.admin_user.person
        self.business_admin_person.location = "Blantyre"
        self.business_admin_person.save(update_fields=["location", "updated_at"])

        self.archived_business = Person.objects.create(
            first_name="Brian",
            last_name="Archive",
            primary_email="brian@example.com",
            archived_at=timezone.now(),
        )
        self.technical_person = Person.objects.create(
            first_name="Root",
            last_name="Operator",
            primary_email="root@example.com",
            mobile="991999999",
            record_type=Person.RecordType.TECHNICAL,
        )
        self.archived_technical_person = Person.objects.create(
            first_name="Tech",
            last_name="Archive",
            primary_email="tech-archive@example.com",
            archived_at=timezone.now(),
            record_type=Person.RecordType.TECHNICAL,
        )
        self.technical_admin_user = User.objects.create_user(
            email="technical-admin@example.com",
            password="testpass123",
            person_first_name="Technical",
            person_last_name="Admin",
            person_record_type=Person.RecordType.TECHNICAL,
        )
        StaffRoleAssignment.objects.assign_role(
            user=self.technical_admin_user,
            role=self.admin_role,
        )

        for index in range(30):
            Person.objects.create(
                first_name=f"Person{index:02d}",
                last_name="Directory",
                primary_email=f"person{index:02d}@example.com",
            )

    def authenticate(self, user):
        self.client.force_authenticate(user=user)

    def get_ids(self, response):
        return [result["id"] for result in response.data["results"]]

    def get_detail_url(self, person_id):
        return self.detail_url.format(person_id=person_id)

    def test_anonymous_user_receives_401(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 401)

    def test_authenticated_non_staff_user_receives_403(self):
        self.authenticate(self.non_staff_user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 403)

    def test_crm_admin_receives_200(self):
        self.authenticate(self.admin_user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)

    def test_crm_manager_receives_200(self):
        self.authenticate(self.manager_user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)

    def test_crm_viewer_receives_200(self):
        self.authenticate(self.viewer_user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)

    def test_active_business_person_is_returned_by_default(self):
        self.authenticate(self.admin_user)
        response = self.client.get(self.url, {"page_size": 50})
        self.assertIn(self.active_business.id, self.get_ids(response))

    def test_archived_business_person_is_excluded_by_default(self):
        self.authenticate(self.admin_user)
        response = self.client.get(self.url)
        self.assertNotIn(self.archived_business.id, self.get_ids(response))

    def test_technical_person_is_excluded_by_default(self):
        self.authenticate(self.admin_user)
        response = self.client.get(self.url)
        self.assertNotIn(self.technical_person.id, self.get_ids(response))

    def test_technical_person_is_excluded_from_record_state_archived(self):
        self.authenticate(self.admin_user)
        response = self.client.get(self.url, {"record_state": "archived"})
        self.assertNotIn(self.archived_technical_person.id, self.get_ids(response))

    def test_technical_person_is_excluded_from_record_state_all(self):
        self.authenticate(self.admin_user)
        response = self.client.get(self.url, {"record_state": "all", "page_size": 50})
        self.assertNotIn(self.technical_person.id, self.get_ids(response))
        self.assertNotIn(self.archived_technical_person.id, self.get_ids(response))

    def test_technical_person_linked_to_active_crm_admin_user_is_still_excluded(self):
        self.authenticate(self.technical_admin_user)
        response = self.client.get(self.url, {"record_state": "all", "page_size": 50})
        self.assertNotIn(self.technical_admin_user.person_id, self.get_ids(response))

    def test_business_person_who_also_has_crm_admin_is_returned(self):
        self.authenticate(self.admin_user)
        response = self.client.get(self.url, {"record_state": "all", "page_size": 50})
        self.assertIn(self.business_admin_person.id, self.get_ids(response))

    def test_record_state_active_returns_only_active_business_people(self):
        self.authenticate(self.admin_user)
        response = self.client.get(self.url, {"record_state": "active", "page_size": 50})
        ids = self.get_ids(response)

        self.assertIn(self.active_business.id, ids)
        self.assertIn(self.business_admin_person.id, ids)
        self.assertNotIn(self.archived_business.id, ids)
        self.assertNotIn(self.technical_person.id, ids)

    def test_record_state_archived_returns_only_archived_business_people(self):
        self.authenticate(self.admin_user)
        response = self.client.get(self.url, {"record_state": "archived"})
        ids = self.get_ids(response)

        self.assertIn(self.archived_business.id, ids)
        self.assertNotIn(self.active_business.id, ids)
        self.assertNotIn(self.archived_technical_person.id, ids)

    def test_record_state_all_returns_all_business_people_regardless_of_archive(self):
        self.authenticate(self.admin_user)
        response = self.client.get(self.url, {"record_state": "all", "page_size": 50})
        ids = self.get_ids(response)

        self.assertIn(self.active_business.id, ids)
        self.assertIn(self.archived_business.id, ids)
        self.assertIn(self.business_admin_person.id, ids)
        self.assertNotIn(self.technical_person.id, ids)

    def test_invalid_record_state_returns_400(self):
        self.authenticate(self.admin_user)
        response = self.client.get(self.url, {"record_state": "invalid"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("record_state", response.data)

    def test_search_matches_first_name(self):
        self.authenticate(self.admin_user)
        response = self.client.get(self.url, {"q": "Amina", "page_size": 50})
        self.assertEqual(self.get_ids(response), [self.active_business.id])

    def test_search_matches_last_name(self):
        self.authenticate(self.admin_user)
        response = self.client.get(self.url, {"q": "Zulu", "page_size": 50})
        self.assertEqual(self.get_ids(response), [self.active_business.id])

    def test_search_matches_email_case_insensitively(self):
        self.authenticate(self.admin_user)
        response = self.client.get(self.url, {"q": "AMINA@EXAMPLE.COM", "page_size": 50})
        self.assertEqual(self.get_ids(response), [self.active_business.id])

    def test_search_matches_mobile(self):
        self.authenticate(self.admin_user)
        response = self.client.get(self.url, {"q": "991000001", "page_size": 50})
        self.assertEqual(self.get_ids(response), [self.active_business.id])

    def test_search_matches_full_name(self):
        self.authenticate(self.admin_user)
        response = self.client.get(self.url, {"q": "Amina Zulu", "page_size": 50})
        self.assertEqual(self.get_ids(response), [self.active_business.id])

    def test_search_with_no_matches_returns_empty_results(self):
        self.authenticate(self.admin_user)
        response = self.client.get(self.url, {"q": "No Match Here"})
        self.assertEqual(response.data["results"], [])

    def test_search_cannot_surface_technical_people(self):
        self.authenticate(self.admin_user)
        response = self.client.get(self.url, {"q": "Root", "record_state": "all", "page_size": 50})
        self.assertEqual(response.data["results"], [])

    def test_ordering_supports_first_name_ascending(self):
        self.authenticate(self.admin_user)
        Person.objects.create(first_name="Aaron", last_name="Same")
        Person.objects.create(first_name="Zoe", last_name="Same")

        response = self.client.get(self.url, {"ordering": "first_name", "page_size": 50})
        first_names = [result["first_name"] for result in response.data["results"][:3]]

        self.assertEqual(first_names, ["Aaron", "Admin", "Amina"])

    def test_ordering_supports_last_name_descending(self):
        self.authenticate(self.admin_user)
        response = self.client.get(self.url, {"ordering": "-last_name", "page_size": 50})
        ordered_names = [(result["last_name"], result["id"]) for result in response.data["results"][:3]]
        self.assertEqual(ordered_names[0][0], "Zulu")

    def test_ordering_supports_created_at_descending_with_deterministic_tie_breaker(self):
        self.authenticate(self.admin_user)
        first = Person.objects.create(first_name="Tie", last_name="Breaker")
        second = Person.objects.create(first_name="Tie", last_name="Breaker")
        tied_timestamp = timezone.now()
        Person.objects.filter(id__in=[first.id, second.id]).update(
            created_at=tied_timestamp,
            updated_at=tied_timestamp,
        )

        response = self.client.get(self.url, {"ordering": "-created_at", "page_size": 50})
        ids = self.get_ids(response)

        self.assertLess(ids.index(first.id), ids.index(second.id))

    def test_invalid_ordering_returns_400(self):
        self.authenticate(self.admin_user)
        response = self.client.get(self.url, {"ordering": "email"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("ordering", response.data)

    def test_default_page_size_is_25(self):
        self.authenticate(self.admin_user)
        response = self.client.get(self.url)
        self.assertEqual(len(response.data["results"]), 25)
        self.assertEqual(response.data["count"], 35)

    def test_page_size_50_is_supported(self):
        self.authenticate(self.admin_user)
        response = self.client.get(self.url, {"page_size": 50})
        self.assertEqual(len(response.data["results"]), 35)
        self.assertEqual(response.data["count"], 35)

    def test_page_size_100_is_supported(self):
        self.authenticate(self.admin_user)
        response = self.client.get(self.url, {"page_size": 100})
        self.assertEqual(len(response.data["results"]), 35)
        self.assertEqual(response.data["count"], 35)

    def test_unsupported_page_size_returns_400(self):
        self.authenticate(self.admin_user)
        response = self.client.get(self.url, {"page_size": 200})
        self.assertEqual(response.status_code, 400)
        self.assertIn("page_size", response.data)

    def test_pagination_never_leaks_technical_records(self):
        self.authenticate(self.admin_user)
        response = self.client.get(self.url, {"page_size": 25, "record_state": "all"})
        result_ids = self.get_ids(response)
        self.assertNotIn(self.technical_person.id, result_ids)
        self.assertNotIn(self.archived_technical_person.id, result_ids)

    def test_detail_anonymous_user_receives_401(self):
        response = self.client.get(self.get_detail_url(self.active_business.id))
        self.assertEqual(response.status_code, 401)

    def test_detail_authenticated_non_staff_user_receives_403(self):
        self.authenticate(self.non_staff_user)
        response = self.client.get(self.get_detail_url(self.active_business.id))
        self.assertEqual(response.status_code, 403)

    def test_detail_crm_admin_receives_200(self):
        self.authenticate(self.admin_user)
        response = self.client.get(self.get_detail_url(self.active_business.id))
        self.assertEqual(response.status_code, 200)

    def test_detail_crm_manager_receives_200(self):
        self.authenticate(self.manager_user)
        response = self.client.get(self.get_detail_url(self.active_business.id))
        self.assertEqual(response.status_code, 200)

    def test_detail_crm_viewer_receives_200(self):
        self.authenticate(self.viewer_user)
        response = self.client.get(self.get_detail_url(self.active_business.id))
        self.assertEqual(response.status_code, 200)

    def test_detail_active_business_person_is_returned(self):
        self.authenticate(self.admin_user)
        response = self.client.get(self.get_detail_url(self.active_business.id))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["id"], self.active_business.id)

    def test_detail_archived_business_person_is_returned(self):
        self.authenticate(self.admin_user)
        response = self.client.get(self.get_detail_url(self.archived_business.id))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["id"], self.archived_business.id)
        self.assertIsNotNone(response.data["archived_at"])

    def test_detail_technical_person_returns_404(self):
        self.authenticate(self.admin_user)
        response = self.client.get(self.get_detail_url(self.technical_person.id))
        self.assertEqual(response.status_code, 404)

    def test_detail_technical_person_linked_to_crm_admin_returns_404(self):
        self.authenticate(self.technical_admin_user)
        response = self.client.get(self.get_detail_url(self.technical_admin_user.person_id))
        self.assertEqual(response.status_code, 404)

    def test_detail_business_person_linked_to_crm_admin_is_returned(self):
        self.authenticate(self.admin_user)
        response = self.client.get(self.get_detail_url(self.business_admin_person.id))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["id"], self.business_admin_person.id)

    def test_detail_nonexistent_id_returns_404(self):
        self.authenticate(self.admin_user)
        response = self.client.get(self.get_detail_url(999999))
        self.assertEqual(response.status_code, 404)

    def test_detail_returns_expected_person_fields(self):
        self.authenticate(self.admin_user)
        response = self.client.get(self.get_detail_url(self.active_business.id))

        self.assertEqual(
            set(response.data.keys()),
            {
                "id",
                "first_name",
                "last_name",
                "primary_email",
                "mobile",
                "location",
                "age_range",
                "gender",
                "archived_at",
                "created_at",
                "updated_at",
            },
        )

    def test_detail_does_not_return_record_type(self):
        self.authenticate(self.admin_user)
        response = self.client.get(self.get_detail_url(self.active_business.id))
        self.assertNotIn("record_type", response.data)

    def test_detail_does_not_return_auth_or_staff_internals(self):
        self.authenticate(self.admin_user)
        response = self.client.get(self.get_detail_url(self.active_business.id))

        self.assertNotIn("user", response.data)
        self.assertNotIn("is_staff", response.data)
        self.assertNotIn("is_superuser", response.data)
        self.assertNotIn("staff_role_assignments", response.data)


@override_settings(ROOT_URLCONF="config.urls")
class PersonOverviewApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.url_template = "/api/v1/people/{person_id}/overview/"

        self.non_staff_user = User.objects.create_user(
            email="nonstaff-overview@example.com",
            password="testpass123",
            person_first_name="Non",
            person_last_name="Staff",
        )
        self.admin_user = User.objects.create_user(
            email="admin-overview@example.com",
            password="testpass123",
            person_first_name="Admin",
            person_last_name="User",
        )
        self.manager_user = User.objects.create_user(
            email="manager-overview@example.com",
            password="testpass123",
            person_first_name="Manager",
            person_last_name="User",
        )
        self.viewer_user = User.objects.create_user(
            email="viewer-overview@example.com",
            password="testpass123",
            person_first_name="Viewer",
            person_last_name="User",
        )

        self.admin_role = StaffRole.objects.get(code=StaffRole.CRM_ADMIN)
        self.manager_role = StaffRole.objects.get(code=StaffRole.CRM_MANAGER)
        self.viewer_role = StaffRole.objects.get(code=StaffRole.CRM_VIEWER)

        StaffRoleAssignment.objects.assign_role(user=self.admin_user, role=self.admin_role)
        StaffRoleAssignment.objects.assign_role(user=self.manager_user, role=self.manager_role)
        StaffRoleAssignment.objects.assign_role(user=self.viewer_user, role=self.viewer_role)

        self.contact_person = Person.objects.create(
            first_name="Contact",
            last_name="Person",
            primary_email="contact@example.com",
            mobile="991000100",
            location="Lilongwe",
        )
        self.active_member_person = Person.objects.create(
            first_name="Active",
            last_name="Member",
            primary_email="active@example.com",
            mobile="991000101",
            location="Blantyre",
        )
        self.active_membership = Membership.objects.create(
            person=self.active_member_person,
            status=Membership.Status.ACTIVE,
            joined_at=timezone.datetime(2024, 4, 12).date(),
            membership_source=Membership.Source.WEBSITE_FORM,
        )
        self.former_member_person = Person.objects.create(
            first_name="Former",
            last_name="Member",
            primary_email="former@example.com",
            mobile="991000102",
            location="Mzuzu",
        )
        self.former_membership = Membership.objects.create(
            person=self.former_member_person,
            status=Membership.Status.FORMER,
            joined_at=timezone.datetime(2021, 5, 2).date(),
            ended_at=timezone.datetime(2024, 7, 15).date(),
            membership_source=Membership.Source.STAFF,
        )
        self.archived_business_person = Person.objects.create(
            first_name="Archived",
            last_name="Business",
            archived_at=timezone.now(),
        )
        self.archived_membership = Membership.objects.create(
            person=self.archived_business_person,
            status=Membership.Status.ACTIVE,
            joined_at=timezone.datetime(2022, 6, 1).date(),
            membership_source=Membership.Source.COMMUNITY_PLATFORM,
        )
        self.technical_person = Person.objects.create(
            first_name="Root",
            last_name="Operator",
            record_type=Person.RecordType.TECHNICAL,
        )
        self.industry = Industry.objects.get(slug="technology")
        self.active_profile = ProfessionalProfile.objects.create(
            person=self.active_member_person,
            job_title="Software Engineer",
            company="Example Ltd",
            industry=self.industry,
            career_stage=ProfessionalProfile.CareerStage.SENIOR,
            linkedin_url="https://www.linkedin.com/in/active-member",
        )
        self.archived_profile = ProfessionalProfile.objects.create(
            person=self.archived_business_person,
            job_title="Advisor",
            company="Archive Ltd",
        )
        self.accounting_skill = Skill.objects.get(slug="accounting")
        self.software_development_skill = Skill.objects.get(slug="software-development")
        self.inactive_skill = Skill.objects.create(
            name="Legacy Skill",
            slug="legacy-overview-skill",
            display_order=5,
            is_active=False,
        )
        self.networking_interest = Interest.objects.get(slug="networking")
        self.technology_interest = Interest.objects.get(slug="technology")
        self.startups_interest = Interest.objects.get(slug="startups")
        self.inactive_interest = Interest.objects.create(
            name="Legacy Interest",
            slug="legacy-overview-interest",
            display_order=5,
            is_active=False,
        )
        self.potential_mentor_tag = Tag.objects.get(slug="potential-mentor")
        self.vip_tag = Tag.objects.get(slug="vip")
        self.inactive_tag = Tag.objects.create(
            name="Legacy Tag",
            slug="legacy-overview-tag",
            display_order=5,
            is_active=False,
        )

    def authenticate(self, user):
        self.client.force_authenticate(user=user)

    def get_url(self, person_id):
        return self.url_template.format(person_id=person_id)

    def test_anonymous_user_receives_401(self):
        response = self.client.get(self.get_url(self.contact_person.id))
        self.assertEqual(response.status_code, 401)

    def test_authenticated_non_staff_user_receives_403(self):
        self.authenticate(self.non_staff_user)
        response = self.client.get(self.get_url(self.contact_person.id))
        self.assertEqual(response.status_code, 403)

    def test_crm_admin_receives_200(self):
        self.authenticate(self.admin_user)
        response = self.client.get(self.get_url(self.contact_person.id))
        self.assertEqual(response.status_code, 200)

    def test_crm_manager_receives_200(self):
        self.authenticate(self.manager_user)
        response = self.client.get(self.get_url(self.contact_person.id))
        self.assertEqual(response.status_code, 200)

    def test_crm_viewer_receives_200(self):
        self.authenticate(self.viewer_user)
        response = self.client.get(self.get_url(self.contact_person.id))
        self.assertEqual(response.status_code, 200)

    def test_active_business_person_returns_200(self):
        self.authenticate(self.admin_user)
        response = self.client.get(self.get_url(self.contact_person.id))
        self.assertEqual(response.status_code, 200)

    def test_archived_business_person_returns_200(self):
        self.authenticate(self.admin_user)
        response = self.client.get(self.get_url(self.archived_business_person.id))
        self.assertEqual(response.status_code, 200)

    def test_technical_person_returns_404(self):
        self.authenticate(self.admin_user)
        response = self.client.get(self.get_url(self.technical_person.id))
        self.assertEqual(response.status_code, 404)

    def test_nonexistent_person_returns_404(self):
        self.authenticate(self.admin_user)
        response = self.client.get(self.get_url(999999))
        self.assertEqual(response.status_code, 404)

    def test_business_person_without_membership_returns_contact_projection(self):
        self.authenticate(self.admin_user)
        response = self.client.get(self.get_url(self.contact_person.id))

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.data["membership"])
        self.assertIsNone(response.data["professional_profile"])
        self.assertEqual(response.data["skills"], [])
        self.assertEqual(response.data["interests"], [])
        self.assertEqual(response.data["tags"], [])
        self.assertEqual(response.data["relationship"]["type"], "CONTACT")
        self.assertEqual(response.data["relationship"]["label"], "Contact")

    def test_skills_are_included_in_deterministic_order_when_present(self):
        PersonSkill.objects.create(person=self.active_member_person, skill=self.software_development_skill)
        PersonSkill.objects.create(person=self.active_member_person, skill=self.accounting_skill)

        self.authenticate(self.admin_user)
        response = self.client.get(self.get_url(self.active_member_person.id))

        self.assertEqual(
            response.data["skills"],
            [
                {
                    "id": self.accounting_skill.id,
                    "name": "Accounting",
                    "slug": "accounting",
                },
                {
                    "id": self.software_development_skill.id,
                    "name": "Software Development",
                    "slug": "software-development",
                },
            ],
        )

    def test_inactive_assigned_skills_are_omitted_from_overview(self):
        PersonSkill.objects.create(person=self.active_member_person, skill=self.accounting_skill)
        PersonSkill.objects.create(person=self.active_member_person, skill=self.inactive_skill)

        self.authenticate(self.admin_user)
        response = self.client.get(self.get_url(self.active_member_person.id))

        self.assertEqual(
            response.data["skills"],
            [
                {
                    "id": self.accounting_skill.id,
                    "name": "Accounting",
                    "slug": "accounting",
                }
            ],
        )

    def test_interests_are_included_in_deterministic_order_when_present(self):
        PersonInterest.objects.create(person=self.active_member_person, interest=self.startups_interest)
        PersonInterest.objects.create(person=self.active_member_person, interest=self.technology_interest)

        self.authenticate(self.admin_user)
        response = self.client.get(self.get_url(self.active_member_person.id))

        self.assertEqual(
            response.data["interests"],
            [
                {
                    "id": self.technology_interest.id,
                    "name": "Technology",
                    "slug": "technology",
                },
                {
                    "id": self.startups_interest.id,
                    "name": "Startups",
                    "slug": "startups",
                },
            ],
        )

    def test_inactive_assigned_interests_are_omitted_from_overview(self):
        PersonInterest.objects.create(person=self.active_member_person, interest=self.networking_interest)
        PersonInterest.objects.create(person=self.active_member_person, interest=self.inactive_interest)

        self.authenticate(self.admin_user)
        response = self.client.get(self.get_url(self.active_member_person.id))

        self.assertEqual(
            response.data["interests"],
            [
                {
                    "id": self.networking_interest.id,
                    "name": "Networking",
                    "slug": "networking",
                }
            ],
        )

    def test_tags_are_included_in_deterministic_order_when_present(self):
        PersonTag.objects.create(person=self.active_member_person, tag=self.vip_tag, assigned_by=self.admin_user)
        PersonTag.objects.create(person=self.active_member_person, tag=self.potential_mentor_tag, assigned_by=self.admin_user)

        self.authenticate(self.admin_user)
        response = self.client.get(self.get_url(self.active_member_person.id))

        self.assertEqual(
            response.data["tags"],
            [
                {
                    "id": self.potential_mentor_tag.id,
                    "name": "Potential Mentor",
                    "slug": "potential-mentor",
                },
                {
                    "id": self.vip_tag.id,
                    "name": "VIP",
                    "slug": "vip",
                },
            ],
        )

    def test_inactive_assigned_tags_are_omitted_from_overview(self):
        PersonTag.objects.create(person=self.active_member_person, tag=self.vip_tag, assigned_by=self.admin_user)
        PersonTag.objects.create(
            person=self.active_member_person,
            tag=self.potential_mentor_tag,
            assigned_by=self.admin_user,
            is_active=False,
            removed_by=self.manager_user,
            removed_at=timezone.now(),
        )
        PersonTag.objects.create(person=self.active_member_person, tag=self.inactive_tag, assigned_by=self.admin_user)

        self.authenticate(self.admin_user)
        response = self.client.get(self.get_url(self.active_member_person.id))

        self.assertEqual(
            response.data["tags"],
            [
                {
                    "id": self.vip_tag.id,
                    "name": "VIP",
                    "slug": "vip",
                }
            ],
        )

    def test_active_membership_returns_active_member_projection(self):
        self.authenticate(self.admin_user)
        response = self.client.get(self.get_url(self.active_member_person.id))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["relationship"]["type"], "ACTIVE_MEMBER")
        self.assertEqual(response.data["relationship"]["label"], "Active Member")
        self.assertEqual(
            response.data["membership"],
            {
                "id": self.active_membership.id,
                "status": "ACTIVE",
                "joined_at": "2024-04-12",
                "ended_at": None,
                "membership_source": "WEBSITE_FORM",
                "created_at": response.data["membership"]["created_at"],
                "updated_at": response.data["membership"]["updated_at"],
            },
        )

    def test_professional_profile_is_included_when_present(self):
        self.authenticate(self.admin_user)
        response = self.client.get(self.get_url(self.active_member_person.id))

        self.assertEqual(
            response.data["professional_profile"],
            {
                "id": self.active_profile.id,
                "job_title": "Software Engineer",
                "company": "Example Ltd",
                "industry": {
                    "id": self.industry.id,
                    "name": "Technology",
                    "slug": "technology",
                },
                "career_stage": ProfessionalProfile.CareerStage.SENIOR,
                "linkedin_url": "https://www.linkedin.com/in/active-member",
                "created_at": response.data["professional_profile"]["created_at"],
                "updated_at": response.data["professional_profile"]["updated_at"],
            },
        )

    def test_professional_profile_api_contract_uses_stable_career_stage_code(self):
        self.authenticate(self.admin_user)
        response = self.client.get(self.get_url(self.active_member_person.id))

        self.assertEqual(
            response.data["professional_profile"]["career_stage"],
            ProfessionalProfile.CareerStage.SENIOR,
        )

    def test_archived_business_person_still_returns_professional_profile(self):
        PersonSkill.objects.create(person=self.archived_business_person, skill=self.accounting_skill)
        PersonInterest.objects.create(person=self.archived_business_person, interest=self.networking_interest)
        PersonTag.objects.create(person=self.archived_business_person, tag=self.vip_tag, assigned_by=self.admin_user)

        self.authenticate(self.admin_user)
        response = self.client.get(self.get_url(self.archived_business_person.id))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["professional_profile"]["id"], self.archived_profile.id)
        self.assertIsNone(response.data["professional_profile"]["industry"])
        self.assertEqual(
            response.data["skills"],
            [
                {
                    "id": self.accounting_skill.id,
                    "name": "Accounting",
                    "slug": "accounting",
                }
            ]
        )
        self.assertEqual(
            response.data["interests"],
            [
                {
                    "id": self.networking_interest.id,
                    "name": "Networking",
                    "slug": "networking",
                }
            ],
        )
        self.assertEqual(
            response.data["tags"],
            [
                {
                    "id": self.vip_tag.id,
                    "name": "VIP",
                    "slug": "vip",
                }
            ],
        )

    def test_technical_person_still_returns_404_even_with_professional_profile(self):
        technical_industry = Industry.objects.create(name="Operations", slug="operations")
        ProfessionalProfile.objects.create(
            person=self.technical_person,
            job_title="System Administrator",
            industry=technical_industry,
        )

        self.authenticate(self.admin_user)
        response = self.client.get(self.get_url(self.technical_person.id))

        self.assertEqual(response.status_code, 404)

    def test_membership_and_relationship_contracts_remain_unchanged_with_professional_profile(self):
        self.authenticate(self.admin_user)
        response = self.client.get(self.get_url(self.active_member_person.id))

        self.assertEqual(response.data["relationship"]["type"], "ACTIVE_MEMBER")
        self.assertEqual(response.data["membership"]["status"], "ACTIVE")
        self.assertEqual(response.data["membership"]["joined_at"], "2024-04-12")

    def test_former_membership_returns_former_member_projection(self):
        self.authenticate(self.admin_user)
        response = self.client.get(self.get_url(self.former_member_person.id))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["relationship"]["type"], "FORMER_MEMBER")
        self.assertEqual(response.data["relationship"]["label"], "Former Member")
        self.assertEqual(response.data["membership"]["status"], "FORMER")
        self.assertEqual(response.data["membership"]["ended_at"], "2024-07-15")

    def test_response_person_fields_match_existing_person_read_contract(self):
        self.authenticate(self.admin_user)
        response = self.client.get(self.get_url(self.contact_person.id))

        self.assertEqual(
            set(response.data["person"].keys()),
            {
                "id",
                "first_name",
                "last_name",
                "primary_email",
                "mobile",
                "location",
                "age_range",
                "gender",
                "archived_at",
                "created_at",
                "updated_at",
            },
        )

    def test_response_does_not_expose_auth_staff_or_speculative_fields(self):
        PersonSkill.objects.create(person=self.active_member_person, skill=self.accounting_skill)
        PersonInterest.objects.create(person=self.active_member_person, interest=self.networking_interest)
        PersonTag.objects.create(person=self.active_member_person, tag=self.vip_tag, assigned_by=self.admin_user)

        self.authenticate(self.admin_user)
        response = self.client.get(self.get_url(self.active_member_person.id))

        self.assertNotIn("record_type", response.data["person"])
        self.assertNotIn("user", response.data["person"])
        self.assertNotIn("is_staff", response.data["person"])
        self.assertNotIn("is_superuser", response.data["person"])
        self.assertNotIn("staff_roles", response.data)
        self.assertEqual(list(response.data["skills"][0].keys()), ["id", "name", "slug"])
        self.assertEqual(list(response.data["interests"][0].keys()), ["id", "name", "slug"])
        self.assertEqual(list(response.data["tags"][0].keys()), ["id", "name", "slug"])
        self.assertNotIn("person", response.data["membership"])
        self.assertNotIn("notes", response.data)
        self.assertNotIn("engagement", response.data)

    def test_endpoint_uses_compact_query_shape(self):
        PersonSkill.objects.create(person=self.active_member_person, skill=self.accounting_skill)
        PersonInterest.objects.create(person=self.active_member_person, interest=self.networking_interest)
        PersonTag.objects.create(person=self.active_member_person, tag=self.vip_tag, assigned_by=self.admin_user)

        self.authenticate(self.admin_user)
        with self.assertNumQueries(5):
            response = self.client.get(self.get_url(self.active_member_person.id))

        self.assertEqual(response.status_code, 200)


@override_settings(ROOT_URLCONF="config.urls")
class PersonWriteLifecycleApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.create_url = "/api/v1/people/"
        self.member_create_url = "/api/v1/people/members/"
        self.admin_user = User.objects.create_user(
            email="person-write-admin@example.com", password="testpass123",
            person_first_name="Admin", person_last_name="Writer",
        )
        self.manager_user = User.objects.create_user(
            email="person-write-manager@example.com", password="testpass123",
            person_first_name="Manager", person_last_name="Writer",
        )
        self.viewer_user = User.objects.create_user(
            email="person-write-viewer@example.com", password="testpass123",
            person_first_name="Viewer", person_last_name="Writer",
        )
        self.nonstaff_user = User.objects.create_user(
            email="person-write-nonstaff@example.com", password="testpass123",
            person_first_name="Nonstaff", person_last_name="Writer",
        )
        for user, code in (
            (self.admin_user, StaffRole.CRM_ADMIN),
            (self.manager_user, StaffRole.CRM_MANAGER),
            (self.viewer_user, StaffRole.CRM_VIEWER),
        ):
            StaffRoleAssignment.objects.assign_role(user=user, role=StaffRole.objects.get(code=code))

    def authenticate(self, user):
        self.client.force_authenticate(user=user)

    def person_payload(self, **overrides):
        payload = {"first_name": "Amina", "last_name": "Zulu", "primary_email": "amina@example.com"}
        payload.update(overrides)
        return payload

    def detail_url(self, person_id):
        return f"/api/v1/people/{person_id}/"

    def archive_url(self, person_id):
        return f"/api/v1/people/{person_id}/archive/"

    def restore_url(self, person_id):
        return f"/api/v1/people/{person_id}/restore/"

    def test_contact_create_is_authorized_and_creates_business_person_with_audit(self):
        self.authenticate(self.admin_user)
        response = self.client.post(self.create_url, self.person_payload(), format="json")

        self.assertEqual(response.status_code, 201)
        person = Person.objects.get(pk=response.data["id"])
        self.assertEqual(person.record_type, Person.RecordType.BUSINESS)
        self.assertIsNone(person.archived_at)
        self.assertFalse(Membership.objects.filter(person=person).exists())
        event = AuditEvent.objects.get(action=AuditEvent.Action.PERSON_CREATED, entity_id=str(person.id))
        self.assertEqual(event.actor_user, self.admin_user)
        self.assertEqual(event.metadata, {"person_id": str(person.id)})

    def test_manager_can_create_contact_and_viewer_nonstaff_and_anonymous_are_denied(self):
        self.authenticate(self.manager_user)
        self.assertEqual(self.client.post(self.create_url, self.person_payload(), format="json").status_code, 201)
        self.client.force_authenticate(user=self.viewer_user)
        self.assertEqual(self.client.post(self.create_url, self.person_payload(primary_email="viewer@example.com"), format="json").status_code, 403)
        self.client.force_authenticate(user=self.nonstaff_user)
        self.assertEqual(self.client.post(self.create_url, self.person_payload(primary_email="nonstaff@example.com"), format="json").status_code, 403)
        self.client.force_authenticate(user=None)
        self.assertEqual(self.client.post(self.create_url, self.person_payload(primary_email="anonymous@example.com"), format="json").status_code, 401)

    def test_create_rejects_server_managed_fields_and_duplicate_business_identity(self):
        Person.objects.create(first_name="Existing", last_name="Person", primary_email="Existing@Example.com", mobile="99 100-0001", archived_at=timezone.now())
        Person.objects.create(first_name="Technical", last_name="Only", primary_email="technical@example.com", record_type=Person.RecordType.TECHNICAL)
        self.authenticate(self.admin_user)
        rejected = self.client.post(self.create_url, self.person_payload(record_type="TECHNICAL"), format="json")
        email_duplicate = self.client.post(self.create_url, self.person_payload(primary_email=" existing@example.COM "), format="json")
        mobile_duplicate = self.client.post(self.create_url, self.person_payload(primary_email="new@example.com", mobile="991000001"), format="json")
        technical_allowed = self.client.post(self.create_url, self.person_payload(primary_email="technical@example.com"), format="json")

        self.assertEqual(rejected.status_code, 400)
        self.assertEqual(email_duplicate.status_code, 409)
        self.assertEqual(email_duplicate.data["code"], "duplicate_person")
        self.assertEqual(mobile_duplicate.status_code, 409)
        self.assertEqual(technical_allowed.status_code, 201)

    def test_create_accepts_each_canonical_age_range_and_rejects_unsupported_value(self):
        self.authenticate(self.admin_user)
        for index, age_range in enumerate(Person.AgeRange.values):
            with self.subTest(age_range=age_range):
                response = self.client.post(
                    self.create_url,
                    self.person_payload(primary_email=f"age-{index}@example.com", age_range=age_range),
                    format="json",
                )
                self.assertEqual(response.status_code, 201)
                self.assertEqual(response.data["age_range"], age_range)

        response = self.client.post(
            self.create_url,
            self.person_payload(primary_email="unsupported-age@example.com", age_range="18_24"),
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("age_range", response.data)

    def test_create_accepts_each_canonical_gender_and_rejects_unsupported_value(self):
        self.authenticate(self.admin_user)
        for index, gender in enumerate(Person.Gender.values):
            with self.subTest(gender=gender):
                response = self.client.post(
                    self.create_url,
                    self.person_payload(primary_email=f"gender-{index}@example.com", gender=gender),
                    format="json",
                )
                self.assertEqual(response.status_code, 201)
                self.assertEqual(response.data["gender"], gender)

        response = self.client.post(
            self.create_url,
            self.person_payload(primary_email="unsupported-gender@example.com", gender="PREFER_NOT_TO_SAY"),
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("gender", response.data)

    def test_create_member_is_atomic_and_emits_both_events(self):
        self.authenticate(self.manager_user)
        payload = self.person_payload(joined_at="2026-08-31", membership_source="STAFF")
        response = self.client.post(self.member_create_url, payload, format="json")

        self.assertEqual(response.status_code, 201)
        person = Person.objects.get(pk=response.data["id"])
        self.assertEqual(person.membership.status, Membership.Status.ACTIVE)
        self.assertEqual(person.membership.membership_source, Membership.Source.STAFF)
        self.assertTrue(AuditEvent.objects.filter(action=AuditEvent.Action.PERSON_CREATED, entity_id=str(person.id)).exists())
        self.assertTrue(AuditEvent.objects.filter(action=AuditEvent.Action.MEMBERSHIP_CREATED, metadata__person_id=str(person.id)).exists())

    def test_create_member_rolls_back_when_required_audit_write_fails(self):
        self.authenticate(self.admin_user)
        with mock.patch("people.views.record_audit_event", side_effect=RuntimeError("audit down")):
            response = self.client.post(self.member_create_url, self.person_payload(joined_at="2026-08-31", membership_source="STAFF"), format="json")

        self.assertEqual(response.status_code, 500)
        self.assertFalse(Person.objects.filter(primary_email="amina@example.com").exists())
        self.assertFalse(Membership.objects.exists())

    def test_patch_updates_only_changed_fields_and_noop_is_not_audited(self):
        person = Person.objects.create(first_name="Amina", last_name="Zulu", primary_email="amina@example.com")
        self.authenticate(self.admin_user)
        changed = self.client.patch(self.detail_url(person.id), {"location": "Lilongwe"}, format="json")
        noop = self.client.patch(self.detail_url(person.id), {"location": "Lilongwe"}, format="json")

        self.assertEqual(changed.status_code, 200)
        update = AuditEvent.objects.get(action=AuditEvent.Action.PERSON_UPDATED)
        self.assertEqual(update.changes, {"location": {"from": "", "to": "Lilongwe"}})
        self.assertEqual(noop.status_code, 200)
        self.assertEqual(AuditEvent.objects.filter(action=AuditEvent.Action.PERSON_UPDATED).count(), 1)

    def test_patch_rejects_archived_duplicate_and_technical_people(self):
        active = Person.objects.create(first_name="Active", last_name="Person", primary_email="active@example.com")
        Person.objects.create(first_name="Archived", last_name="Candidate", primary_email="duplicate@example.com", archived_at=timezone.now())
        technical = Person.objects.create(first_name="Technical", last_name="Person", record_type=Person.RecordType.TECHNICAL)
        active.archived_at = timezone.now()
        active.save(update_fields=["archived_at", "updated_at"])
        self.authenticate(self.admin_user)
        archived = self.client.patch(self.detail_url(active.id), {"location": "Lilongwe"}, format="json")
        technical_response = self.client.patch(self.detail_url(technical.id), {"location": "Lilongwe"}, format="json")

        self.assertEqual(archived.status_code, 409)
        self.assertEqual(technical_response.status_code, 404)

    def test_archive_and_restore_preserve_membership_and_emit_lifecycle_events(self):
        person = Person.objects.create(first_name="Amina", last_name="Zulu")
        membership = Membership.objects.create(person=person, status=Membership.Status.ACTIVE, joined_at="2025-01-01", membership_source=Membership.Source.STAFF)
        self.authenticate(self.admin_user)
        archive = self.client.post(self.archive_url(person.id), {}, format="json")
        restore = self.client.post(self.restore_url(person.id), {}, format="json")

        self.assertEqual(archive.status_code, 200)
        self.assertEqual(restore.status_code, 200)
        membership.refresh_from_db()
        self.assertEqual(membership.status, Membership.Status.ACTIVE)
        self.assertTrue(AuditEvent.objects.filter(action=AuditEvent.Action.PERSON_ARCHIVED, entity_id=str(person.id)).exists())
        self.assertTrue(AuditEvent.objects.filter(action=AuditEvent.Action.PERSON_RESTORED, entity_id=str(person.id)).exists())


@override_settings(ROOT_URLCONF="config.urls")
class PeopleDirectoryQueryApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.url = "/api/v1/people/"
        self.admin_user = User.objects.create_user(email="directory-admin@example.com", password="testpass123", person_first_name="Directory", person_last_name="Admin")
        self.viewer_user = User.objects.create_user(email="directory-viewer@example.com", password="testpass123", person_first_name="Directory", person_last_name="Viewer")
        self.nonstaff_user = User.objects.create_user(email="directory-nonstaff@example.com", password="testpass123", person_first_name="Directory", person_last_name="Nonstaff")
        StaffRoleAssignment.objects.assign_role(user=self.admin_user, role=StaffRole.objects.get(code=StaffRole.CRM_ADMIN))
        StaffRoleAssignment.objects.assign_role(user=self.viewer_user, role=StaffRole.objects.get(code=StaffRole.CRM_VIEWER))
        self.contact = Person.objects.create(first_name="Ada", last_name="Lovelace", primary_email="ada@example.com", mobile="991", location=" Milton Keynes ")
        self.active_member = Person.objects.create(first_name="Grace", last_name="Hopper", primary_email="grace@example.com", location="London")
        self.former_member = Person.objects.create(first_name="Alan", last_name="Turing", archived_at=timezone.now())
        Membership.objects.create(person=self.active_member, status=Membership.Status.ACTIVE, joined_at="2024-01-01", membership_source=Membership.Source.STAFF)
        Membership.objects.create(person=self.former_member, status=Membership.Status.FORMER, joined_at="2023-01-01", ended_at="2024-01-01", membership_source=Membership.Source.STAFF)
        self.industry = Industry.objects.create(name="Technology", slug="technology")
        ProfessionalProfile.objects.create(person=self.active_member, job_title="Software Engineer", company="Microsoft", industry=self.industry, career_stage=ProfessionalProfile.CareerStage.SENIOR)
        self.interest_a = Interest.objects.create(name="Mentoring", slug="mentoring")
        self.interest_b = Interest.objects.create(name="Networking", slug="networking")
        self.skill = Skill.objects.create(name="Python", slug="python")
        self.active_tag = Tag.objects.create(name="VIP", slug="vip")
        self.removed_tag = Tag.objects.create(name="Removed", slug="removed")
        PersonInterest.objects.create(person=self.active_member, interest=self.interest_a)
        PersonInterest.objects.create(person=self.contact, interest=self.interest_b)
        PersonSkill.objects.create(person=self.active_member, skill=self.skill)
        PersonTag.objects.create(person=self.active_member, tag=self.active_tag, assigned_by=self.admin_user)
        PersonTag.objects.create(person=self.active_member, tag=self.removed_tag, assigned_by=self.admin_user, is_active=False, removed_by=self.admin_user, removed_at=timezone.now())
        Person.objects.create(first_name="Technical", last_name="Record", record_type=Person.RecordType.TECHNICAL, location="London")

    def authenticate(self, user):
        self.client.force_authenticate(user=user)

    def result_ids(self, response):
        return [item["id"] for item in response.data["results"]]

    def test_authorization_and_business_boundary_remain_intact(self):
        self.assertEqual(self.client.get(self.url).status_code, 401)
        self.authenticate(self.nonstaff_user)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.authenticate(self.viewer_user)
        response = self.client.get(self.url, {"record_state": "all"})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(Person.objects.get(first_name="Technical").id, self.result_ids(response))

    def test_search_includes_identity_full_name_and_professional_profile(self):
        self.authenticate(self.admin_user)
        self.assertIn(self.contact.id, self.result_ids(self.client.get(self.url, {"q": "Ada Lovelace"})))
        self.assertIn(self.active_member.id, self.result_ids(self.client.get(self.url, {"q": "engineer"})))
        self.assertIn(self.active_member.id, self.result_ids(self.client.get(self.url, {"q": "MICROSOFT"})))
        self.assertEqual(self.client.get(self.url, {"q": "   "}).status_code, 200)

    def test_relationship_location_and_professional_filters_use_repeated_or_values(self):
        self.authenticate(self.admin_user)
        relationships = self.client.get(self.url, [("relationship", "CONTACT"), ("relationship", "ACTIVE_MEMBER")])
        locations = self.client.get(self.url, [("location", "milton keynes"), ("location", "London")])
        professional = self.client.get(self.url, [("industry", str(self.industry.id)), ("career_stage", "SENIOR")])
        self.assertEqual(set(self.result_ids(relationships)), {self.contact.id, self.active_member.id})
        self.assertEqual(set(self.result_ids(locations)), {self.contact.id, self.active_member.id})
        self.assertEqual(self.result_ids(professional), [self.active_member.id])
        self.assertEqual(self.client.get(self.url, {"relationship": "UNKNOWN"}).status_code, 400)
        self.assertEqual(self.client.get(self.url, {"industry": "not-an-id"}).status_code, 400)

    def test_classification_filters_are_or_within_and_and_across_without_duplicates(self):
        self.authenticate(self.admin_user)
        response = self.client.get(self.url, [
            ("interest", str(self.interest_a.id)), ("interest", str(self.interest_b.id)),
            ("skill", str(self.skill.id)), ("tag", str(self.active_tag.id)),
        ])
        removed_tag = self.client.get(self.url, {"tag": self.removed_tag.id})
        self.assertEqual(self.result_ids(response), [self.active_member.id])
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(self.result_ids(removed_tag), [])

    def test_archived_state_and_membership_joined_ordering_are_deterministic(self):
        self.authenticate(self.admin_user)
        archived_former = self.client.get(self.url, {"record_state": "archived", "relationship": "FORMER_MEMBER"})
        ordered = self.client.get(self.url, {"record_state": "all", "ordering": "-membership_joined_at"})
        self.assertEqual(self.result_ids(archived_former), [self.former_member.id])
        self.assertEqual(self.result_ids(ordered)[:2], [self.active_member.id, self.former_member.id])
