from datetime import timedelta
from unittest import mock

from django.core.exceptions import ValidationError
from django.core.cache import cache
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from community.models import CommunityProfile
from community.services import get_or_create_community_profile
from memberships.models import Membership
from people.models import Person
from professional_profiles.models import Industry, ProfessionalProfile
from accounts.models import User
from skills.models import PersonSkill, Skill
from interests.models import Interest, PersonInterest


class CommunityProfileFoundationTests(TestCase):
    join_url = "/api/v1/community/join/"

    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.industry = Industry.objects.get(slug="technology")

    def payload(self, **overrides):
        payload = {
            "first_name": "Amina",
            "last_name": "Zulu",
            "gender": Person.Gender.FEMALE,
            "age_range": Person.AgeRange.AGE_30_34,
            "email": "profile@example.com",
            "mobile": "",
            "phone_region": "",
            "location": "Milton Keynes",
            "industry": self.industry.slug,
            "job_title": "Designer",
            "linkedin_url": "",
        }
        payload.update(overrides)
        return payload

    def test_model_defaults_and_review_required_semantics(self):
        person = Person.objects.create(first_name="Amina", last_name="Zulu")
        profile = CommunityProfile.objects.create(person=person)

        self.assertEqual(profile.bio, "")
        self.assertFalse(profile.person_preexisted_community)
        self.assertIsNone(profile.review_acknowledged_at)
        self.assertFalse(profile.review_required)

        profile.person_preexisted_community = True
        profile.save(update_fields=["person_preexisted_community", "updated_at"])
        self.assertTrue(profile.review_required)
        profile.review_acknowledged_at = timezone.now()
        profile.save(update_fields=["review_acknowledged_at", "updated_at"])
        self.assertFalse(profile.review_required)

    def test_bio_boundary_is_400_characters(self):
        person = Person.objects.create(first_name="Amina", last_name="Zulu")
        CommunityProfile(person=person, bio="x" * 400).full_clean()
        with self.assertRaises(ValidationError):
            CommunityProfile(person=person, bio="x" * 401).full_clean()

    def test_one_community_profile_per_person(self):
        person = Person.objects.create(first_name="Amina", last_name="Zulu")
        CommunityProfile.objects.create(person=person)
        with self.assertRaises(Exception):
            CommunityProfile.objects.create(person=person)

    def test_new_join_creates_profile_with_false_provenance(self):
        response = self.client.post(self.join_url, self.payload(), format="json")

        self.assertEqual(response.status_code, 202)
        person = Person.objects.get(primary_email="profile@example.com")
        profile = CommunityProfile.objects.get(person=person)
        self.assertFalse(profile.person_preexisted_community)

    def test_existing_person_join_creates_profile_with_true_provenance(self):
        person = Person.objects.create(
            first_name="Existing",
            last_name="Member",
            primary_email="profile@example.com",
        )

        response = self.client.post(self.join_url, self.payload(), format="json")

        self.assertEqual(response.status_code, 202)
        profile = CommunityProfile.objects.get(person=person)
        self.assertTrue(profile.person_preexisted_community)

    def test_existing_active_member_without_profile_gets_true_provenance(self):
        person = Person.objects.create(
            first_name="Existing",
            last_name="Member",
            primary_email="profile@example.com",
        )
        Membership.objects.create(
            person=person,
            status=Membership.Status.ACTIVE,
            joined_at=timezone.localdate(),
            membership_source=Membership.Source.STAFF,
        )

        response = self.client.post(self.join_url, self.payload(), format="json")

        self.assertEqual(response.status_code, 202)
        self.assertTrue(CommunityProfile.objects.get(person=person).person_preexisted_community)

    def test_rejoin_preserves_false_provenance(self):
        first = self.client.post(self.join_url, self.payload(), format="json", HTTP_IDEMPOTENCY_KEY="first")
        second = self.client.post(self.join_url, self.payload(), format="json", HTTP_IDEMPOTENCY_KEY="second")

        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 202)
        person = Person.objects.get(primary_email="profile@example.com")
        self.assertFalse(CommunityProfile.objects.get(person=person).person_preexisted_community)

    def test_idempotency_replay_preserves_provenance(self):
        first = self.client.post(self.join_url, self.payload(), format="json", HTTP_IDEMPOTENCY_KEY="replay")
        second = self.client.post(self.join_url, self.payload(), format="json", HTTP_IDEMPOTENCY_KEY="replay")

        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 202)
        person = Person.objects.get(primary_email="profile@example.com")
        self.assertFalse(CommunityProfile.objects.get(person=person).person_preexisted_community)

    def test_preexisting_provenance_remains_true_on_rejoin(self):
        person = Person.objects.create(
            first_name="Existing",
            last_name="Member",
            primary_email="profile@example.com",
        )
        profile = CommunityProfile.objects.create(person=person, person_preexisted_community=True)

        response = self.client.post(self.join_url, self.payload(), format="json", HTTP_IDEMPOTENCY_KEY="rejoin")

        self.assertEqual(response.status_code, 202)
        profile.refresh_from_db()
        self.assertTrue(profile.person_preexisted_community)

    def test_historical_lazy_creation_defaults_to_false(self):
        person = Person.objects.create(first_name="Historical", last_name="Member")
        Membership.objects.create(
            person=person,
            status=Membership.Status.ACTIVE,
            joined_at=timezone.localdate() - timedelta(days=10),
            membership_source=Membership.Source.COMMUNITY_PLATFORM,
        )
        User.objects.create_user(
            email=person.primary_email or "historical@example.com",
            password="Historical-password-123!",
            person=person,
        )

        profile = get_or_create_community_profile(person=person)

        self.assertFalse(profile.person_preexisted_community)
        self.assertFalse(profile.review_required)

    def test_failed_join_rolls_back_community_profile(self):
        with mock.patch("community.services._schedule_account_activation", side_effect=RuntimeError("delivery unavailable")):
            with self.assertRaises(RuntimeError):
                self.client.post(self.join_url, self.payload(), format="json")

        self.assertFalse(Person.objects.filter(primary_email="profile@example.com").exists())
        self.assertFalse(CommunityProfile.objects.exists())


class CommunityProfileApiTests(TestCase):
    profile_url = "/api/v1/community/profile/"
    options_url = "/api/v1/community/profile/options/"
    acknowledgement_url = "/api/v1/community/profile/review-acknowledgement/"

    def setUp(self):
        self.client = APIClient()
        self.industry = Industry.objects.get(slug="technology")
        self.person = Person.objects.create(
            first_name="Amina",
            last_name="Zulu",
            location="Milton Keynes",
            primary_email="private@example.com",
        )
        self.membership = Membership.objects.create(
            person=self.person,
            status=Membership.Status.ACTIVE,
            joined_at=timezone.localdate() - timedelta(days=30),
            membership_source=Membership.Source.COMMUNITY_PLATFORM,
        )
        self.user = User.objects.create_user(
            email="private@example.com",
            password="Strong-password-123!",
            person=self.person,
        )
        self.client.force_authenticate(user=self.user)

    def test_eligible_member_receives_composed_profile_and_completion(self):
        CommunityProfile.objects.create(person=self.person, bio="Community builder")
        ProfessionalProfile.objects.create(
            person=self.person,
            job_title="Designer",
            company="Elevate MK",
            industry=self.industry,
            career_stage=ProfessionalProfile.CareerStage.MID_CAREER,
            linkedin_url="https://www.linkedin.com/in/amina",
        )
        skill = Skill.objects.filter(is_active=True).first()
        interest = Interest.objects.filter(is_active=True).first()
        PersonSkill.objects.create(person=self.person, skill=skill)
        PersonInterest.objects.create(person=self.person, interest=interest)

        response = self.client.get(self.profile_url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["person"], {
            "first_name": "Amina",
            "last_name": "Zulu",
            "location": "Milton Keynes",
        })
        self.assertEqual(response.data["community"], {"bio": "Community builder", "review_required": False})
        self.assertEqual(response.data["professional"]["industry"], {
            "id": self.industry.id,
            "slug": self.industry.slug,
            "label": self.industry.name,
        })
        self.assertEqual(response.data["membership"], {
            "status": Membership.Status.ACTIVE,
            "joined_at": self.membership.joined_at.isoformat(),
        })
        self.assertEqual(response.data["completion"], {
            "name": True,
            "professional_details": True,
            "bio": True,
            "skills": True,
            "interests": True,
        })
        self.assertNotIn("email", response.data["person"])
        self.assertNotIn("mobile", response.data["person"])
        self.assertNotIn("person_preexisted_community", response.data["community"])
        self.assertNotIn("review_acknowledged_at", response.data["community"])

    def test_missing_optional_data_is_safe_and_profile_is_lazily_created(self):
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(self.profile_url)

        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(queries), 8)
        self.assertEqual(response.data["professional"], {
            "job_title": "",
            "company": "",
            "industry": None,
            "career_stage": None,
            "linkedin_url": "",
        })
        self.assertEqual(response.data["community"], {"bio": "", "review_required": False})
        self.assertEqual(response.data["completion"], {
            "name": True,
            "professional_details": False,
            "bio": False,
            "skills": False,
            "interests": False,
        })
        self.assertTrue(CommunityProfile.objects.filter(person=self.person).exists())

    def test_review_required_is_projected_without_internal_provenance(self):
        CommunityProfile.objects.create(person=self.person, person_preexisted_community=True)

        response = self.client.get(self.profile_url)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["community"]["review_required"])
        self.assertNotIn("person_preexisted_community", response.data["community"])
        self.assertNotIn("review_acknowledged_at", response.data["community"])

    def test_profile_is_derived_from_authenticated_person_not_a_supplied_id(self):
        other_person = Person.objects.create(first_name="Other", last_name="Person")

        response = self.client.get(f"{self.profile_url}?person_id={other_person.id}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["person"]["first_name"], "Amina")
        self.assertFalse(CommunityProfile.objects.filter(person=other_person).exists())

    def test_unauthenticated_and_ineligible_users_are_rejected(self):
        self.client.force_authenticate(user=None)
        self.assertEqual(self.client.get(self.profile_url).status_code, 401)

        former_person = Person.objects.create(first_name="Former", last_name="Member")
        former_user = User.objects.create_user(
            email="former@example.com",
            password="Strong-password-123!",
            person=former_person,
        )
        Membership.objects.create(
            person=former_person,
            status=Membership.Status.FORMER,
            joined_at=timezone.localdate() - timedelta(days=60),
            ended_at=timezone.localdate() - timedelta(days=1),
            membership_source=Membership.Source.COMMUNITY_PLATFORM,
        )
        self.client.force_authenticate(user=former_user)
        self.assertEqual(self.client.get(self.profile_url).status_code, 403)

    def test_archived_and_technical_people_are_rejected(self):
        archived_person = Person.objects.create(
            first_name="Archived",
            last_name="Member",
            archived_at=timezone.now(),
        )
        archived_user = User.objects.create_user(
            email="archived@example.com",
            password="Strong-password-123!",
            person=archived_person,
        )
        Membership.objects.create(
            person=archived_person,
            status=Membership.Status.ACTIVE,
            joined_at=timezone.localdate(),
            membership_source=Membership.Source.COMMUNITY_PLATFORM,
        )
        self.client.force_authenticate(user=archived_user)
        self.assertEqual(self.client.get(self.profile_url).status_code, 403)

        technical_person = Person.objects.create(
            first_name="Technical",
            last_name="Account",
            record_type=Person.RecordType.TECHNICAL,
        )
        technical_user = User.objects.create_user(
            email="technical@example.com",
            password="Strong-password-123!",
            person=technical_person,
        )
        Membership.objects.create(
            person=technical_person,
            status=Membership.Status.ACTIVE,
            joined_at=timezone.localdate(),
            membership_source=Membership.Source.COMMUNITY_PLATFORM,
        )
        self.client.force_authenticate(user=technical_user)
        self.assertEqual(self.client.get(self.profile_url).status_code, 403)

    def test_patch_updates_canonical_profile_and_returns_fresh_completion(self):
        response = self.client.patch(
            self.profile_url,
            {
                "person": {"first_name": "  AMINA  ", "last_name": "McDonald", "location": "  Bletchley  Park "},
                "community": {"bio": "  Building better connections.  "},
                "professional": {
                    "job_title": "Product Designer",
                    "company": "Elevate MK",
                    "industry": self.industry.slug,
                    "career_stage": ProfessionalProfile.CareerStage.MID_CAREER,
                    "linkedin_url": "https://www.linkedin.com/in/amina",
                },
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["person"]["first_name"], "Amina")
        self.assertEqual(response.data["person"]["location"], "Bletchley Park")
        self.assertEqual(response.data["community"]["bio"], "Building better connections.")
        self.assertTrue(response.data["completion"]["professional_details"])
        self.person.refresh_from_db()
        self.assertEqual(self.person.primary_email, "private@example.com")
        self.assertEqual(self.person.mobile, "")
        professional = ProfessionalProfile.objects.get(person=self.person)
        self.assertEqual(professional.job_title, "Product Designer")

    def test_patch_rejects_protected_fields_and_does_not_change_person(self):
        response = self.client.patch(
            self.profile_url,
            {"person": {"primary_email": "changed@example.com", "mobile": "+447700900123"}},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.person.refresh_from_db()
        self.assertEqual(self.person.primary_email, "private@example.com")
        self.assertEqual(self.person.mobile, "")

    def test_patch_can_replace_and_clear_skills_and_interests(self):
        skill = Skill.objects.filter(is_active=True).first()
        interest = Interest.objects.filter(is_active=True).first()
        response = self.client.patch(
            self.profile_url,
            {"skills": [skill.slug], "interests": [interest.slug]},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["skills"][0]["slug"], skill.slug)
        self.assertTrue(response.data["completion"]["skills"])
        self.assertTrue(response.data["completion"]["interests"])

        response = self.client.patch(self.profile_url, {"skills": [], "interests": []}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["completion"]["skills"])
        self.assertFalse(response.data["completion"]["interests"])
        self.assertFalse(PersonSkill.objects.filter(person=self.person).exists())
        self.assertFalse(PersonInterest.objects.filter(person=self.person).exists())

    def test_patch_rejects_inactive_taxonomy_and_unknown_fields(self):
        inactive = Industry.objects.create(name="Inactive", slug="inactive-profile-test", is_active=False)
        response = self.client.patch(self.profile_url, {"professional": {"industry": inactive.slug}}, format="json")
        self.assertEqual(response.status_code, 400)
        response = self.client.patch(self.profile_url, {"email": "private@example.com"}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_patch_updates_existing_professional_profile_and_validates_name_and_stage(self):
        ProfessionalProfile.objects.create(person=self.person, job_title="Old title", industry=self.industry)
        response = self.client.patch(
            self.profile_url,
            {"person": {"first_name": ""}, "professional": {"career_stage": "NOT_A_STAGE"}},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.person.refresh_from_db()
        self.assertEqual(self.person.first_name, "Amina")
        self.assertEqual(ProfessionalProfile.objects.get(person=self.person).job_title, "Old title")

        response = self.client.patch(
            self.profile_url,
            {"professional": {"job_title": "Updated title", "company": "New company"}},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        professional = ProfessionalProfile.objects.get(person=self.person)
        self.assertEqual(professional.job_title, "Updated title")
        self.assertEqual(professional.company, "New company")

    def test_invalid_audited_update_rolls_back_all_profile_changes(self):
        with mock.patch("community.services.record_audit_event", side_effect=RuntimeError("audit unavailable")):
            with self.assertRaises(RuntimeError):
                self.client.patch(self.profile_url, {"person": {"location": "New location"}}, format="json")
        self.person.refresh_from_db()
        self.assertEqual(self.person.location, "Milton Keynes")

    def test_options_are_community_safe_and_active_only(self):
        Industry.objects.create(name="Internal", slug="internal-options-test", is_active=False)
        response = self.client.get(self.options_url)
        self.assertEqual(response.status_code, 200)
        self.assertIn({"slug": self.industry.slug, "label": self.industry.name}, response.data["industries"])
        self.assertNotIn("id", response.data["industries"][0])
        self.assertNotIn({"slug": "internal-options-test", "label": "Internal"}, response.data["industries"])
        self.assertTrue(response.data["career_stages"])

    def test_review_acknowledgement_is_idempotent_and_preserves_provenance(self):
        profile = CommunityProfile.objects.create(person=self.person, person_preexisted_community=True)
        first = self.client.post(self.acknowledgement_url, {}, format="json")
        profile.refresh_from_db()
        acknowledged_at = profile.review_acknowledged_at
        second = self.client.post(self.acknowledgement_url, {}, format="json")
        profile.refresh_from_db()
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertFalse(first.data["review_required"])
        self.assertFalse(second.data["review_required"])
        self.assertEqual(profile.review_acknowledged_at, acknowledged_at)
        self.assertTrue(profile.person_preexisted_community)

    def test_profile_mutations_require_community_eligibility(self):
        self.client.force_authenticate(user=None)
        self.assertEqual(self.client.patch(self.profile_url, {"community": {"bio": "x"}}, format="json").status_code, 401)

        self.membership.status = Membership.Status.FORMER
        self.membership.save(update_fields=["status"])
        self.client.force_authenticate(user=self.user)
        self.assertEqual(self.client.patch(self.profile_url, {"community": {"bio": "x"}}, format="json").status_code, 403)
