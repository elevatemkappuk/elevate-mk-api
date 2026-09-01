import importlib

from django.apps import apps
from django.test import TestCase

from data_imports.models import ImportBatch, ImportRecord


class ImportBatchLifecycleTests(TestCase):
    def create_batch(self, status):
        return ImportBatch.objects.create(
            source_type=ImportBatch.SourceType.MEMBERSHIP_FORM,
            source_filename=f"{status.lower()}.xlsx",
            source_fingerprint=status.lower().ljust(64, "0"),
            status=status,
        )

    def test_imported_is_defined_but_not_an_automatic_analysis_destination(self):
        self.assertIn(ImportBatch.Status.IMPORTED, ImportBatch.Status.values)
        self.assertNotIn(ImportBatch.Status.IMPORTED, (
            ImportBatch.Status.PROCESSING,
            ImportBatch.Status.READY_FOR_REVIEW,
            ImportBatch.Status.READY_FOR_IMPORT,
        ))

    def test_legacy_batch_statuses_migrate_conservatively(self):
        pending = self.create_batch("PENDING")
        staged = self.create_batch("STAGED")
        ready_to_commit = self.create_batch("READY_TO_COMMIT")
        completed = self.create_batch("COMPLETED")
        analyzed_ready = self.create_batch("ANALYZED")
        analyzed_review = self.create_batch("ANALYZED")
        ImportRecord.objects.create(
            batch=analyzed_review,
            source_row_identifier="review-required",
            source_fingerprint="r" * 64,
            status=ImportRecord.Status.REVIEW_REQUIRED,
        )

        migration = importlib.import_module(
            "data_imports.migrations.0006_migrate_importbatch_lifecycle_statuses"
        )
        migration.migrate_importbatch_lifecycle_statuses(apps, None)

        for batch in (pending, staged, ready_to_commit, completed, analyzed_ready, analyzed_review):
            batch.refresh_from_db()
        self.assertEqual(pending.status, ImportBatch.Status.PROCESSING)
        self.assertEqual(staged.status, ImportBatch.Status.PROCESSING)
        self.assertEqual(ready_to_commit.status, ImportBatch.Status.READY_FOR_IMPORT)
        self.assertEqual(completed.status, ImportBatch.Status.IMPORTED)
        self.assertEqual(analyzed_ready.status, ImportBatch.Status.READY_FOR_IMPORT)
        self.assertEqual(analyzed_review.status, ImportBatch.Status.READY_FOR_REVIEW)
