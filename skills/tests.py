from django.contrib.admin.sites import AdminSite
from django.db import IntegrityError
from django.db.models import ProtectedError
from django.test import TestCase, override_settings
from django.utils import timezone
from unittest.mock import patch

from accounts.models import User
from audit.models import AuditEvent
from memberships.models import Membership
from people.models import Person
from professional_profiles.models import ProfessionalProfile
from rest_framework.test import APIClient
from staff_access.models import StaffRole, StaffRoleAssignment
from skills.admin import PersonSkillAdmin, SkillAdmin
from skills.models import PersonSkill, Skill


class SkillModelTests(TestCase):
    def test_skill_defaults_apply(self):
        skill = Skill.objects.create(name="Systems Thinking", slug="systems-thinking")

        self.assertEqual(skill.description, "")
        self.assertTrue(skill.is_active)
        self.assertEqual(skill.display_order, 100)

    def test_slug_is_unique(self):
        Skill.objects.create(name="Writing", slug="writing")

        with self.assertRaises(IntegrityError):
            Skill.objects.create(name="Duplicate Writing", slug="writing")

    def test_ordering_is_display_order_name_then_id(self):
        first = Skill.objects.create(name="Accounting", slug="accounting-extra", display_order=300)
        second = Skill.objects.create(name="Strategy", slug="strategy-extra", display_order=300)
        third = Skill.objects.create(name="Zoology", slug="zoology-extra", display_order=310)

        ordered_ids = list(Skill.objects.order_by("display_order", "name", "id").values_list("id", flat=True))
        self.assertEqual(ordered_ids[-3:], [first.id, second.id, third.id])

    def test_inactive_skill_is_persisted(self):
        skill = Skill.objects.create(name="Legacy", slug="legacy-skill", is_active=False)

        self.assertFalse(skill.is_active)


class PersonSkillModelTests(TestCase):
    def test_person_may_have_zero_skills(self):
        person = Person.objects.create(first_name="No", last_name="Skills")

        self.assertEqual(person.person_skills.count(), 0)

    def test_multiple_different_skills_are_allowed(self):
        person = Person.objects.create(first_name="Multi", last_name="Skilled")
        first = Skill.objects.create(name="Facilitation", slug="facilitation")
        second = Skill.objects.create(name="Negotiation", slug="negotiation")

        PersonSkill.objects.create(person=person, skill=first)
        PersonSkill.objects.create(person=person, skill=second)

        self.assertEqual(person.person_skills.count(), 2)

    def test_duplicate_person_skill_is_rejected(self):
        person = Person.objects.create(first_name="Duplicate", last_name="Skill")
        skill = Skill.objects.create(name="Moderation", slug="moderation")
        PersonSkill.objects.create(person=person, skill=skill)

        with self.assertRaises(IntegrityError):
            PersonSkill.objects.create(person=person, skill=skill)

    def test_person_delete_is_protected(self):
        person = Person.objects.create(first_name="Protected", last_name="Person")
        skill = Skill.objects.create(name="Protection", slug="protection")
        PersonSkill.objects.create(person=person, skill=skill)

        with self.assertRaises(ProtectedError):
            person.delete()

    def test_skill_delete_is_protected(self):
        person = Person.objects.create(first_name="Protected", last_name="Skill")
        skill = Skill.objects.create(name="Deletion", slug="deletion")
        PersonSkill.objects.create(person=person, skill=skill)

        with self.assertRaises(ProtectedError):
            skill.delete()


class SkillAdminTests(TestCase):
    def setUp(self):
        self.site = AdminSite()
        self.skill_admin = SkillAdmin(Skill, self.site)
        self.person_skill_admin = PersonSkillAdmin(PersonSkill, self.site)

    def test_skill_admin_configuration(self):
        self.assertEqual(self.skill_admin.list_display, ("name", "slug", "is_active", "display_order"))
        self.assertEqual(self.skill_admin.list_filter, ("is_active",))
        self.assertEqual(self.skill_admin.readonly_fields, ("created_at", "updated_at"))

    def test_person_skill_admin_configuration(self):
        self.assertEqual(self.person_skill_admin.list_display, ("person", "skill", "created_at"))
        self.assertEqual(self.person_skill_admin.readonly_fields, ("created_at",))


@override_settings(ROOT_URLCONF="config.urls")
class SkillApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.skills_url = "/api/v1/skills/"
        self.person_skills_url_template = "/api/v1/people/{person_id}/skills/"
        self.person_skill_detail_url_template = "/api/v1/people/{person_id}/skills/{skill_id}/"
        self.person_overview_url_template = "/api/v1/people/{person_id}/overview/"

        self.non_staff_user = User.objects.create_user(
            email="nonstaff-skills@example.com",
            password="testpass123",
            person_first_name="Non",
            person_last_name="Staff",
        )
        self.admin_user = User.objects.create_user(
            email="admin-skills@example.com",
            password="testpass123",
            person_first_name="Admin",
            person_last_name="User",
        )
        self.manager_user = User.objects.create_user(
            email="manager-skills@example.com",
            password="testpass123",
            person_first_name="Manager",
            person_last_name="User",
        )
        self.viewer_user = User.objects.create_user(
            email="viewer-skills@example.com",
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

        self.accounting = Skill.objects.get(slug="accounting")
        self.strategy = Skill.objects.get(slug="strategy")
        self.project_management = Skill.objects.get(slug="project-management")
        self.inactive_skill = Skill.objects.create(
            name="Inactive Assignment Skill",
            slug="inactive-assignment-skill",
            is_active=False,
            display_order=999,
        )
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
        )

    def authenticate(self, user):
        self.client.force_authenticate(user=user)

    def get_person_skills_url(self, person_id):
        return self.person_skills_url_template.format(person_id=person_id)

    def get_person_skill_detail_url(self, person_id, skill_id):
        return self.person_skill_detail_url_template.format(person_id=person_id, skill_id=skill_id)

    def get_person_overview_url(self, person_id):
        return self.person_overview_url_template.format(person_id=person_id)

    def test_skill_seed_contains_27_canonical_rows(self):
        seeded = list(
            Skill.objects.filter(slug__in=[
                "accounting",
                "business-development",
                "coaching",
                "content-creation",
                "customer-service",
                "data-analysis",
                "digital-marketing",
                "event-management",
                "finance",
                "graphic-design",
                "leadership",
                "marketing",
                "mentoring",
                "operations-management",
                "photography",
                "project-management",
                "public-speaking",
                "recruitment",
                "sales",
                "social-media-management",
                "software-development",
                "strategy",
                "teaching-training",
                "video-production",
                "web-design",
                "writing-editing",
                "other",
            ]).order_by("display_order", "name", "id").values_list("slug", flat=True)
        )

        self.assertEqual(len(seeded), 27)
        self.assertEqual(
            seeded,
            [
                "accounting",
                "business-development",
                "coaching",
                "content-creation",
                "customer-service",
                "data-analysis",
                "digital-marketing",
                "event-management",
                "finance",
                "graphic-design",
                "leadership",
                "marketing",
                "mentoring",
                "operations-management",
                "photography",
                "project-management",
                "public-speaking",
                "recruitment",
                "sales",
                "social-media-management",
                "software-development",
                "strategy",
                "teaching-training",
                "video-production",
                "web-design",
                "writing-editing",
                "other",
            ],
        )

    def test_unrelated_custom_skill_is_preserved(self):
        custom_skill = Skill.objects.create(name="Community Mediation", slug="community-mediation")

        self.assertTrue(Skill.objects.filter(pk=custom_skill.pk).exists())

    def test_skill_list_anonymous_receives_401(self):
        response = self.client.get(self.skills_url)
        self.assertEqual(response.status_code, 401)

    def test_skill_list_nonstaff_receives_403(self):
        self.authenticate(self.non_staff_user)
        response = self.client.get(self.skills_url)
        self.assertEqual(response.status_code, 403)

    def test_skill_list_crm_viewer_receives_200(self):
        self.authenticate(self.viewer_user)
        response = self.client.get(self.skills_url)
        self.assertEqual(response.status_code, 200)

    def test_skill_list_crm_manager_receives_200(self):
        self.authenticate(self.manager_user)
        response = self.client.get(self.skills_url)
        self.assertEqual(response.status_code, 200)

    def test_skill_list_crm_admin_receives_200(self):
        self.authenticate(self.admin_user)
        response = self.client.get(self.skills_url)
        self.assertEqual(response.status_code, 200)

    def test_skill_list_returns_active_only_in_deterministic_order_with_limited_fields(self):
        inactive_skill = Skill.objects.create(
            name="Legacy Skill",
            slug="legacy-skill",
            display_order=5,
            is_active=False,
        )
        self.authenticate(self.admin_user)
        response = self.client.get(self.skills_url)

        self.assertEqual(response.status_code, 200)
        self.assertNotIn(inactive_skill.slug, [row["slug"] for row in response.data])
        self.assertEqual(list(response.data[0].keys()), ["id", "name", "slug"])
        self.assertEqual(response.data[0]["slug"], "accounting")
        self.assertEqual(response.data[-1]["slug"], "other")

    def test_person_skills_anonymous_receives_401(self):
        response = self.client.get(self.get_person_skills_url(self.active_business_person.id))
        self.assertEqual(response.status_code, 401)

    def test_person_skills_nonstaff_receives_403(self):
        self.authenticate(self.non_staff_user)
        response = self.client.get(self.get_person_skills_url(self.active_business_person.id))
        self.assertEqual(response.status_code, 403)

    def test_person_skills_crm_viewer_receives_200(self):
        self.authenticate(self.viewer_user)
        response = self.client.get(self.get_person_skills_url(self.active_business_person.id))
        self.assertEqual(response.status_code, 200)

    def test_person_skills_crm_manager_receives_200(self):
        self.authenticate(self.manager_user)
        response = self.client.get(self.get_person_skills_url(self.active_business_person.id))
        self.assertEqual(response.status_code, 200)

    def test_person_skills_crm_admin_receives_200(self):
        self.authenticate(self.admin_user)
        response = self.client.get(self.get_person_skills_url(self.active_business_person.id))
        self.assertEqual(response.status_code, 200)

    def test_person_skills_business_person_with_no_skills_returns_empty_list(self):
        self.authenticate(self.admin_user)
        response = self.client.get(self.get_person_skills_url(self.active_business_person.id))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, [])

    def test_person_skills_business_person_returns_active_skills_only_in_order(self):
        inactive_skill = Skill.objects.create(
            name="Legacy Skill",
            slug="legacy-person-skill",
            display_order=5,
            is_active=False,
        )
        PersonSkill.objects.create(person=self.active_business_person, skill=self.strategy)
        PersonSkill.objects.create(person=self.active_business_person, skill=self.accounting)
        PersonSkill.objects.create(person=self.active_business_person, skill=inactive_skill)

        self.authenticate(self.admin_user)
        response = self.client.get(self.get_person_skills_url(self.active_business_person.id))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data,
            [
                {"id": self.accounting.id, "name": "Accounting", "slug": "accounting"},
                {"id": self.strategy.id, "name": "Strategy", "slug": "strategy"},
            ],
        )

    def test_person_skills_archived_business_person_returns_200(self):
        PersonSkill.objects.create(person=self.archived_business_person, skill=self.accounting)

        self.authenticate(self.admin_user)
        response = self.client.get(self.get_person_skills_url(self.archived_business_person.id))
        self.assertEqual(response.status_code, 200)

    def test_person_skills_technical_person_returns_404(self):
        self.authenticate(self.admin_user)
        response = self.client.get(self.get_person_skills_url(self.technical_person.id))
        self.assertEqual(response.status_code, 404)

    def test_person_skills_nonexistent_person_returns_404(self):
        self.authenticate(self.admin_user)
        response = self.client.get(self.get_person_skills_url(999999))
        self.assertEqual(response.status_code, 404)

    def test_person_skills_does_not_expose_person_skill_internals(self):
        PersonSkill.objects.create(person=self.active_business_person, skill=self.accounting)

        self.authenticate(self.admin_user)
        response = self.client.get(self.get_person_skills_url(self.active_business_person.id))

        self.assertEqual(list(response.data[0].keys()), ["id", "name", "slug"])

    def test_assign_skill_anonymous_receives_401(self):
        response = self.client.post(self.get_person_skills_url(self.active_business_person.id), {"skill": self.accounting.id}, format="json")
        self.assertEqual(response.status_code, 401)

    def test_assign_skill_nonstaff_receives_403(self):
        self.authenticate(self.non_staff_user)
        response = self.client.post(self.get_person_skills_url(self.active_business_person.id), {"skill": self.accounting.id}, format="json")
        self.assertEqual(response.status_code, 403)

    def test_assign_skill_viewer_receives_403(self):
        self.authenticate(self.viewer_user)
        response = self.client.post(self.get_person_skills_url(self.active_business_person.id), {"skill": self.accounting.id}, format="json")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(AuditEvent.objects.filter(action=AuditEvent.Action.SKILL_ASSIGNED).count(), 0)

    def test_assign_skill_manager_receives_201(self):
        self.authenticate(self.manager_user)
        response = self.client.post(self.get_person_skills_url(self.active_business_person.id), {"skill": self.accounting.id}, format="json")
        self.assertEqual(response.status_code, 201)

    def test_assign_skill_admin_receives_201(self):
        self.authenticate(self.admin_user)
        response = self.client.post(self.get_person_skills_url(self.active_business_person.id), {"skill": self.accounting.id}, format="json")
        self.assertEqual(response.status_code, 201)

    def test_assign_skill_active_business_and_active_skill_creates_person_skill(self):
        self.authenticate(self.admin_user)
        response = self.client.post(self.get_person_skills_url(self.active_business_person.id), {"skill": self.project_management.id}, format="json")

        self.assertEqual(response.status_code, 201)
        self.assertTrue(PersonSkill.objects.filter(person=self.active_business_person, skill=self.project_management).exists())
        self.assertEqual(
            response.data,
            {"id": self.project_management.id, "name": "Project Management", "slug": "project-management"},
        )

    def test_successful_skill_assignment_writes_skill_assigned_audit_event(self):
        self.authenticate(self.admin_user)
        response = self.client.post(
            self.get_person_skills_url(self.active_business_person.id),
            {"skill": self.project_management.id},
            format="json",
        )

        person_skill = PersonSkill.objects.get(person=self.active_business_person, skill=self.project_management)
        event = AuditEvent.objects.get(action=AuditEvent.Action.SKILL_ASSIGNED)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(event.actor_user, self.admin_user)
        self.assertEqual(event.entity_type, "PersonSkill")
        self.assertEqual(event.entity_id, str(person_skill.id))
        self.assertEqual(
            event.metadata,
            {"person_id": str(self.active_business_person.id), "skill_id": str(self.project_management.id)},
        )
        self.assertEqual(event.changes, {"assigned": {"from": False, "to": True}})

    def test_assign_skill_duplicate_returns_409_and_does_not_create_second_row(self):
        PersonSkill.objects.create(person=self.active_business_person, skill=self.accounting)

        self.authenticate(self.admin_user)
        response = self.client.post(self.get_person_skills_url(self.active_business_person.id), {"skill": self.accounting.id}, format="json")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(PersonSkill.objects.filter(person=self.active_business_person, skill=self.accounting).count(), 1)
        self.assertEqual(AuditEvent.objects.filter(action=AuditEvent.Action.SKILL_ASSIGNED).count(), 0)

    def test_assign_skill_inactive_skill_returns_400(self):
        self.authenticate(self.admin_user)
        response = self.client.post(self.get_person_skills_url(self.active_business_person.id), {"skill": self.inactive_skill.id}, format="json")

        self.assertEqual(response.status_code, 400)
        self.assertIn("skill", response.data)
        self.assertEqual(AuditEvent.objects.filter(action=AuditEvent.Action.SKILL_ASSIGNED).count(), 0)

    def test_assign_skill_nonexistent_skill_returns_400(self):
        self.authenticate(self.admin_user)
        response = self.client.post(self.get_person_skills_url(self.active_business_person.id), {"skill": 999999}, format="json")

        self.assertEqual(response.status_code, 400)
        self.assertIn("skill", response.data)

    def test_assign_skill_archived_business_person_returns_409(self):
        self.authenticate(self.admin_user)
        response = self.client.post(self.get_person_skills_url(self.archived_business_person.id), {"skill": self.accounting.id}, format="json")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(AuditEvent.objects.filter(action=AuditEvent.Action.SKILL_ASSIGNED).count(), 0)

    def test_assign_skill_technical_person_returns_404(self):
        self.authenticate(self.admin_user)
        response = self.client.post(self.get_person_skills_url(self.technical_person.id), {"skill": self.accounting.id}, format="json")
        self.assertEqual(response.status_code, 404)

    def test_assign_skill_nonexistent_person_returns_404(self):
        self.authenticate(self.admin_user)
        response = self.client.post(self.get_person_skills_url(999999), {"skill": self.accounting.id}, format="json")
        self.assertEqual(response.status_code, 404)

    def test_assign_skill_client_cannot_control_person(self):
        other_person = Person.objects.create(first_name="Other", last_name="Person")

        self.authenticate(self.admin_user)
        response = self.client.post(
            self.get_person_skills_url(self.active_business_person.id),
            {"skill": self.accounting.id, "person": other_person.id},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("person", response.data)
        self.assertFalse(PersonSkill.objects.filter(person=other_person, skill=self.accounting).exists())

    def test_assign_skill_unknown_fields_are_rejected(self):
        self.authenticate(self.admin_user)
        response = self.client.post(
            self.get_person_skills_url(self.active_business_person.id),
            {"skill": self.accounting.id, "created_at": "2026-08-31T10:00:00Z"},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("created_at", response.data)

    def test_assign_skill_response_does_not_expose_person_skill_id(self):
        self.authenticate(self.admin_user)
        response = self.client.post(self.get_person_skills_url(self.active_business_person.id), {"skill": self.accounting.id}, format="json")

        self.assertEqual(response.status_code, 201)
        self.assertEqual(set(response.data.keys()), {"id", "name", "slug"})

    def test_assign_skill_duplicate_integrity_error_is_returned_as_409(self):
        self.authenticate(self.admin_user)
        with patch("skills.views.PersonSkill.objects.create", side_effect=IntegrityError):
            response = self.client.post(
                self.get_person_skills_url(self.active_business_person.id),
                {"skill": self.accounting.id},
                format="json",
            )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(AuditEvent.objects.filter(action=AuditEvent.Action.SKILL_ASSIGNED).count(), 0)

    def test_skill_assignment_rolls_back_when_audit_write_fails(self):
        self.authenticate(self.admin_user)
        with patch("skills.views.record_audit_event", side_effect=RuntimeError("audit down")):
            response = self.client.post(
                self.get_person_skills_url(self.active_business_person.id),
                {"skill": self.project_management.id},
                format="json",
            )

        self.assertEqual(response.status_code, 500)
        self.assertFalse(
            PersonSkill.objects.filter(person=self.active_business_person, skill=self.project_management).exists()
        )
        self.assertEqual(AuditEvent.objects.filter(action=AuditEvent.Action.SKILL_ASSIGNED).count(), 0)

    def test_remove_skill_anonymous_receives_401(self):
        PersonSkill.objects.create(person=self.active_business_person, skill=self.accounting)
        response = self.client.delete(self.get_person_skill_detail_url(self.active_business_person.id, self.accounting.id))
        self.assertEqual(response.status_code, 401)

    def test_remove_skill_nonstaff_receives_403(self):
        PersonSkill.objects.create(person=self.active_business_person, skill=self.accounting)
        self.authenticate(self.non_staff_user)
        response = self.client.delete(self.get_person_skill_detail_url(self.active_business_person.id, self.accounting.id))
        self.assertEqual(response.status_code, 403)

    def test_remove_skill_viewer_receives_403(self):
        PersonSkill.objects.create(person=self.active_business_person, skill=self.accounting)
        self.authenticate(self.viewer_user)
        response = self.client.delete(self.get_person_skill_detail_url(self.active_business_person.id, self.accounting.id))
        self.assertEqual(response.status_code, 403)
        self.assertEqual(AuditEvent.objects.filter(action=AuditEvent.Action.SKILL_REMOVED).count(), 0)

    def test_remove_skill_manager_receives_204(self):
        PersonSkill.objects.create(person=self.active_business_person, skill=self.accounting)
        self.authenticate(self.manager_user)
        response = self.client.delete(self.get_person_skill_detail_url(self.active_business_person.id, self.accounting.id))
        self.assertEqual(response.status_code, 204)

    def test_remove_skill_admin_receives_204(self):
        PersonSkill.objects.create(person=self.active_business_person, skill=self.accounting)
        self.authenticate(self.admin_user)
        response = self.client.delete(self.get_person_skill_detail_url(self.active_business_person.id, self.accounting.id))
        self.assertEqual(response.status_code, 204)

    def test_remove_skill_existing_assignment_returns_204_and_deletes_person_skill_only(self):
        person_skill = PersonSkill.objects.create(person=self.active_business_person, skill=self.accounting)
        other_person = Person.objects.create(first_name="Other", last_name="Worker")
        PersonSkill.objects.create(person=other_person, skill=self.accounting)

        self.authenticate(self.admin_user)
        response = self.client.delete(self.get_person_skill_detail_url(self.active_business_person.id, self.accounting.id))

        self.assertEqual(response.status_code, 204)
        self.assertFalse(PersonSkill.objects.filter(person=self.active_business_person, skill=self.accounting).exists())
        self.assertTrue(Skill.objects.filter(pk=self.accounting.id).exists())
        self.assertTrue(PersonSkill.objects.filter(person=other_person, skill=self.accounting).exists())
        event = AuditEvent.objects.get(action=AuditEvent.Action.SKILL_REMOVED)
        self.assertEqual(event.actor_user, self.admin_user)
        self.assertEqual(event.entity_type, "PersonSkill")
        self.assertEqual(event.entity_id, str(person_skill.id))
        self.assertEqual(
            event.metadata,
            {"person_id": str(self.active_business_person.id), "skill_id": str(self.accounting.id)},
        )
        self.assertEqual(event.changes, {"assigned": {"from": True, "to": False}})

    def test_remove_skill_inactive_skill_assignment_can_be_removed(self):
        PersonSkill.objects.create(person=self.active_business_person, skill=self.inactive_skill)

        self.authenticate(self.admin_user)
        response = self.client.delete(self.get_person_skill_detail_url(self.active_business_person.id, self.inactive_skill.id))

        self.assertEqual(response.status_code, 204)
        self.assertFalse(PersonSkill.objects.filter(person=self.active_business_person, skill=self.inactive_skill).exists())
        event = AuditEvent.objects.get(action=AuditEvent.Action.SKILL_REMOVED)
        self.assertEqual(
            event.metadata,
            {"person_id": str(self.active_business_person.id), "skill_id": str(self.inactive_skill.id)},
        )

    def test_remove_skill_missing_assignment_returns_404(self):
        self.authenticate(self.admin_user)
        response = self.client.delete(self.get_person_skill_detail_url(self.active_business_person.id, self.accounting.id))
        self.assertEqual(response.status_code, 404)
        self.assertEqual(AuditEvent.objects.filter(action=AuditEvent.Action.SKILL_REMOVED).count(), 0)

    def test_remove_skill_nonexistent_skill_returns_404(self):
        self.authenticate(self.admin_user)
        response = self.client.delete(self.get_person_skill_detail_url(self.active_business_person.id, 999999))
        self.assertEqual(response.status_code, 404)

    def test_remove_skill_archived_business_person_returns_409(self):
        PersonSkill.objects.create(person=self.archived_business_person, skill=self.accounting)

        self.authenticate(self.admin_user)
        response = self.client.delete(self.get_person_skill_detail_url(self.archived_business_person.id, self.accounting.id))
        self.assertEqual(response.status_code, 409)
        self.assertEqual(AuditEvent.objects.filter(action=AuditEvent.Action.SKILL_REMOVED).count(), 0)

    def test_remove_skill_technical_person_returns_404(self):
        self.authenticate(self.admin_user)
        response = self.client.delete(self.get_person_skill_detail_url(self.technical_person.id, self.accounting.id))
        self.assertEqual(response.status_code, 404)

    def test_remove_skill_nonexistent_person_returns_404(self):
        self.authenticate(self.admin_user)
        response = self.client.delete(self.get_person_skill_detail_url(999999, self.accounting.id))
        self.assertEqual(response.status_code, 404)

    def test_skill_removal_rolls_back_when_audit_write_fails(self):
        PersonSkill.objects.create(person=self.active_business_person, skill=self.accounting)

        self.authenticate(self.admin_user)
        with patch("skills.views.record_audit_event", side_effect=RuntimeError("audit down")):
            response = self.client.delete(
                self.get_person_skill_detail_url(self.active_business_person.id, self.accounting.id)
            )

        self.assertEqual(response.status_code, 500)
        self.assertTrue(PersonSkill.objects.filter(person=self.active_business_person, skill=self.accounting).exists())
        self.assertEqual(AuditEvent.objects.filter(action=AuditEvent.Action.SKILL_REMOVED).count(), 0)

    def test_assignment_appears_in_overview_without_changing_membership_or_professional_profile(self):
        self.authenticate(self.admin_user)
        create_response = self.client.post(
            self.get_person_skills_url(self.active_business_person.id),
            {"skill": self.accounting.id},
            format="json",
        )
        self.assertEqual(create_response.status_code, 201)

        overview_response = self.client.get(self.get_person_overview_url(self.active_business_person.id))

        self.assertEqual(overview_response.status_code, 200)
        self.assertEqual(
            overview_response.data["skills"],
            [{"id": self.accounting.id, "name": "Accounting", "slug": "accounting"}],
        )
        self.assertEqual(overview_response.data["membership"]["id"], self.membership.id)
        self.assertEqual(overview_response.data["professional_profile"]["id"], self.professional_profile.id)

    def test_removal_disappears_from_overview_without_changing_membership_or_professional_profile(self):
        PersonSkill.objects.create(person=self.active_business_person, skill=self.accounting)

        self.authenticate(self.admin_user)
        delete_response = self.client.delete(
            self.get_person_skill_detail_url(self.active_business_person.id, self.accounting.id)
        )
        self.assertEqual(delete_response.status_code, 204)

        overview_response = self.client.get(self.get_person_overview_url(self.active_business_person.id))

        self.assertEqual(overview_response.status_code, 200)
        self.assertEqual(overview_response.data["skills"], [])
        self.assertEqual(overview_response.data["membership"]["id"], self.membership.id)
        self.assertEqual(overview_response.data["professional_profile"]["id"], self.professional_profile.id)
