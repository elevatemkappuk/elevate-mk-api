from datetime import timedelta

from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.db import connection
from django.conf import settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from audit.models import AuditEvent
from community.models import CommunityProfile
from interests.models import Interest, PersonInterest
from memberships.models import Membership
from people.models import Person
from professional_profiles.models import Industry, ProfessionalProfile
from skills.models import PersonSkill, Skill
from community.views import CommunityDirectoryDetailView, CommunityDirectoryListView


class CommunityDirectoryApiTests(TestCase):
    list_url = "/api/v1/community/directory/"

    def setUp(self):
        self.client = APIClient()
        self.industry = Industry.objects.get(slug="technology")
        self.skill = Skill.objects.create(name="Strategy", slug="directory-strategy")
        self.interest = Interest.objects.create(name="Networking", slug="directory-networking")

        self.viewer = self.create_member("Viewer", "Member", "viewer@example.com")
        self.client.force_authenticate(user=self.viewer)
        self.target = self.create_member("Ada", "Lovelace", "ada@example.com", visible=True)
        ProfessionalProfile.objects.create(
            person=self.target.person,
            job_title="Engineer",
            company="Elevate MK",
            industry=self.industry,
            career_stage=ProfessionalProfile.CareerStage.SENIOR,
            linkedin_url="https://www.linkedin.com/in/ada",
        )
        PersonSkill.objects.create(person=self.target.person, skill=self.skill)
        PersonInterest.objects.create(person=self.target.person, interest=self.interest)
        profile = self.target.person.community_profile
        profile.bio = "Building useful things."
        profile.email_visible = True
        profile.mobile_visible = True
        profile.person.mobile = "+447700900123"
        profile.person.save(update_fields=["mobile"])
        profile.save(update_fields=["bio", "email_visible", "mobile_visible"])

    def create_member(self, first_name, last_name, email, *, visible=False, record_type=Person.RecordType.BUSINESS):
        person = Person.objects.create(
            first_name=first_name,
            last_name=last_name,
            primary_email=email,
            record_type=record_type,
        )
        Membership.objects.create(
            person=person,
            status=Membership.Status.ACTIVE,
            joined_at=timezone.localdate() - timedelta(days=1),
            membership_source=Membership.Source.COMMUNITY_PLATFORM,
        )
        user = User.objects.create_user(email=email, password="Strong-password-123!", person=person)
        CommunityProfile.objects.create(person=person, directory_visible=visible)
        return user

    def test_authentication_and_eligibility_are_required(self):
        self.client.force_authenticate(user=None)
        self.assertEqual(self.client.get(self.list_url).status_code, 401)

        ineligible = self.create_member("Former", "Member", "former@example.com")
        ineligible.person.membership.status = Membership.Status.FORMER
        ineligible.person.membership.ended_at = timezone.localdate()
        ineligible.person.membership.save(update_fields=["status", "ended_at"])
        self.client.force_authenticate(user=ineligible)
        self.assertEqual(self.client.get(self.list_url).status_code, 403)

    def test_list_has_compact_allowlisted_projection_and_query_efficiency(self):
        before_profiles = CommunityProfile.objects.count()
        before_events = AuditEvent.objects.count()
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(self.list_url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
        result = response.data["results"][0]
        self.assertEqual(result["first_name"], "Ada")
        self.assertEqual(result["professional"]["industry"], {"slug": "technology", "label": "Technology"})
        self.assertEqual(result["skills"], [{"slug": self.skill.slug, "label": self.skill.name}])
        self.assertEqual(result["interests"], [{"slug": self.interest.slug, "label": self.interest.name}])
        self.assertNotIn("bio", result)
        self.assertNotIn("contact", result)
        self.assertNotIn("email", result)
        self.assertNotIn("mobile", result)
        self.assertNotIn("asset_namespace_id", result)
        self.assertNotIn("person_id", result)
        self.assertLessEqual(len(queries), 6)
        self.assertEqual(CommunityProfile.objects.count(), before_profiles)
        self.assertEqual(AuditEvent.objects.count(), before_events)

    def test_detail_projects_contact_independently_and_excludes_internal_data(self):
        directory_id = self.target.person.community_profile.directory_id
        response = self.client.get(f"{self.list_url}{directory_id}/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["directory_id"], str(directory_id))
        self.assertEqual(response.data["bio"], "Building useful things.")
        self.assertEqual(response.data["contact"], {"email": "ada@example.com", "mobile": "+447700900123"})
        for field in ("id", "person_id", "user_id", "membership", "asset_namespace_id", "gender", "age_range", "tags", "audit"):
            self.assertNotIn(field, response.data)

        profile = self.target.person.community_profile
        profile.email_visible = False
        profile.mobile_visible = True
        profile.save(update_fields=["email_visible"])
        response = self.client.get(f"{self.list_url}{directory_id}/")
        self.assertEqual(response.data["contact"], {"email": None, "mobile": "+447700900123"})

    def test_hidden_unknown_archived_former_and_non_business_targets_are_not_found(self):
        hidden = self.create_member("Hidden", "Member", "hidden@example.com", visible=False)
        hidden_id = hidden.person.community_profile.directory_id
        self.assertEqual(self.client.get(f"{self.list_url}{hidden_id}/").status_code, 404)
        self.assertEqual(self.client.get(f"{self.list_url}00000000-0000-0000-0000-000000000000/").status_code, 404)

        archived = self.create_member("Archived", "Member", "archived@example.com", visible=True)
        archived.person.archived_at = timezone.now()
        archived.person.save(update_fields=["archived_at"])
        self.assertEqual(self.client.get(f"{self.list_url}{archived.person.community_profile.directory_id}/").status_code, 404)

        former = self.create_member("Former", "Member", "former-target@example.com", visible=True)
        former.person.membership.status = Membership.Status.FORMER
        former.person.membership.ended_at = timezone.localdate()
        former.person.membership.save(update_fields=["status", "ended_at"])
        self.assertEqual(self.client.get(f"{self.list_url}{former.person.community_profile.directory_id}/").status_code, 404)

        technical = self.create_member(
            "Technical", "Account", "technical-target@example.com", visible=True,
            record_type=Person.RecordType.TECHNICAL,
        )
        self.assertEqual(self.client.get(f"{self.list_url}{technical.person.community_profile.directory_id}/").status_code, 404)

        unlinked = Person.objects.create(first_name="Unlinked", last_name="Member")
        Membership.objects.create(
            person=unlinked,
            status=Membership.Status.ACTIVE,
            joined_at=timezone.localdate(),
            membership_source=Membership.Source.COMMUNITY_PLATFORM,
        )
        unlinked_profile = CommunityProfile.objects.create(person=unlinked, directory_visible=True)
        self.assertEqual(self.client.get(f"{self.list_url}{unlinked_profile.directory_id}/").status_code, 404)

    def test_search_filters_pagination_and_invalid_taxonomy(self):
        second = self.create_member("Grace", "Hopper", "grace@example.com", visible=True)
        ProfessionalProfile.objects.create(person=second.person, industry=self.industry)
        response = self.client.get(self.list_url, {"q": "  ADA  ", "industry": "technology", "skill": self.skill.slug, "interest": self.interest.slug})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)

        response = self.client.get(self.list_url, {"q": "hopper"})
        self.assertEqual(response.data["count"], 1)
        response = self.client.get(self.list_url, {"q": ""})
        self.assertEqual(response.data["count"], 2)
        response = self.client.get(self.list_url, {"page_size": 1})
        self.assertEqual(len(response.data["results"]), 1)
        response = self.client.get(self.list_url, {"page_size": 101})
        self.assertEqual(response.status_code, 400)
        response = self.client.get(self.list_url, {"industry": "does-not-exist"})
        self.assertEqual(response.status_code, 400)

    def test_directory_uses_configurable_scrape_protection_scope(self):
        self.assertEqual(CommunityDirectoryListView.throttle_scope, "community_directory")
        self.assertEqual(CommunityDirectoryDetailView.throttle_scope, "community_directory")
        self.assertEqual(settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]["community_directory"], "60/hour")
