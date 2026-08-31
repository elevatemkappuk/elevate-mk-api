from django.contrib.admin.sites import AdminSite
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.db.models import ProtectedError
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import User
from interests.models import Interest, PersonInterest
from memberships.models import Membership
from people.models import Person
from professional_profiles.models import Industry, ProfessionalProfile
from rest_framework.test import APIClient
from skills.models import PersonSkill, Skill
from staff_access.models import StaffRole, StaffRoleAssignment
from tags.admin import PersonTagAdmin, TagAdmin
from tags.models import PersonTag, Tag


class TagModelTests(TestCase):
    def test_tag_defaults_apply(self):
        tag = Tag.objects.create(name="Pipeline Flag", slug="pipeline-flag")

        self.assertEqual(tag.description, "")
        self.assertTrue(tag.is_active)
        self.assertEqual(tag.display_order, 100)

    def test_slug_is_unique(self):
        Tag.objects.create(name="Potential Speaker", slug="potential-speaker-duplicate-check")

        with self.assertRaises(IntegrityError):
            Tag.objects.create(name="Potential Speaker Again", slug="potential-speaker-duplicate-check")

    def test_ordering_is_display_order_name_then_id(self):
        first = Tag.objects.create(name="Alpha", slug="tag-alpha", display_order=300)
        second = Tag.objects.create(name="Beta", slug="tag-beta", display_order=300)
        third = Tag.objects.create(name="Gamma", slug="tag-gamma", display_order=310)

        ordered_ids = list(Tag.objects.order_by("display_order", "name", "id").values_list("id", flat=True))
        self.assertEqual(ordered_ids[-3:], [first.id, second.id, third.id])

    def test_inactive_tag_is_persisted(self):
        tag = Tag.objects.create(name="Legacy", slug="legacy-tag", is_active=False)

        self.assertFalse(tag.is_active)


class PersonTagModelTests(TestCase):
    def setUp(self):
        self.assigned_by = User.objects.create_user(
            email="assigner-tags@example.com",
            password="testpass123",
            person_first_name="Assigning",
            person_last_name="User",
        )

    def test_required_assigned_by(self):
        person = Person.objects.create(first_name="Missing", last_name="Assigner")
        tag = Tag.objects.create(name="VIP", slug="vip-extra")
        person_tag = PersonTag(person=person, tag=tag)

        with self.assertRaises(ValidationError):
            person_tag.full_clean()

    def test_assigned_at_is_populated(self):
        person = Person.objects.create(first_name="Assigned", last_name="At")
        tag = Tag.objects.create(name="Partner Lead", slug="partner-lead-extra")
        person_tag = PersonTag.objects.create(person=person, tag=tag, assigned_by=self.assigned_by)

        self.assertIsNotNone(person_tag.assigned_at)

    def test_person_may_have_zero_tags(self):
        person = Person.objects.create(first_name="No", last_name="Tags")

        self.assertEqual(person.person_tags.count(), 0)

    def test_multiple_different_tags_are_allowed(self):
        person = Person.objects.create(first_name="Multi", last_name="Tagged")
        first = Tag.objects.create(name="VIP", slug="vip-extra-two")
        second = Tag.objects.create(name="Other", slug="other-extra")

        PersonTag.objects.create(person=person, tag=first, assigned_by=self.assigned_by)
        PersonTag.objects.create(person=person, tag=second, assigned_by=self.assigned_by)

        self.assertEqual(person.person_tags.count(), 2)

    def test_duplicate_person_tag_is_rejected(self):
        person = Person.objects.create(first_name="Duplicate", last_name="Tag")
        tag = Tag.objects.create(name="Potential Mentor", slug="potential-mentor-extra")
        PersonTag.objects.create(person=person, tag=tag, assigned_by=self.assigned_by)

        with self.assertRaises(IntegrityError):
            PersonTag.objects.create(person=person, tag=tag, assigned_by=self.assigned_by)

    def test_person_delete_is_protected(self):
        person = Person.objects.create(first_name="Protected", last_name="Person")
        tag = Tag.objects.create(name="Potential Volunteer", slug="potential-volunteer-extra")
        PersonTag.objects.create(person=person, tag=tag, assigned_by=self.assigned_by)

        with self.assertRaises(ProtectedError):
            person.delete()

    def test_tag_delete_is_protected(self):
        person = Person.objects.create(first_name="Protected", last_name="Tag")
        tag = Tag.objects.create(name="Follow-up Required", slug="follow-up-required-extra")
        PersonTag.objects.create(person=person, tag=tag, assigned_by=self.assigned_by)

        with self.assertRaises(ProtectedError):
            tag.delete()

    def test_assigned_by_delete_is_protected(self):
        person = Person.objects.create(first_name="Protected", last_name="Assigner")
        tag = Tag.objects.create(name="Sponsor Contact", slug="sponsor-contact-extra")
        PersonTag.objects.create(person=person, tag=tag, assigned_by=self.assigned_by)

        with self.assertRaises(ProtectedError):
            self.assigned_by.delete()

    def test_removed_by_delete_is_protected(self):
        person = Person.objects.create(first_name="Removed", last_name="By")
        tag = Tag.objects.create(name="Founding Member", slug="founding-member-extra")
        removed_by = User.objects.create_user(
            email="remover-tags@example.com",
            password="testpass123",
            person_first_name="Removing",
            person_last_name="User",
        )
        PersonTag.objects.create(
            person=person,
            tag=tag,
            assigned_by=self.assigned_by,
            is_active=False,
            removed_by=removed_by,
            removed_at=timezone.now(),
        )

        with self.assertRaises(ProtectedError):
            removed_by.delete()

    def test_active_lifecycle_validation_requires_removed_fields_to_be_null(self):
        person = Person.objects.create(first_name="Active", last_name="Lifecycle")
        tag = Tag.objects.create(name="VIP", slug="vip-lifecycle-active")
        person_tag = PersonTag(
            person=person,
            tag=tag,
            assigned_by=self.assigned_by,
            removed_by=self.assigned_by,
            removed_at=timezone.now(),
        )

        with self.assertRaises(ValidationError):
            person_tag.full_clean()

    def test_inactive_lifecycle_validation_requires_removed_fields(self):
        person = Person.objects.create(first_name="Inactive", last_name="Lifecycle")
        tag = Tag.objects.create(name="Other", slug="other-lifecycle-inactive")
        person_tag = PersonTag(
            person=person,
            tag=tag,
            assigned_by=self.assigned_by,
            is_active=False,
        )

        with self.assertRaises(ValidationError):
            person_tag.full_clean()


class TagAdminTests(TestCase):
    def setUp(self):
        self.site = AdminSite()
        self.tag_admin = TagAdmin(Tag, self.site)
        self.person_tag_admin = PersonTagAdmin(PersonTag, self.site)

    def test_tag_admin_configuration(self):
        self.assertEqual(self.tag_admin.list_display, ("name", "slug", "is_active", "display_order"))
        self.assertEqual(self.tag_admin.list_filter, ("is_active",))
        self.assertEqual(self.tag_admin.readonly_fields, ("created_at", "updated_at"))

    def test_person_tag_admin_configuration(self):
        self.assertEqual(
            self.person_tag_admin.list_display,
            ("person", "tag", "is_active", "assigned_by", "assigned_at", "removed_by", "removed_at"),
        )
        self.assertEqual(self.person_tag_admin.readonly_fields, ("assigned_at",))


@override_settings(ROOT_URLCONF="config.urls")
class TagApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.tags_url = "/api/v1/tags/"
        self.person_tags_url_template = "/api/v1/people/{person_id}/tags/"
        self.person_overview_url_template = "/api/v1/people/{person_id}/overview/"

        self.non_staff_user = User.objects.create_user(
            email="nonstaff-tags@example.com",
            password="testpass123",
            person_first_name="Non",
            person_last_name="Staff",
        )
        self.admin_user = User.objects.create_user(
            email="admin-tags@example.com",
            password="testpass123",
            person_first_name="Admin",
            person_last_name="User",
        )
        self.manager_user = User.objects.create_user(
            email="manager-tags@example.com",
            password="testpass123",
            person_first_name="Manager",
            person_last_name="User",
        )
        self.viewer_user = User.objects.create_user(
            email="viewer-tags@example.com",
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

        self.potential_mentor = Tag.objects.get(slug="potential-mentor")
        self.vip = Tag.objects.get(slug="vip")
        self.other = Tag.objects.get(slug="other")
        self.inactive_tag = Tag.objects.create(
            name="Inactive Tag",
            slug="inactive-tag",
            is_active=False,
            display_order=999,
        )

        self.accounting_skill = Skill.objects.get(slug="accounting")
        self.technology_interest = Interest.objects.get(slug="technology")
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

    def get_person_tags_url(self, person_id):
        return self.person_tags_url_template.format(person_id=person_id)

    def get_person_overview_url(self, person_id):
        return self.person_overview_url_template.format(person_id=person_id)

    def test_tag_seed_contains_9_canonical_rows(self):
        seeded = list(
            Tag.objects.filter(
                slug__in=[
                    "potential-speaker",
                    "potential-mentor",
                    "potential-volunteer",
                    "partner-lead",
                    "sponsor-contact",
                    "follow-up-required",
                    "founding-member",
                    "vip",
                    "other",
                ]
            ).order_by("display_order", "name", "id").values_list("slug", flat=True)
        )

        self.assertEqual(len(seeded), 9)
        self.assertEqual(
            seeded,
            [
                "potential-speaker",
                "potential-mentor",
                "potential-volunteer",
                "partner-lead",
                "sponsor-contact",
                "follow-up-required",
                "founding-member",
                "vip",
                "other",
            ],
        )

    def test_unrelated_custom_tag_is_preserved(self):
        custom_tag = Tag.objects.create(name="Board Priority", slug="board-priority")

        self.assertTrue(Tag.objects.filter(pk=custom_tag.pk).exists())

    def test_tag_list_anonymous_receives_401(self):
        response = self.client.get(self.tags_url)
        self.assertEqual(response.status_code, 401)

    def test_tag_list_nonstaff_receives_403(self):
        self.authenticate(self.non_staff_user)
        response = self.client.get(self.tags_url)
        self.assertEqual(response.status_code, 403)

    def test_tag_list_crm_viewer_receives_200(self):
        self.authenticate(self.viewer_user)
        response = self.client.get(self.tags_url)
        self.assertEqual(response.status_code, 200)

    def test_tag_list_crm_manager_receives_200(self):
        self.authenticate(self.manager_user)
        response = self.client.get(self.tags_url)
        self.assertEqual(response.status_code, 200)

    def test_tag_list_crm_admin_receives_200(self):
        self.authenticate(self.admin_user)
        response = self.client.get(self.tags_url)
        self.assertEqual(response.status_code, 200)

    def test_tag_list_returns_active_only_in_deterministic_order_with_limited_fields(self):
        self.authenticate(self.admin_user)
        response = self.client.get(self.tags_url)

        self.assertEqual(response.status_code, 200)
        self.assertNotIn(self.inactive_tag.slug, [row["slug"] for row in response.data])
        self.assertEqual(list(response.data[0].keys()), ["id", "name", "slug"])
        self.assertEqual(response.data[0]["slug"], "potential-speaker")
        self.assertEqual(response.data[-1]["slug"], "other")

    def test_person_tags_anonymous_receives_401(self):
        response = self.client.get(self.get_person_tags_url(self.active_business_person.id))
        self.assertEqual(response.status_code, 401)

    def test_person_tags_nonstaff_receives_403(self):
        self.authenticate(self.non_staff_user)
        response = self.client.get(self.get_person_tags_url(self.active_business_person.id))
        self.assertEqual(response.status_code, 403)

    def test_person_tags_crm_viewer_receives_200(self):
        self.authenticate(self.viewer_user)
        response = self.client.get(self.get_person_tags_url(self.active_business_person.id))
        self.assertEqual(response.status_code, 200)

    def test_person_tags_crm_manager_receives_200(self):
        self.authenticate(self.manager_user)
        response = self.client.get(self.get_person_tags_url(self.active_business_person.id))
        self.assertEqual(response.status_code, 200)

    def test_person_tags_crm_admin_receives_200(self):
        self.authenticate(self.admin_user)
        response = self.client.get(self.get_person_tags_url(self.active_business_person.id))
        self.assertEqual(response.status_code, 200)

    def test_person_tags_business_person_with_no_active_tags_returns_empty_list(self):
        self.authenticate(self.admin_user)
        response = self.client.get(self.get_person_tags_url(self.active_business_person.id))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, [])

    def test_person_tags_business_person_returns_active_tags_only_in_order(self):
        PersonTag.objects.create(person=self.active_business_person, tag=self.vip, assigned_by=self.admin_user)
        PersonTag.objects.create(person=self.active_business_person, tag=self.potential_mentor, assigned_by=self.admin_user)
        PersonTag.objects.create(
            person=self.active_business_person,
            tag=self.inactive_tag,
            assigned_by=self.admin_user,
        )

        self.authenticate(self.admin_user)
        response = self.client.get(self.get_person_tags_url(self.active_business_person.id))

        self.assertEqual(
            response.data,
            [
                {"id": self.potential_mentor.id, "name": "Potential Mentor", "slug": "potential-mentor"},
                {"id": self.vip.id, "name": "VIP", "slug": "vip"},
            ],
        )

    def test_person_tags_archived_business_person_returns_200(self):
        PersonTag.objects.create(person=self.archived_business_person, tag=self.vip, assigned_by=self.admin_user)

        self.authenticate(self.admin_user)
        response = self.client.get(self.get_person_tags_url(self.archived_business_person.id))
        self.assertEqual(response.status_code, 200)

    def test_person_tags_technical_person_returns_404(self):
        self.authenticate(self.admin_user)
        response = self.client.get(self.get_person_tags_url(self.technical_person.id))
        self.assertEqual(response.status_code, 404)

    def test_person_tags_nonexistent_person_returns_404(self):
        self.authenticate(self.admin_user)
        response = self.client.get(self.get_person_tags_url(999999))
        self.assertEqual(response.status_code, 404)

    def test_inactive_person_tag_is_omitted(self):
        PersonTag.objects.create(
            person=self.active_business_person,
            tag=self.vip,
            assigned_by=self.admin_user,
            is_active=False,
            removed_by=self.manager_user,
            removed_at=timezone.now(),
        )

        self.authenticate(self.admin_user)
        response = self.client.get(self.get_person_tags_url(self.active_business_person.id))

        self.assertEqual(response.data, [])

    def test_inactive_tag_definition_is_omitted(self):
        PersonTag.objects.create(
            person=self.active_business_person,
            tag=self.inactive_tag,
            assigned_by=self.admin_user,
        )

        self.authenticate(self.admin_user)
        response = self.client.get(self.get_person_tags_url(self.active_business_person.id))

        self.assertEqual(response.data, [])

    def test_person_tags_do_not_expose_lifecycle_metadata(self):
        PersonTag.objects.create(person=self.active_business_person, tag=self.vip, assigned_by=self.admin_user)

        self.authenticate(self.admin_user)
        response = self.client.get(self.get_person_tags_url(self.active_business_person.id))

        self.assertEqual(list(response.data[0].keys()), ["id", "name", "slug"])

    def test_overview_returns_empty_tags_when_none_are_assigned(self):
        self.authenticate(self.admin_user)
        response = self.client.get(self.get_person_overview_url(self.active_business_person.id))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["tags"], [])
        self.assertEqual(response.data["skills"], [])
        self.assertEqual(response.data["interests"], [])
        self.assertEqual(response.data["membership"]["id"], self.membership.id)
        self.assertEqual(response.data["professional_profile"]["id"], self.professional_profile.id)
        self.assertEqual(response.data["relationship"]["type"], "ACTIVE_MEMBER")

    def test_overview_includes_active_tags_without_changing_other_domains(self):
        PersonTag.objects.create(person=self.active_business_person, tag=self.vip, assigned_by=self.admin_user)
        PersonTag.objects.create(person=self.active_business_person, tag=self.potential_mentor, assigned_by=self.admin_user)
        PersonSkill.objects.create(person=self.active_business_person, skill=self.accounting_skill)
        PersonInterest.objects.create(person=self.active_business_person, interest=self.technology_interest)

        self.authenticate(self.admin_user)
        response = self.client.get(self.get_person_overview_url(self.active_business_person.id))

        self.assertEqual(
            response.data["tags"],
            [
                {"id": self.potential_mentor.id, "name": "Potential Mentor", "slug": "potential-mentor"},
                {"id": self.vip.id, "name": "VIP", "slug": "vip"},
            ],
        )
        self.assertEqual(
            response.data["skills"],
            [{"id": self.accounting_skill.id, "name": "Accounting", "slug": "accounting"}],
        )
        self.assertEqual(
            response.data["interests"],
            [{"id": self.technology_interest.id, "name": "Technology", "slug": "technology"}],
        )
        self.assertEqual(response.data["membership"]["id"], self.membership.id)
        self.assertEqual(response.data["professional_profile"]["id"], self.professional_profile.id)
        self.assertEqual(response.data["relationship"]["type"], "ACTIVE_MEMBER")

    def test_overview_omits_inactive_person_tag(self):
        PersonTag.objects.create(
            person=self.active_business_person,
            tag=self.vip,
            assigned_by=self.admin_user,
            is_active=False,
            removed_by=self.manager_user,
            removed_at=timezone.now(),
        )

        self.authenticate(self.admin_user)
        response = self.client.get(self.get_person_overview_url(self.active_business_person.id))

        self.assertEqual(response.data["tags"], [])

    def test_overview_omits_inactive_tag_definition(self):
        PersonTag.objects.create(person=self.active_business_person, tag=self.inactive_tag, assigned_by=self.admin_user)

        self.authenticate(self.admin_user)
        response = self.client.get(self.get_person_overview_url(self.active_business_person.id))

        self.assertEqual(response.data["tags"], [])

    def test_overview_archived_business_person_returns_tags(self):
        PersonTag.objects.create(person=self.archived_business_person, tag=self.vip, assigned_by=self.admin_user)

        self.authenticate(self.admin_user)
        response = self.client.get(self.get_person_overview_url(self.archived_business_person.id))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data["tags"],
            [{"id": self.vip.id, "name": "VIP", "slug": "vip"}],
        )

    def test_overview_technical_person_stays_404(self):
        PersonTag.objects.create(person=self.technical_person, tag=self.vip, assigned_by=self.admin_user)

        self.authenticate(self.admin_user)
        response = self.client.get(self.get_person_overview_url(self.technical_person.id))

        self.assertEqual(response.status_code, 404)
