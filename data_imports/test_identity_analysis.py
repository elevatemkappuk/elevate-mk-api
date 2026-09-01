from django.test import TestCase

from data_imports.models import ImportBatch, ImportRecord
from data_imports.services.identity_analysis import analyze_import_batch
from people.models import Person


class IdentityAnalysisTests(TestCase):
    def setUp(self):
        self.batch = ImportBatch.objects.create(source_type="MEMBERSHIP_FORM", source_filename="source.xlsx", source_fingerprint="a" * 64, status=ImportBatch.Status.PROCESSING)

    def record(self, **data):
        return ImportRecord.objects.create(batch=self.batch, source_row_identifier=f"row-{ImportRecord.objects.count()}", source_fingerprint=str(ImportRecord.objects.count()).zfill(64), normalized_data=data)

    def person(self, **kwargs):
        defaults = {"first_name": "Amina", "last_name": "Zulu", "primary_email": "amina@example.com", "mobile": "0791234567"}
        defaults.update(kwargs)
        return Person.objects.create(**defaults)

    def test_unique_exact_email_without_contradiction_auto_matches(self):
        person = self.person()
        record = self.record(email="amina@example.com", mobile="0791234567", first_name="Amina", last_name="Zulu")
        analyze_import_batch(self.batch)
        record.refresh_from_db()
        self.assertEqual(record.status, ImportRecord.Status.RESOLVED)
        self.assertEqual(record.resolution_method, ImportRecord.ResolutionMethod.AUTO_MATCH)
        self.assertEqual(record.resolved_person, person)
        self.assertIsNone(record.outcome)

    def test_email_conflict_and_mobile_only_are_review_required(self):
        self.person()
        email_conflict = self.record(email="amina@example.com", mobile="different", first_name="Other", last_name="Name")
        mobile_only = self.record(mobile="0791234567", first_name="Amina", last_name="Zulu")
        analyze_import_batch(self.batch)
        email_conflict.refresh_from_db(); mobile_only.refresh_from_db()
        self.assertEqual(email_conflict.status, ImportRecord.Status.REVIEW_REQUIRED)
        self.assertIn("NAME_CONFLICT", email_conflict.match_candidates[0]["contradiction_codes"])
        self.assertEqual(mobile_only.resolution_reason, "MOBILE_ONLY_MATCH")
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.status, ImportBatch.Status.READY_FOR_REVIEW)

    def test_name_only_is_no_match_and_technical_people_are_excluded(self):
        self.person(primary_email=None, mobile="", record_type=Person.RecordType.TECHNICAL)
        record = self.record(first_name="Amina", last_name="Zulu")
        analyze_import_batch(self.batch)
        record.refresh_from_db()
        self.assertEqual(record.resolution_method, ImportRecord.ResolutionMethod.NO_MATCH)
        self.assertEqual(record.match_candidates, [])
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.status, ImportBatch.Status.READY_FOR_IMPORT)

    def test_profile_drift_does_not_block_email_auto_match_and_analysis_is_repeatable(self):
        self.person()
        record = self.record(email="amina@example.com", first_name="Amina", last_name="Zulu", industry="Different", job_title="Different")
        analyze_import_batch(self.batch)
        record.refresh_from_db()
        first = (record.status, record.resolution_method, record.match_candidates)
        analyze_import_batch(self.batch)
        record.refresh_from_db()
        self.assertEqual((record.status, record.resolution_method, record.match_candidates), first)

    def test_invalid_and_committed_records_are_not_analyzed_or_mutated(self):
        invalid = self.record(email="amina@example.com")
        invalid.status = ImportRecord.Status.INVALID; invalid.save(update_fields=["status"])
        committed = self.record(email="amina@example.com")
        committed.status = ImportRecord.Status.COMMITTED; committed.resolution_reason = "preserve"; committed.save(update_fields=["status", "resolution_reason"])
        before_people = Person.objects.count()
        analyze_import_batch(self.batch)
        invalid.refresh_from_db(); committed.refresh_from_db()
        self.assertEqual(invalid.status, ImportRecord.Status.INVALID)
        self.assertEqual(committed.resolution_reason, "preserve")
        self.assertEqual(Person.objects.count(), before_people)
