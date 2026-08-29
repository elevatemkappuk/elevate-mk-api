from django.contrib.admin.sites import AdminSite
from django.test import override_settings
from django.test import RequestFactory
from django.utils import timezone

from django.test import TestCase

from accounts.models import User
from people.admin import PersonAdmin
from people.models import Person
from rest_framework.test import APIClient
from staff_access.models import StaffRole, StaffRoleAssignment


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
