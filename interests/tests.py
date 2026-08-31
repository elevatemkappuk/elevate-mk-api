from django.contrib.admin.sites import AdminSite
from django.db import IntegrityError
from django.db.models import ProtectedError
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import User
from interests.admin import InterestAdmin, PersonInterestAdmin
from interests.models import Interest, PersonInterest
from memberships.models import Membership
from people.models import Person
from professional_profiles.models import Industry, ProfessionalProfile
from rest_framework.test import APIClient
from staff_access.models import StaffRole, StaffRoleAssignment
from skills.models import PersonSkill, Skill


class InterestModelTests(TestCase):
    def test_interest_defaults_apply(self):
        interest = Interest.objects.create(name="Advisory Boards", slug="advisory-boards")

        self.assertEqual(interest.description, "")
        self.assertTrue(interest.is_active)
        self.assertEqual(interest.display_order, 100)

    def test_slug_is_unique(self):
        Interest.objects.create(name="Networking", slug="networking-duplicate-check")

        with self.assertRaises(IntegrityError):
            Interest.objects.create(name="Networking Again", slug="networking-duplicate-check")

    def test_ordering_is_display_order_name_then_id(self):
        first = Interest.objects.create(name="Alpha", slug="interest-alpha", display_order=300)
        second = Interest.objects.create(name="Beta", slug="interest-beta", display_order=300)
        third = Interest.objects.create(name="Gamma", slug="interest-gamma", display_order=310)

        ordered_ids = list(Interest.objects.order_by("display_order", "name", "id").values_list("id", flat=True))
        self.assertEqual(ordered_ids[-3:], [first.id, second.id, third.id])

    def test_inactive_interest_is_persisted(self):
        interest = Interest.objects.create(name="Legacy", slug="legacy-interest", is_active=False)

        self.assertFalse(interest.is_active)


class PersonInterestModelTests(TestCase):
    def test_person_may_have_zero_interests(self):
        person = Person.objects.create(first_name="No", last_name="Interests")

        self.assertEqual(person.person_interests.count(), 0)

    def test_multiple_different_interests_are_allowed(self):
        person = Person.objects.create(first_name="Multi", last_name="Interested")
        first = Interest.objects.create(name="Innovation", slug="innovation-extra")
        second = Interest.objects.create(name="Partnerships", slug="partnerships-extra")

        PersonInterest.objects.create(person=person, interest=first)
        PersonInterest.objects.create(person=person, interest=second)

        self.assertEqual(person.person_interests.count(), 2)

    def test_duplicate_person_interest_is_rejected(self):
        person = Person.objects.create(first_name="Duplicate", last_name="Interest")
        interest = Interest.objects.create(name="Networking", slug="networking-extra")
        PersonInterest.objects.create(person=person, interest=interest)

        with self.assertRaises(IntegrityError):
            PersonInterest.objects.create(person=person, interest=interest)

    def test_person_delete_is_protected(self):
        person = Person.objects.create(first_name="Protected", last_name="Person")
        interest = Interest.objects.create(name="Technology", slug="technology-extra")
        PersonInterest.objects.create(person=person, interest=interest)

        with self.assertRaises(ProtectedError):
            person.delete()

    def test_interest_delete_is_protected(self):
        person = Person.objects.create(first_name="Protected", last_name="Interest")
        interest = Interest.objects.create(name="Leadership", slug="leadership-extra")
        PersonInterest.objects.create(person=person, interest=interest)

        with self.assertRaises(ProtectedError):
            interest.delete()


class InterestAdminTests(TestCase):
    def setUp(self):
        self.site = AdminSite()
        self.interest_admin = InterestAdmin(Interest, self.site)
        self.person_interest_admin = PersonInterestAdmin(PersonInterest, self.site)

    def test_interest_admin_configuration(self):
        self.assertEqual(self.interest_admin.list_display, ("name", "slug", "is_active", "display_order"))
        self.assertEqual(self.interest_admin.list_filter, ("is_active",))
        self.assertEqual(self.interest_admin.readonly_fields, ("created_at", "updated_at"))

    def test_person_interest_admin_configuration(self):
        self.assertEqual(self.person_interest_admin.list_display, ("person", "interest", "created_at"))
        self.assertEqual(self.person_interest_admin.readonly_fields, ("created_at",))


@override_settings(ROOT_URLCONF="config.urls")
class InterestApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.interests_url = "/api/v1/interests/"
        self.person_interests_url_template = "/api/v1/people/{person_id}/interests/"
        self.person_overview_url_template = "/api/v1/people/{person_id}/overview/"

        self.non_staff_user = User.objects.create_user(
            email="nonstaff-interests@example.com",
            password="testpass123",
            person_first_name="Non",
            person_last_name="Staff",
        )
        self.admin_user = User.objects.create_user(
            email="admin-interests@example.com",
            password="testpass123",
            person_first_name="Admin",
            person_last_name="User",
        )
        self.manager_user = User.objects.create_user(
            email="manager-interests@example.com",
            password="testpass123",
            person_first_name="Manager",
            person_last_name="User",
        )
        self.viewer_user = User.objects.create_user(
            email="viewer-interests@example.com",
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

        self.active_business_person = Person.objects.create(
            first_name="Amina",
            last_name="Worker",
            primary_email="amina@example.com",
        )
        self.business_admin_person = self.admin_user.person
        self.archived_business_person = Person.objects.create(
            first_name="Archived",
            last_name="Worker",
            archived_at=timezone.now(),
        )
        self.technical_person = Person.objects.create(
            first_name="Root",
            last_name="Operator",
            record_type=Person.RecordType.TECHNICAL,
        )
        self.technical_admin_user = User.objects.create_user(
            email="technical-interests-admin@example.com",
            password="testpass123",
            person_first_name="Technical",
            person_last_name="Admin",
            person_record_type=Person.RecordType.TECHNICAL,
        )
        StaffRoleAssignment.objects.assign_role(user=self.technical_admin_user, role=self.admin_role)

        self.networking = Interest.objects.get(slug="networking")
        self.technology = Interest.objects.get(slug="technology")
        self.startups = Interest.objects.get(slug="startups")
        self.inactive_interest = Interest.objects.create(
            name="Inactive Interest",
            slug="inactive-interest",
            is_active=False,
            display_order=999,
        )
        self.accounting_skill = Skill.objects.get(slug="accounting")
        self.industry = Industry.objects.get(slug="technology")
        self.membership = Membership.objects.create(
            person=self.active_business_person,
            status=Membership.Status.ACTIVE,
            joined_at=timezone.datetime(2024, 4, 12).date(),
            membership_source=Membership.Source.STAFF,
        )
        self.professional_profile = ProfessionalProfile.objects.create(
            person=self.active_business_person,
            job_title="Operator",
            company="Elevate MK",
            industry=self.industry,
        )

    def authenticate(self, user):
        self.client.force_authenticate(user=user)

    def get_person_interests_url(self, person_id):
        return self.person_interests_url_template.format(person_id=person_id)

    def get_person_overview_url(self, person_id):
        return self.person_overview_url_template.format(person_id=person_id)

    def test_interest_seed_contains_19_canonical_rows(self):
        seeded = list(
            Interest.objects.filter(
                slug__in=[
                    "networking",
                    "mentoring",
                    "career-development",
                    "entrepreneurship",
                    "technology",
                    "leadership",
                    "business-collaboration",
                    "speaking-opportunities",
                    "volunteering",
                    "professional-development",
                    "community-building",
                    "investment-funding",
                    "startups",
                    "innovation",
                    "social-impact",
                    "events-workshops",
                    "job-opportunities",
                    "partnerships",
                    "other",
                ]
            )
            .order_by("display_order", "name", "id")
            .values_list("slug", flat=True)
        )

        self.assertEqual(len(seeded), 19)
        self.assertEqual(
            seeded,
            [
                "networking",
                "mentoring",
                "career-development",
                "entrepreneurship",
                "technology",
                "leadership",
                "business-collaboration",
                "speaking-opportunities",
                "volunteering",
                "professional-development",
                "community-building",
                "investment-funding",
                "startups",
                "innovation",
                "social-impact",
                "events-workshops",
                "job-opportunities",
                "partnerships",
                "other",
            ],
        )

    def test_unrelated_custom_interest_is_preserved(self):
        custom_interest = Interest.objects.create(name="Board Service", slug="board-service")

        self.assertTrue(Interest.objects.filter(pk=custom_interest.pk).exists())

    def test_interest_list_anonymous_receives_401(self):
        response = self.client.get(self.interests_url)
        self.assertEqual(response.status_code, 401)

    def test_interest_list_nonstaff_receives_403(self):
        self.authenticate(self.non_staff_user)
        response = self.client.get(self.interests_url)
        self.assertEqual(response.status_code, 403)

    def test_interest_list_crm_viewer_receives_200(self):
        self.authenticate(self.viewer_user)
        response = self.client.get(self.interests_url)
        self.assertEqual(response.status_code, 200)

    def test_interest_list_crm_manager_receives_200(self):
        self.authenticate(self.manager_user)
        response = self.client.get(self.interests_url)
        self.assertEqual(response.status_code, 200)

    def test_interest_list_crm_admin_receives_200(self):
        self.authenticate(self.admin_user)
        response = self.client.get(self.interests_url)
        self.assertEqual(response.status_code, 200)

    def test_interest_list_returns_active_only_in_deterministic_order_with_limited_fields(self):
        self.authenticate(self.admin_user)
        response = self.client.get(self.interests_url)

        self.assertEqual(response.status_code, 200)
        self.assertNotIn(self.inactive_interest.slug, [row["slug"] for row in response.data])
        self.assertEqual(list(response.data[0].keys()), ["id", "name", "slug"])
        self.assertEqual(response.data[0]["slug"], "networking")
        self.assertEqual(response.data[-1]["slug"], "other")

    def test_person_interests_anonymous_receives_401(self):
        response = self.client.get(self.get_person_interests_url(self.active_business_person.id))
        self.assertEqual(response.status_code, 401)

    def test_person_interests_nonstaff_receives_403(self):
        self.authenticate(self.non_staff_user)
        response = self.client.get(self.get_person_interests_url(self.active_business_person.id))
        self.assertEqual(response.status_code, 403)

    def test_person_interests_crm_viewer_receives_200(self):
        self.authenticate(self.viewer_user)
        response = self.client.get(self.get_person_interests_url(self.active_business_person.id))
        self.assertEqual(response.status_code, 200)

    def test_person_interests_crm_manager_receives_200(self):
        self.authenticate(self.manager_user)
        response = self.client.get(self.get_person_interests_url(self.active_business_person.id))
        self.assertEqual(response.status_code, 200)

    def test_person_interests_crm_admin_receives_200(self):
        self.authenticate(self.admin_user)
        response = self.client.get(self.get_person_interests_url(self.active_business_person.id))
        self.assertEqual(response.status_code, 200)

    def test_person_interests_business_person_with_no_interests_returns_empty_list(self):
        self.authenticate(self.admin_user)
        response = self.client.get(self.get_person_interests_url(self.active_business_person.id))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, [])

    def test_person_interests_business_person_returns_active_interests_only_in_order(self):
        PersonInterest.objects.create(person=self.active_business_person, interest=self.startups)
        PersonInterest.objects.create(person=self.active_business_person, interest=self.technology)
        PersonInterest.objects.create(person=self.active_business_person, interest=self.inactive_interest)

        self.authenticate(self.admin_user)
        response = self.client.get(self.get_person_interests_url(self.active_business_person.id))

        self.assertEqual(
            response.data,
            [
                {"id": self.technology.id, "name": "Technology", "slug": "technology"},
                {"id": self.startups.id, "name": "Startups", "slug": "startups"},
            ],
        )

    def test_person_interests_archived_business_person_returns_200(self):
        PersonInterest.objects.create(person=self.archived_business_person, interest=self.networking)

        self.authenticate(self.admin_user)
        response = self.client.get(self.get_person_interests_url(self.archived_business_person.id))
        self.assertEqual(response.status_code, 200)

    def test_person_interests_technical_person_returns_404(self):
        self.authenticate(self.admin_user)
        response = self.client.get(self.get_person_interests_url(self.technical_person.id))
        self.assertEqual(response.status_code, 404)

    def test_person_interests_technical_person_linked_to_crm_admin_still_returns_404(self):
        PersonInterest.objects.create(person=self.technical_admin_user.person, interest=self.networking)

        self.authenticate(self.admin_user)
        response = self.client.get(self.get_person_interests_url(self.technical_admin_user.person_id))
        self.assertEqual(response.status_code, 404)

    def test_person_interests_business_person_linked_to_crm_admin_returns_200(self):
        PersonInterest.objects.create(person=self.business_admin_person, interest=self.networking)

        self.authenticate(self.admin_user)
        response = self.client.get(self.get_person_interests_url(self.business_admin_person.id))
        self.assertEqual(response.status_code, 200)

    def test_person_interests_nonexistent_person_returns_404(self):
        self.authenticate(self.admin_user)
        response = self.client.get(self.get_person_interests_url(999999))
        self.assertEqual(response.status_code, 404)

    def test_person_interests_do_not_expose_person_interest_internals(self):
        PersonInterest.objects.create(person=self.active_business_person, interest=self.networking)

        self.authenticate(self.admin_user)
        response = self.client.get(self.get_person_interests_url(self.active_business_person.id))

        self.assertEqual(list(response.data[0].keys()), ["id", "name", "slug"])

    def test_overview_returns_empty_interests_when_none_are_assigned(self):
        self.authenticate(self.admin_user)
        response = self.client.get(self.get_person_overview_url(self.active_business_person.id))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["interests"], [])
        self.assertEqual(response.data["skills"], [])
        self.assertEqual(response.data["membership"]["id"], self.membership.id)
        self.assertEqual(response.data["professional_profile"]["id"], self.professional_profile.id)
        self.assertEqual(response.data["relationship"]["type"], "ACTIVE_MEMBER")

    def test_overview_includes_assigned_active_interests(self):
        PersonInterest.objects.create(person=self.active_business_person, interest=self.startups)
        PersonInterest.objects.create(person=self.active_business_person, interest=self.technology)
        PersonSkill.objects.create(person=self.active_business_person, skill=self.accounting_skill)

        self.authenticate(self.admin_user)
        response = self.client.get(self.get_person_overview_url(self.active_business_person.id))

        self.assertEqual(
            response.data["interests"],
            [
                {"id": self.technology.id, "name": "Technology", "slug": "technology"},
                {"id": self.startups.id, "name": "Startups", "slug": "startups"},
            ],
        )
        self.assertEqual(
            response.data["skills"],
            [{"id": self.accounting_skill.id, "name": "Accounting", "slug": "accounting"}],
        )
        self.assertEqual(response.data["membership"]["id"], self.membership.id)
        self.assertEqual(response.data["professional_profile"]["id"], self.professional_profile.id)
        self.assertEqual(response.data["relationship"]["type"], "ACTIVE_MEMBER")

    def test_overview_omits_inactive_assigned_interests(self):
        PersonInterest.objects.create(person=self.active_business_person, interest=self.networking)
        PersonInterest.objects.create(person=self.active_business_person, interest=self.inactive_interest)

        self.authenticate(self.admin_user)
        response = self.client.get(self.get_person_overview_url(self.active_business_person.id))

        self.assertEqual(
            response.data["interests"],
            [{"id": self.networking.id, "name": "Networking", "slug": "networking"}],
        )

    def test_overview_archived_business_person_returns_interests(self):
        PersonInterest.objects.create(person=self.archived_business_person, interest=self.networking)

        self.authenticate(self.admin_user)
        response = self.client.get(self.get_person_overview_url(self.archived_business_person.id))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data["interests"],
            [{"id": self.networking.id, "name": "Networking", "slug": "networking"}],
        )

    def test_overview_technical_person_stays_404(self):
        PersonInterest.objects.create(person=self.technical_person, interest=self.networking)

        self.authenticate(self.admin_user)
        response = self.client.get(self.get_person_overview_url(self.technical_person.id))

        self.assertEqual(response.status_code, 404)
