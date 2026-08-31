from django.contrib.admin.sites import AdminSite
from django.test import override_settings
from django.test import RequestFactory
from django.utils import timezone

from django.test import TestCase

from accounts.models import User
from memberships.models import Membership
from people.admin import PersonAdmin
from people.models import Person
from professional_profiles.models import Industry, ProfessionalProfile
from rest_framework.test import APIClient
from staff_access.models import StaffRole, StaffRoleAssignment
from skills.models import PersonSkill, Skill


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

        self.authenticate(self.admin_user)
        response = self.client.get(self.get_url(self.active_member_person.id))

        self.assertNotIn("record_type", response.data["person"])
        self.assertNotIn("user", response.data["person"])
        self.assertNotIn("is_staff", response.data["person"])
        self.assertNotIn("is_superuser", response.data["person"])
        self.assertNotIn("staff_roles", response.data)
        self.assertEqual(list(response.data["skills"][0].keys()), ["id", "name", "slug"])
        self.assertNotIn("person", response.data["membership"])
        self.assertNotIn("interests", response.data)
        self.assertNotIn("tags", response.data)
        self.assertNotIn("notes", response.data)
        self.assertNotIn("engagement", response.data)

    def test_endpoint_uses_compact_query_shape(self):
        PersonSkill.objects.create(person=self.active_member_person, skill=self.accounting_skill)

        self.authenticate(self.admin_user)
        with self.assertNumQueries(3):
            response = self.client.get(self.get_url(self.active_member_person.id))

        self.assertEqual(response.status_code, 200)
