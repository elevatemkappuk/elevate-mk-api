from django.contrib.admin.sites import AdminSite
from django.test import RequestFactory
from django.utils import timezone

from django.test import TestCase

from accounts.models import User
from people.admin import PersonAdmin
from people.models import Person


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
