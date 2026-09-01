from unittest import mock

from django.test import TestCase
from django.utils import timezone

from audit.models import AuditEvent
from data_imports.models import ImportBatch, ImportRecord
from data_imports.services.membership_form_import import (
    ImportBatchNotImportable,
    ImportBatchPreflightError,
    import_membership_form_batch,
)
from memberships.models import Membership
from people.models import Person
from professional_profiles.models import Industry, ProfessionalProfile


class MembershipFormImportServiceTests(TestCase):
    def setUp(self):
        self.batch = ImportBatch.objects.create(
            source_type=ImportBatch.SourceType.MEMBERSHIP_FORM,
            source_filename="membership.xlsx",
            source_fingerprint="a" * 64,
            status=ImportBatch.Status.READY_FOR_IMPORT,
        )

    def source(self, **overrides):
        data = {
            "source_timestamp": "2024-04-12T09:30:00Z",
            "first_name": "Amina",
            "last_name": "Zulu",
            "email": "amina@example.com",
            "mobile": "0791234567",
            "location": "Lilongwe",
            "age_range": Person.AgeRange.AGE_25_29,
            "gender": Person.Gender.FEMALE,
            "industry": "Technology",
            "job_title": "Engineer",
            "linkedin_url": "https://www.linkedin.com/in/amina",
        }
        data.update(overrides)
        return data

    def record(self, method, *, person=None, source=None, status=ImportRecord.Status.RESOLVED):
        return ImportRecord.objects.create(
            batch=self.batch,
            source_row_identifier=f"row-{ImportRecord.objects.count() + 1}",
            source_fingerprint=str(ImportRecord.objects.count() + 1).zfill(64),
            status=status,
            resolution_method=method,
            resolved_person=person,
            normalized_data=self.source() if source is None else source,
        )

    def test_ready_for_import_creates_business_person_membership_profile_and_provenance(self):
        industry = Industry.objects.create(name="Technology", slug="technology")
        record = self.record(ImportRecord.ResolutionMethod.NO_MATCH)

        result = import_membership_form_batch(batch_id=self.batch.id)

        record.refresh_from_db()
        self.batch.refresh_from_db()
        person = record.resolved_person
        self.assertEqual(result.status, ImportBatch.Status.IMPORTED)
        self.assertEqual(result.people_created, 1)
        self.assertEqual(person.record_type, Person.RecordType.BUSINESS)
        self.assertEqual(person.age_range, Person.AgeRange.AGE_25_29)
        self.assertEqual(person.gender, Person.Gender.FEMALE)
        self.assertEqual(person.membership.status, Membership.Status.ACTIVE)
        self.assertEqual(person.membership.membership_source, Membership.Source.MEMBERSHIP_FORM)
        self.assertNotEqual(person.membership.membership_source, Membership.Source.OTHER)
        self.assertEqual(str(person.membership.joined_at), "2024-04-12")
        self.assertEqual(person.professional_profile.industry, industry)
        self.assertEqual(record.status, ImportRecord.Status.COMMITTED)
        self.assertEqual(record.outcome, ImportRecord.Outcome.CREATED)
        self.assertIsNotNone(record.committed_at)
        self.assertEqual(self.batch.status, ImportBatch.Status.IMPORTED)
        self.assertIsNotNone(self.batch.completed_at)

    def test_staff_create_new_uses_the_same_business_person_creation_path(self):
        record = self.record(ImportRecord.ResolutionMethod.STAFF_CREATE_NEW)

        import_membership_form_batch(batch_id=self.batch.id)

        record.refresh_from_db()
        self.assertEqual(record.resolved_person.record_type, Person.RecordType.BUSINESS)
        self.assertEqual(record.outcome, ImportRecord.Outcome.CREATED)

    def test_auto_and_staff_matches_fill_only_missing_person_fields_and_reuse_active_membership(self):
        auto_person = Person.objects.create(
            first_name="Current",
            last_name="Person",
            primary_email="current@example.com",
            mobile="",
            location="Kumasi",
        )
        staff_person = Person.objects.create(
            first_name="Staff",
            last_name="Match",
            archived_at=timezone.now(),
        )
        existing_membership = Membership.objects.create(
            person=auto_person,
            status=Membership.Status.ACTIVE,
            joined_at="2020-01-01",
            membership_source=Membership.Source.OTHER,
        )
        auto_record = self.record(
            ImportRecord.ResolutionMethod.AUTO_MATCH,
            person=auto_person,
            source=self.source(mobile="0711111111", location="Lilongwe"),
        )
        staff_record = self.record(ImportRecord.ResolutionMethod.STAFF_MATCH, person=staff_person)

        result = import_membership_form_batch(batch_id=self.batch.id)

        auto_person.refresh_from_db()
        staff_person.refresh_from_db()
        auto_record.refresh_from_db()
        staff_record.refresh_from_db()
        existing_membership.refresh_from_db()
        self.assertEqual(auto_person.location, "Kumasi")
        self.assertEqual(auto_person.mobile, "0711111111")
        self.assertEqual(existing_membership.joined_at.isoformat(), "2020-01-01")
        self.assertEqual(existing_membership.membership_source, Membership.Source.OTHER)
        self.assertEqual(staff_person.primary_email, "amina@example.com")
        self.assertIsNotNone(staff_person.archived_at)
        self.assertEqual(result.memberships_reused, 1)
        self.assertEqual(result.memberships_created, 1)
        self.assertEqual(auto_record.outcome, ImportRecord.Outcome.UPDATED)
        self.assertEqual(staff_record.outcome, ImportRecord.Outcome.UPDATED)

    def test_non_active_membership_blocks_the_whole_batch_without_reactivation(self):
        first = self.record(ImportRecord.ResolutionMethod.NO_MATCH)
        former_person = Person.objects.create(first_name="Former", last_name="Member")
        former_membership = Membership.objects.create(
            person=former_person,
            status=Membership.Status.FORMER,
            joined_at="2020-01-01",
            ended_at="2021-01-01",
            membership_source=Membership.Source.STAFF,
        )
        self.record(ImportRecord.ResolutionMethod.AUTO_MATCH, person=former_person)

        with self.assertRaises(ImportBatchPreflightError):
            import_membership_form_batch(batch_id=self.batch.id)

        self.batch.refresh_from_db()
        first.refresh_from_db()
        former_membership.refresh_from_db()
        self.assertFalse(Person.objects.filter(primary_email="amina@example.com").exists())
        self.assertEqual(former_membership.status, Membership.Status.FORMER)
        self.assertEqual(former_membership.membership_source, Membership.Source.STAFF)
        self.assertEqual(first.status, ImportRecord.Status.RESOLVED)
        self.assertEqual(self.batch.status, ImportBatch.Status.READY_FOR_IMPORT)

    def test_later_mutation_failure_rolls_back_earlier_person_membership_and_audit_writes(self):
        self.record(ImportRecord.ResolutionMethod.NO_MATCH)
        self.record(
            ImportRecord.ResolutionMethod.NO_MATCH,
            source=self.source(
                first_name="Brian",
                last_name="Archive",
                email="brian@example.com",
                mobile="0799999999",
            ),
        )

        with mock.patch(
            "data_imports.services.membership_form_import._create_or_fill_profile",
            side_effect=[(False, []), RuntimeError("profile write failed")],
        ):
            with self.assertRaises(RuntimeError):
                import_membership_form_batch(batch_id=self.batch.id)

        self.batch.refresh_from_db()
        self.assertFalse(Person.objects.filter(primary_email__in=["amina@example.com", "brian@example.com"]).exists())
        self.assertEqual(Membership.objects.count(), 0)
        self.assertEqual(AuditEvent.objects.count(), 0)
        self.assertEqual(self.batch.status, ImportBatch.Status.READY_FOR_IMPORT)

    def test_invalid_rows_are_skipped_without_authoritative_mutation(self):
        invalid = self.record(
            None,
            status=ImportRecord.Status.INVALID,
            source=self.source(first_name=None, last_name=None),
        )

        result = import_membership_form_batch(batch_id=self.batch.id)

        invalid.refresh_from_db()
        self.assertEqual(result.invalid_skipped, 1)
        self.assertEqual(Person.objects.count(), 0)
        self.assertEqual(Membership.objects.count(), 0)
        self.assertEqual(ProfessionalProfile.objects.count(), 0)
        self.assertEqual(invalid.status, ImportRecord.Status.INVALID)
        self.assertEqual(invalid.outcome, ImportRecord.Outcome.SKIPPED)

    def test_unresolved_reviews_missing_resolved_people_and_wrong_batch_status_are_rejected(self):
        review = self.record(ImportRecord.ResolutionMethod.NOT_RESOLVED, status=ImportRecord.Status.REVIEW_REQUIRED)
        with self.assertRaises(ImportBatchPreflightError):
            import_membership_form_batch(batch_id=self.batch.id)
        review.delete()
        self.record(ImportRecord.ResolutionMethod.AUTO_MATCH)
        with self.assertRaises(ImportBatchPreflightError):
            import_membership_form_batch(batch_id=self.batch.id)
        self.batch.status = ImportBatch.Status.READY_FOR_REVIEW
        self.batch.save(update_fields=["status", "updated_at"])
        with self.assertRaises(ImportBatchNotImportable):
            import_membership_form_batch(batch_id=self.batch.id)

    def test_imported_batch_is_idempotently_rejected_and_does_not_duplicate_data(self):
        self.record(ImportRecord.ResolutionMethod.NO_MATCH)
        import_membership_form_batch(batch_id=self.batch.id)
        before_people = Person.objects.count()
        before_memberships = Membership.objects.count()
        before_audits = AuditEvent.objects.count()

        with self.assertRaises(ImportBatchNotImportable):
            import_membership_form_batch(batch_id=self.batch.id)

        self.assertEqual(Person.objects.count(), before_people)
        self.assertEqual(Membership.objects.count(), before_memberships)
        self.assertEqual(AuditEvent.objects.count(), before_audits)

    def test_profiles_use_safe_industry_matching_and_fill_missing_only(self):
        industry = Industry.objects.create(name="Technology", slug="technology")
        person = Person.objects.create(first_name="Existing", last_name="Person")
        profile = ProfessionalProfile.objects.create(person=person, job_title="Current title")
        self.record(
            ImportRecord.ResolutionMethod.AUTO_MATCH,
            person=person,
            source=self.source(job_title="Historical title", industry="Technology"),
        )

        import_membership_form_batch(batch_id=self.batch.id)

        profile.refresh_from_db()
        self.assertEqual(profile.job_title, "Current title")
        self.assertEqual(profile.industry, industry)
        self.assertEqual(Industry.objects.count(), 1)

    def test_unmappable_source_industry_does_not_create_taxonomy_records(self):
        person = Person.objects.create(first_name="Existing", last_name="Person")
        self.record(
            ImportRecord.ResolutionMethod.AUTO_MATCH,
            person=person,
            source=self.source(industry="Unmapped spreadsheet category", job_title="", linkedin_url=""),
        )

        import_membership_form_batch(batch_id=self.batch.id)

        self.assertEqual(Industry.objects.count(), 0)
        self.assertFalse(ProfessionalProfile.objects.filter(person=person).exists())

    def test_audit_events_use_identifiers_not_source_pii_and_roll_back_on_failure(self):
        self.record(ImportRecord.ResolutionMethod.NO_MATCH)

        import_membership_form_batch(batch_id=self.batch.id)

        events = list(AuditEvent.objects.all())
        self.assertTrue(any(event.action == AuditEvent.Action.IMPORT_BATCH_IMPORTED for event in events))
        self.assertNotIn("amina@example.com", str([event.metadata for event in events]))
        self.assertNotIn("0791234567", str([event.metadata for event in events]))

        failing_batch = ImportBatch.objects.create(
            source_type=ImportBatch.SourceType.MEMBERSHIP_FORM,
            source_filename="failing.xlsx",
            source_fingerprint="f" * 64,
            status=ImportBatch.Status.READY_FOR_IMPORT,
        )
        self.batch = failing_batch
        self.record(ImportRecord.ResolutionMethod.NO_MATCH)
        before_people = Person.objects.count()
        before_audits = AuditEvent.objects.count()
        with mock.patch(
            "data_imports.services.membership_form_import.record_audit_event",
            side_effect=RuntimeError("audit unavailable"),
        ):
            with self.assertRaises(RuntimeError):
                import_membership_form_batch(batch_id=failing_batch.id)

        failing_batch.refresh_from_db()
        self.assertEqual(Person.objects.count(), before_people)
        self.assertEqual(AuditEvent.objects.count(), before_audits)
        self.assertEqual(failing_batch.status, ImportBatch.Status.READY_FOR_IMPORT)
