from unittest import mock

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from audit.models import AuditEvent
from community.models import JoinSubmissionReceipt
from memberships.models import Membership
from people.models import Person
from professional_profiles.models import Industry, ProfessionalProfile


class CommunityJoinApiTests(TestCase):
    join_url = "/api/v1/community/join/"
    industries_url = "/api/v1/community/industries/"

    def setUp(self):
        self.client = APIClient()
        cache.clear()
        self.industry = Industry.objects.get(slug="technology")

    def payload(self, **overrides):
        result = {
            "first_name": "Amina",
            "last_name": "Zulu",
            "gender": Person.Gender.FEMALE,
            "age_range": Person.AgeRange.AGE_30_34,
            "email": "amina@example.com",
            "mobile": "07123 456 789",
            "phone_region": "GB",
            "location": "Milton Keynes",
            "industry": self.industry.slug,
            "job_title": "Software Engineer",
            "linkedin_url": "https://www.linkedin.com/in/amina",
        }
        result.update(overrides)
        return result

    def test_public_industry_projection_contains_only_slug_and_label(self):
        response = self.client.get(self.industries_url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.data[0]), {"slug", "label"})
        self.assertIn({"slug": self.industry.slug, "label": self.industry.name}, response.data)

    def test_crm_industry_endpoint_remains_protected(self):
        response = self.client.get("/api/v1/industries/")

        self.assertEqual(response.status_code, 401)

    def test_new_join_creates_person_profile_membership_and_safe_response(self):
        response = self.client.post(self.join_url, self.payload(), format="json")

        self.assertEqual(response.status_code, 202)
        self.assertEqual(
            response.data,
            {"status": "accepted", "message": "Your membership submission has been received."},
        )
        person = Person.objects.get(primary_email="amina@example.com")
        self.assertEqual(person.record_type, Person.RecordType.BUSINESS)
        self.assertEqual(person.mobile, "+447123456789")
        self.assertEqual(person.professional_profile.industry, self.industry)
        self.assertEqual(person.professional_profile.job_title, "Software Engineer")
        self.assertEqual(person.membership.status, Membership.Status.ACTIVE)
        self.assertEqual(person.membership.membership_source, Membership.Source.COMMUNITY_PLATFORM)
        self.assertEqual(person.membership.joined_at, timezone.localdate())
        self.assertIsNone(person.membership.ended_at)
        self.assertTrue(
            AuditEvent.objects.filter(
                action=AuditEvent.Action.PERSON_CREATED,
                metadata__source="COMMUNITY_JOIN",
            ).exists()
        )

    def test_exact_email_reuses_active_person_and_fills_only_missing_values(self):
        person = Person.objects.create(
            first_name="Existing",
            last_name="Name",
            primary_email="amina@example.com",
            location="Existing location",
        )
        response = self.client.post(self.join_url, self.payload(), format="json")

        self.assertEqual(response.status_code, 202)
        person.refresh_from_db()
        self.assertEqual(person.first_name, "Existing")
        self.assertEqual(person.last_name, "Name")
        self.assertEqual(person.location, "Existing location")
        self.assertEqual(person.gender, Person.Gender.FEMALE)
        self.assertEqual(person.age_range, Person.AgeRange.AGE_30_34)
        self.assertEqual(person.membership.status, Membership.Status.ACTIVE)

    def test_cross_person_email_and_mobile_collision_is_generic_and_does_not_mutate(self):
        person_a = Person.objects.create(
            first_name="Email",
            last_name="Owner",
            primary_email="amina@example.com",
            location="A location",
        )
        person_b = Person.objects.create(
            first_name="Mobile",
            last_name="Owner",
            primary_email="other@example.com",
            mobile="+447123456789",
        )
        profile_a = ProfessionalProfile.objects.create(person=person_a, job_title="A title", industry=self.industry)
        profile_b = ProfessionalProfile.objects.create(person=person_b, job_title="B title", industry=self.industry)
        membership_a = Membership.objects.create(
            person=person_a,
            status=Membership.Status.ACTIVE,
            joined_at=timezone.localdate(),
            membership_source=Membership.Source.STAFF,
        )
        membership_b = Membership.objects.create(
            person=person_b,
            status=Membership.Status.ACTIVE,
            joined_at=timezone.localdate(),
            membership_source=Membership.Source.WEBSITE_FORM,
        )

        response = self.client.post(
            self.join_url,
            self.payload(email="AMINA@EXAMPLE.COM", mobile="07123 456 789"),
            format="json",
        )

        self.assertEqual(response.status_code, 409)
        person_a.refresh_from_db()
        person_b.refresh_from_db()
        profile_a.refresh_from_db()
        profile_b.refresh_from_db()
        membership_a.refresh_from_db()
        membership_b.refresh_from_db()
        self.assertEqual(person_a.location, "A location")
        self.assertEqual(person_b.mobile, "+447123456789")
        self.assertEqual(profile_a.job_title, "A title")
        self.assertEqual(profile_b.job_title, "B title")
        self.assertEqual(membership_a.membership_source, Membership.Source.STAFF)
        self.assertEqual(membership_b.membership_source, Membership.Source.WEBSITE_FORM)

    def test_same_person_normalized_mobile_is_safely_reused(self):
        person = Person.objects.create(
            first_name="Existing",
            last_name="Mobile",
            primary_email="amina@example.com",
            mobile="07123 456 789",
        )

        response = self.client.post(
            self.join_url,
            self.payload(mobile="07123456789"),
            format="json",
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(Person.objects.filter(primary_email="amina@example.com").count(), 1)
        person.refresh_from_db()
        self.assertEqual(person.mobile, "07123 456 789")
        self.assertEqual(person.membership.status, Membership.Status.ACTIVE)

    def test_existing_active_member_repeat_is_accepted_without_duplicate_membership(self):
        person = Person.objects.create(
            first_name="Existing",
            last_name="Member",
            primary_email="amina@example.com",
            mobile="+447123456789",
            location="Existing location",
        )
        profile = ProfessionalProfile.objects.create(
            person=person,
            job_title="Existing title",
            industry=Industry.objects.create(name="Finance", slug="finance"),
            linkedin_url="https://www.linkedin.com/in/existing",
        )
        joined_at = timezone.localdate()
        membership = Membership.objects.create(
            person=person,
            status=Membership.Status.ACTIVE,
            joined_at=joined_at,
            membership_source=Membership.Source.STAFF,
        )
        response = self.client.post(self.join_url, self.payload(), format="json")

        self.assertEqual(response.status_code, 202)
        self.assertEqual(
            response.data,
            {"status": "accepted", "message": "Your membership submission has been received."},
        )
        self.assertEqual(Membership.objects.filter(person=person).count(), 1)
        person.refresh_from_db()
        membership.refresh_from_db()
        profile.refresh_from_db()
        self.assertEqual(membership.status, Membership.Status.ACTIVE)
        self.assertEqual(membership.joined_at, joined_at)
        self.assertEqual(membership.membership_source, Membership.Source.STAFF)
        self.assertEqual(person.first_name, "Existing")
        self.assertEqual(person.last_name, "Member")
        self.assertEqual(person.mobile, "+447123456789")
        self.assertEqual(person.location, "Existing location")
        self.assertEqual(profile.job_title, "Existing title")
        self.assertEqual(profile.industry.slug, "finance")
        self.assertEqual(profile.linkedin_url, "https://www.linkedin.com/in/existing")

    def test_existing_active_member_fills_only_missing_values(self):
        person = Person.objects.create(
            first_name="Existing",
            last_name="Member",
            primary_email="amina@example.com",
            location="Existing location",
        )
        profile = ProfessionalProfile.objects.create(person=person, job_title="Existing title")
        membership = Membership.objects.create(
            person=person,
            status=Membership.Status.ACTIVE,
            joined_at=timezone.localdate(),
            membership_source=Membership.Source.STAFF,
        )

        response = self.client.post(self.join_url, self.payload(), format="json")

        self.assertEqual(response.status_code, 202)
        person.refresh_from_db()
        profile.refresh_from_db()
        membership.refresh_from_db()
        self.assertEqual(person.location, "Existing location")
        self.assertEqual(person.gender, Person.Gender.FEMALE)
        self.assertEqual(person.age_range, Person.AgeRange.AGE_30_34)
        self.assertEqual(profile.job_title, "Existing title")
        self.assertEqual(profile.industry, self.industry)
        self.assertEqual(profile.linkedin_url, "https://www.linkedin.com/in/amina")
        self.assertEqual(membership.status, Membership.Status.ACTIVE)

    def test_name_only_and_mobile_only_evidence_do_not_match(self):
        Person.objects.create(first_name="Amina", last_name="Zulu", primary_email="other@example.com")
        Person.objects.create(first_name="Mobile", last_name="Owner", primary_email="mobile@example.com", mobile="+447123456789")

        name_response = self.client.post(
            self.join_url,
            self.payload(email="new@example.com", mobile="", phone_region=""),
            format="json",
        )
        mobile_response = self.client.post(
            self.join_url,
            self.payload(email="new-mobile@example.com", mobile="07123456789"),
            format="json",
        )

        self.assertEqual(name_response.status_code, 202)
        self.assertEqual(mobile_response.status_code, 409)
        self.assertEqual(Person.objects.filter(primary_email="new@example.com").count(), 1)
        self.assertFalse(Person.objects.filter(primary_email="new-mobile@example.com").exists())

    def test_contradictory_mobile_evidence_does_not_mutate(self):
        Person.objects.create(
            first_name="Existing",
            last_name="Person",
            primary_email="amina@example.com",
            mobile="07123111111",
        )

        response = self.client.post(self.join_url, self.payload(mobile="07123222222"), format="json")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(Person.objects.filter(primary_email="amina@example.com").count(), 1)
        self.assertFalse(Membership.objects.filter(person__primary_email="amina@example.com").exists())

    def test_archived_former_and_ambiguous_or_contradictory_identity_do_not_mutate(self):
        archived = Person.objects.create(
            first_name="Archived", last_name="Person", primary_email="archived@example.com", archived_at=timezone.now()
        )
        former = Person.objects.create(first_name="Former", last_name="Person", primary_email="former@example.com")
        Membership.objects.create(
            person=former,
            status=Membership.Status.FORMER,
            joined_at=timezone.localdate(),
            ended_at=timezone.localdate(),
            membership_source=Membership.Source.STAFF,
        )
        Person.objects.create(first_name="One", last_name="Email", primary_email="ambiguous@example.com")
        Person.objects.create(first_name="Two", last_name="Email", primary_email="ambiguous@example.com")

        for email in ("archived@example.com", "former@example.com", "ambiguous@example.com"):
            response = self.client.post(self.join_url, self.payload(email=email), format="json")
            self.assertEqual(response.status_code, 409)
        self.assertFalse(hasattr(archived, "membership"))
        self.assertEqual(Membership.objects.filter(person=former).count(), 1)

    def test_existing_populated_profile_fields_are_not_overwritten(self):
        person = Person.objects.create(first_name="Existing", last_name="Profile", primary_email="amina@example.com")
        profile = ProfessionalProfile.objects.create(
            person=person,
            job_title="Existing title",
            industry=Industry.objects.create(name="Finance", slug="finance"),
            linkedin_url="https://www.linkedin.com/in/existing",
        )

        response = self.client.post(self.join_url, self.payload(), format="json")

        self.assertEqual(response.status_code, 202)
        profile.refresh_from_db()
        self.assertEqual(profile.job_title, "Existing title")
        self.assertEqual(profile.industry.slug, "finance")
        self.assertEqual(profile.linkedin_url, "https://www.linkedin.com/in/existing")

    def test_invalid_or_inactive_industry_is_rejected(self):
        inactive = Industry.objects.create(name="Inactive", slug="inactive", is_active=False)
        for value in ("missing", inactive.slug):
            response = self.client.post(self.join_url, self.payload(industry=value), format="json")
            self.assertEqual(response.status_code, 400)
        self.assertFalse(Person.objects.filter(primary_email="amina@example.com").exists())

    def test_mobile_requires_phone_region_and_invalid_phone_does_not_mutate(self):
        missing_region = self.client.post(
            self.join_url,
            self.payload(phone_region=""),
            format="json",
        )
        invalid_number = self.client.post(
            self.join_url,
            self.payload(mobile="07123", phone_region="GB", email="invalid@example.com"),
            format="json",
        )
        mismatch = self.client.post(
            self.join_url,
            self.payload(mobile="+233241234567", phone_region="GB", email="mismatch@example.com"),
            format="json",
        )

        self.assertEqual(missing_region.status_code, 400)
        self.assertEqual(invalid_number.status_code, 400)
        self.assertEqual(mismatch.status_code, 400)
        self.assertFalse(Person.objects.filter(primary_email__in=("invalid@example.com", "mismatch@example.com")).exists())

    def test_phone_region_does_not_populate_person_location(self):
        response = self.client.post(self.join_url, self.payload(), format="json")

        self.assertEqual(response.status_code, 202)
        person = Person.objects.get(primary_email="amina@example.com")
        self.assertEqual(person.location, "Milton Keynes")

    def test_unknown_and_invalid_fields_are_rejected(self):
        response = self.client.post(
            self.join_url,
            self.payload(status="ACTIVE", joined_at="2026-09-30"),
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("status", response.data)
        self.assertIn("joined_at", response.data)

    def test_transaction_rolls_back_when_audit_fails(self):
        with mock.patch("community.services.record_audit_event", side_effect=RuntimeError("audit down")):
            with self.assertRaises(RuntimeError):
                self.client.post(self.join_url, self.payload(), format="json")

        self.assertFalse(Person.objects.filter(primary_email="amina@example.com").exists())
        self.assertFalse(AuditEvent.objects.filter(metadata__source="COMMUNITY_JOIN").exists())

    def test_idempotency_replays_safe_result_and_rejects_different_payload(self):
        headers = {"HTTP_IDEMPOTENCY_KEY": "join-retry-1"}
        first = self.client.post(self.join_url, self.payload(), format="json", **headers)
        second = self.client.post(self.join_url, self.payload(), format="json", **headers)
        different = self.client.post(
            self.join_url,
            self.payload(first_name="Different"),
            format="json",
            **headers,
        )

        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 202)
        self.assertEqual(second.data, first.data)
        self.assertEqual(different.status_code, 409)
        self.assertEqual(Person.objects.filter(primary_email="amina@example.com").count(), 1)

    def test_idempotency_email_normalization_replays_same_result(self):
        headers = {"HTTP_IDEMPOTENCY_KEY": "join-email-normalization"}
        first = self.client.post(self.join_url, self.payload(email="AMINA@EXAMPLE.COM"), format="json", **headers)
        second = self.client.post(self.join_url, self.payload(email="amina@example.com"), format="json", **headers)

        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 202)
        self.assertEqual(second.data, first.data)
        self.assertEqual(Person.objects.filter(primary_email="amina@example.com").count(), 1)

    def test_idempotency_mobile_normalization_replays_same_result(self):
        headers = {"HTTP_IDEMPOTENCY_KEY": "join-mobile-normalization"}
        first = self.client.post(self.join_url, self.payload(mobile="07123 456 789"), format="json", **headers)
        second = self.client.post(self.join_url, self.payload(mobile="07123456789"), format="json", **headers)

        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 202)
        self.assertEqual(second.data, first.data)
        self.assertEqual(Person.objects.filter(primary_email="amina@example.com").count(), 1)

    def test_idempotency_receipt_contains_only_digest_metadata(self):
        headers = {"HTTP_IDEMPOTENCY_KEY": "join-receipt-privacy"}
        response = self.client.post(self.join_url, self.payload(), format="json", **headers)

        self.assertEqual(response.status_code, 202)
        receipt = JoinSubmissionReceipt.objects.get()
        self.assertEqual(
            {field.name for field in receipt._meta.fields},
            {"id", "key_hash", "request_digest", "status", "created_at", "updated_at"},
        )
        self.assertNotIn("amina@example.com", receipt.request_digest)
        self.assertNotIn("07123", receipt.request_digest)

    def test_invalid_phone_does_not_create_an_accepted_idempotency_receipt(self):
        response = self.client.post(
            self.join_url,
            self.payload(mobile="+233241234567", phone_region="GB"),
            format="json",
            HTTP_IDEMPOTENCY_KEY="join-invalid-phone",
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(JoinSubmissionReceipt.objects.exists())

    def test_join_is_csrf_protected(self):
        csrf_client = APIClient(enforce_csrf_checks=True)
        response = csrf_client.post(self.join_url, self.payload(), format="json")

        self.assertEqual(response.status_code, 403)
        self.assertFalse(Person.objects.filter(primary_email="amina@example.com").exists())

    def test_join_has_dedicated_throttle(self):
        responses = [
            self.client.post(
                self.join_url,
                self.payload(email=f"member-{index}@example.com", mobile="", phone_region=""),
                format="json",
            )
            for index in range(11)
        ]

        self.assertTrue(all(response.status_code == 202 for response in responses[:10]))
        self.assertEqual(responses[10].status_code, 429)
