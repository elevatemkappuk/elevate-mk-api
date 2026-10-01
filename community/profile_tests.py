from datetime import timedelta
from unittest import mock

from django.core.exceptions import ValidationError
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from community.models import CommunityProfile
from community.services import get_or_create_community_profile
from memberships.models import Membership
from people.models import Person
from professional_profiles.models import Industry
from accounts.models import User


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
