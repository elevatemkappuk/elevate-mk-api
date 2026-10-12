from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from django.db import close_old_connections, connection, transaction
from django.test import SimpleTestCase, TransactionTestCase
from django.test.client import RequestFactory

from community.locking import acquire_community_join_email_lock, community_join_email_lock_key
from community.services import submit_community_join
from community.models import CommunityAccountInvitation
from memberships.models import Membership
from notifications.models import TransactionalEmailJob
from people.models import Person
from professional_profiles.models import Industry, ProfessionalProfile
from people.services import normalize_email


class CommunityJoinLockKeyTests(SimpleTestCase):
    def test_key_is_stable_for_the_same_canonical_email(self):
        self.assertEqual(
            community_join_email_lock_key(normalize_email(" Member@Example.COM ")),
            community_join_email_lock_key(normalize_email("member@example.com")),
        )

    def test_different_emails_have_different_keys_in_representative_cases(self):
        self.assertNotEqual(
            community_join_email_lock_key("one@example.com"),
            community_join_email_lock_key("two@example.com"),
        )


class CommunityJoinConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.industry, _ = Industry.objects.get_or_create(
            slug="technology",
            defaults={"name": "Technology"},
        )
        self.start_barrier = Barrier(2)

    def payload(self):
        return {
            "first_name": "Concurrent",
            "last_name": "Member",
            "gender": Person.Gender.FEMALE,
            "age_range": Person.AgeRange.AGE_30_34,
            "email": "CONCURRENT@example.com",
            "mobile": "",
            "phone_region": "",
            "location": "Milton Keynes",
            "industry": self.industry.slug,
            "job_title": "Software Engineer",
            "linkedin_url": "",
            "email_marketing_opt_in": False,
        }

    def submit(self, idempotency_key, *, synchronize=True):
        close_old_connections()
        try:
            if synchronize:
                self.start_barrier.wait(timeout=10)
            request = RequestFactory().post("/api/v1/community/join/", data={}, content_type="application/json")
            with transaction.atomic():
                result = submit_community_join(
                    data=self.payload(),
                    request=request,
                    idempotency_key=idempotency_key,
                )
            return result.replayed
        finally:
            close_old_connections()

    def test_concurrent_same_email_creates_one_complete_join_lifecycle(self):
        if connection.vendor != "postgresql":
            self.skipTest("PostgreSQL advisory-lock concurrency is not equivalent on this database.")

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(self.submit, "concurrent-join-a"),
                executor.submit(self.submit, "concurrent-join-b"),
            ]
            outcomes = [future.result(timeout=30) for future in futures]

        self.assertEqual(outcomes, [False, False])
        person = Person.objects.get(primary_email="concurrent@example.com")
        self.assertEqual(Person.objects.count(), 1)
        self.assertEqual(Person.objects.filter(primary_email="concurrent@example.com").count(), 1)
        self.assertEqual(Membership.objects.filter(person=person).count(), 1)
        self.assertEqual(ProfessionalProfile.objects.filter(person=person).count(), 1)
        self.assertEqual(
            CommunityAccountInvitation.objects.filter(
                person=person,
                used_at__isnull=True,
                revoked_at__isnull=True,
                superseded_at__isnull=True,
            ).count(),
            1,
        )
        self.assertEqual(TransactionalEmailJob.objects.filter(invitation__person=person).count(), 1)

    def test_transaction_rollback_releases_email_lock_for_a_later_join(self):
        if connection.vendor != "postgresql":
            self.skipTest("PostgreSQL advisory-lock release is not equivalent on this database.")

        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                acquire_community_join_email_lock("concurrent@example.com")
                raise RuntimeError("rollback lock test")

        self.submit("rollback-follow-up", synchronize=False)
        self.assertEqual(Person.objects.filter(primary_email="concurrent@example.com").count(), 1)
