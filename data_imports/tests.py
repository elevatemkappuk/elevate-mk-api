from django.db import IntegrityError, transaction
from django.test import TestCase

from accounts.models import User
from data_imports.models import ImportBatch, ImportRecord
from data_imports.services.fingerprints import fingerprint_source, fingerprint_source_row
from people.models import Person


class ImportStagingModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="importer@example.com",
            password="testpass123",
            person_first_name="Import",
            person_last_name="Operator",
        )
        self.batch = ImportBatch.objects.create(
            source_type=ImportBatch.SourceType.MEMBERSHIP_FORM,
            source_filename="memberships.xlsx",
            source_fingerprint=fingerprint_source({"file": "memberships.xlsx"}),
            created_by=self.user,
        )

    def test_batch_and_record_default_to_staging_lifecycle_states(self):
        record = ImportRecord.objects.create(
            batch=self.batch,
            source_row_identifier="row-2",
            source_fingerprint=fingerprint_source_row({"email": "person@example.com"}),
        )

        self.assertEqual(self.batch.status, ImportBatch.Status.PROCESSING)
        self.assertEqual(record.status, ImportRecord.Status.STAGED)
        self.assertIsNone(record.outcome)
        self.assertIsNone(record.resolved_person)
        self.assertEqual(record.raw_data, {})
        self.assertEqual(record.normalized_data, {})
        self.assertEqual(record.match_candidates, [])
        self.assertEqual(self.batch.created_by, self.user)

    def test_raw_and_normalized_data_are_independent(self):
        record = ImportRecord.objects.create(
            batch=self.batch,
            source_row_identifier="row-3",
            source_fingerprint=fingerprint_source_row({"name": "Original"}),
            raw_data={"name": "Original", "source_column": "A"},
            normalized_data={"first_name": "Original"},
        )
        record.normalized_data["first_name"] = "Changed"
        record.save(update_fields=["normalized_data"])
        record.refresh_from_db()

        self.assertEqual(record.raw_data, {"name": "Original", "source_column": "A"})
        self.assertEqual(record.normalized_data, {"first_name": "Changed"})

    def test_source_row_identifier_is_stable_and_unique_within_a_batch(self):
        kwargs = {
            "batch": self.batch,
            "source_row_identifier": "eventbrite-order-123-attendee-1",
            "source_fingerprint": fingerprint_source_row({"attendee": 1}),
        }
        ImportRecord.objects.create(**kwargs)

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ImportRecord.objects.create(**kwargs)

    def test_resolved_person_is_optional_provenance_metadata(self):
        person = Person.objects.create(first_name="Resolved", last_name="Person")
        record = ImportRecord.objects.create(
            batch=self.batch,
            source_row_identifier="row-4",
            source_fingerprint=fingerprint_source_row({"name": "Resolved Person"}),
            resolved_person=person,
            resolution_method=ImportRecord.ResolutionMethod.STAFF_MATCH,
        )

        self.assertEqual(record.resolved_person, person)
        self.assertEqual(record.reviewed_by, None)


class ImportFingerprintTests(TestCase):
    def test_fingerprint_is_deterministic_independent_of_dictionary_key_order(self):
        self.assertEqual(
            fingerprint_source({"name": "Amina", "email": "amina@example.com"}),
            fingerprint_source({"email": "amina@example.com", "name": "Amina"}),
        )

    def test_fingerprint_changes_when_source_content_changes(self):
        self.assertNotEqual(
            fingerprint_source_row({"email": "first@example.com"}),
            fingerprint_source_row({"email": "second@example.com"}),
        )
