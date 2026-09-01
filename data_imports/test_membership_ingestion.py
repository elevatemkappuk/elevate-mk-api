from datetime import datetime
from io import BytesIO

from django.test import TestCase
from openpyxl import Workbook

from data_imports.adapters.membership_form import REQUIRED_HEADERS, SHEET_NAME, MembershipFormStructureError
from data_imports.models import ImportBatch, ImportRecord
from data_imports.services.identity_analysis import analyze_import_batch
from data_imports.services.membership_ingestion import ingest_membership_form
from memberships.models import Membership
from people.models import Person


def workbook_bytes(headers=REQUIRED_HEADERS, rows=()):
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = SHEET_NAME
    worksheet.append(headers)
    for row in rows:
        worksheet.append(row)
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


class MembershipFormIngestionTests(TestCase):
    def sample_row(self, **overrides):
        values = {
            "Timestamp": datetime(2026, 2, 23, 20, 20, 41, 97000), "First Name ": " Amina ", "Last Name ": " Zulu ",
            "Gender": " Female ", "Age ": "25 - 29", "Email (preferably your personal email)": " AMINA@EXAMPLE.COM ",
            "Mobile Number": "079 123-4567", "Location": " Lilongwe ", "What industry do you work in?": " Technology ",
            "What is your current role or job title?": " Engineer ", "Linkedin URL": "https://linkedin.com/in/amina",
        }
        values.update(overrides)
        return [values[header] for header in REQUIRED_HEADERS]

    def test_stages_actual_headers_raw_evidence_and_normalized_values(self):
        batch = ingest_membership_form(workbook_bytes=workbook_bytes(rows=[self.sample_row()]), source_filename="membership.xlsx")
        record = batch.records.get()
        self.assertEqual(batch.status, ImportBatch.Status.STAGED)
        self.assertEqual(record.source_row_identifier, "sheet:Form Responses 1:row:2")
        self.assertEqual(record.raw_data["First Name "], " Amina ")
        self.assertEqual(record.raw_data["Timestamp"], "2026-02-23T20:20:41.097000")
        self.assertEqual(record.normalized_data["email"], "amina@example.com")
        self.assertEqual(record.normalized_data["mobile"], "0791234567")
        self.assertEqual(record.normalized_data["age_range"], Person.AgeRange.AGE_25_29)
        self.assertEqual(record.normalized_data["gender"], Person.Gender.FEMALE)
        self.assertEqual(record.status, ImportRecord.Status.STAGED)
        self.assertEqual(Person.objects.count(), 0)
        self.assertEqual(Membership.objects.count(), 0)

    def test_missing_header_fails_the_batch_without_records(self):
        payload = workbook_bytes(headers=REQUIRED_HEADERS[:-1], rows=[])
        with self.assertRaises(MembershipFormStructureError):
            ingest_membership_form(workbook_bytes=payload, source_filename="bad.xlsx")
        batch = ImportBatch.objects.get(source_filename="bad.xlsx")
        self.assertEqual(batch.status, ImportBatch.Status.FAILED)
        self.assertFalse(batch.records.exists())

    def test_invalid_optional_email_and_url_mark_row_invalid_but_preserve_evidence(self):
        batch = ingest_membership_form(workbook_bytes=workbook_bytes(rows=[self.sample_row(**{"Email (preferably your personal email)": "invalid", "Linkedin URL": "not-a-url"})]), source_filename="invalid.xlsx")
        record = batch.records.get()
        self.assertEqual(record.status, ImportRecord.Status.INVALID)
        self.assertEqual({error["code"] for error in record.validation_errors}, {"invalid_email", "invalid_url"})
        self.assertEqual(record.raw_data["Email (preferably your personal email)"], "invalid")

    def test_unknown_age_range_marks_row_invalid_without_replacing_it_with_null(self):
        batch = ingest_membership_form(
            workbook_bytes=workbook_bytes(rows=[self.sample_row(**{"Age ": "18-24"})]),
            source_filename="unknown-age.xlsx",
        )
        record = batch.records.get()

        self.assertEqual(record.status, ImportRecord.Status.INVALID)
        self.assertIn(
            {"field": "age_range", "code": "unsupported_age_range", "message": "Age range value is not supported."},
            record.validation_errors,
        )
        self.assertIsNone(record.normalized_data["age_range"])
        self.assertEqual(record.raw_data["Age "], "18-24")
        self.assertEqual(Person.objects.count(), 0)
        self.assertEqual(Membership.objects.count(), 0)

    def test_unknown_gender_marks_row_invalid_without_converting_to_other(self):
        batch = ingest_membership_form(
            workbook_bytes=workbook_bytes(rows=[self.sample_row(**{"Gender": "Prefer not to say"})]),
            source_filename="unknown-gender.xlsx",
        )
        record = batch.records.get()

        self.assertEqual(record.status, ImportRecord.Status.INVALID)
        self.assertIn(
            {"field": "gender", "code": "unsupported_gender", "message": "Gender value is not supported."},
            record.validation_errors,
        )
        self.assertIsNone(record.normalized_data["gender"])
        self.assertNotEqual(record.normalized_data["gender"], Person.Gender.OTHER)
        self.assertEqual(record.raw_data["Gender"], "Prefer not to say")
        self.assertEqual(Person.objects.count(), 0)
        self.assertEqual(Membership.objects.count(), 0)

    def test_identity_analysis_consumes_a_canonically_normalized_staged_row_without_crm_mutation(self):
        batch = ingest_membership_form(
            workbook_bytes=workbook_bytes(rows=[self.sample_row(**{"Age ": "25-29", "Gender": "Non Binary"})]),
            source_filename="normalized-row.xlsx",
        )

        analyze_import_batch(batch)
        record = batch.records.get()
        batch.refresh_from_db()

        self.assertEqual(record.normalized_data["age_range"], Person.AgeRange.AGE_25_29)
        self.assertEqual(record.normalized_data["gender"], Person.Gender.NON_BINARY)
        self.assertEqual(record.status, ImportRecord.Status.RESOLVED)
        self.assertEqual(batch.status, ImportBatch.Status.ANALYZED)
        self.assertEqual(Person.objects.count(), 0)
        self.assertEqual(Membership.objects.count(), 0)

    def test_extra_columns_are_preserved_and_empty_rows_are_ignored(self):
        headers = REQUIRED_HEADERS + ("Extra source field",)
        batch = ingest_membership_form(workbook_bytes=workbook_bytes(headers=headers, rows=[self.sample_row() + ["evidence"], [None] * len(headers)]), source_filename="extra.xlsx")
        self.assertEqual(batch.records.count(), 1)
        self.assertEqual(batch.records.get().raw_data["Extra source field"], "evidence")

    def test_workbook_and_row_fingerprints_are_deterministic_and_source_change_sensitive(self):
        first = workbook_bytes(rows=[self.sample_row()])
        second = workbook_bytes(rows=[self.sample_row()])
        changed = workbook_bytes(rows=[self.sample_row(**{"First Name ": "Different"})])
        first_batch = ingest_membership_form(workbook_bytes=first, source_filename="first.xlsx")
        second_batch = ingest_membership_form(workbook_bytes=second, source_filename="second.xlsx")
        changed_batch = ingest_membership_form(workbook_bytes=changed, source_filename="changed.xlsx")
        self.assertEqual(first_batch.source_fingerprint, second_batch.source_fingerprint)
        self.assertNotEqual(first_batch.source_fingerprint, changed_batch.source_fingerprint)
        self.assertNotEqual(first_batch.records.get().source_fingerprint, changed_batch.records.get().source_fingerprint)
