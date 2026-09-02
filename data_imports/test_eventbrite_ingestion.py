from datetime import date, time
from io import BytesIO

from django.test import TestCase
from django.urls import resolve
from django.utils import timezone
from openpyxl import Workbook
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import User
from data_imports.adapters.eventbrite import REQUIRED_COLUMN_ALIASES, EventbriteStructureError
from data_imports.models import ImportBatch, ImportRecord
from data_imports.services.eventbrite_ingestion import ingest_eventbrite_workbook
from data_imports.services.identity_analysis import analyze_import_batch
from events.models import Event, EventParticipation, ExternalEventReference
from memberships.models import Membership
from people.models import Person
from professional_profiles.models import ProfessionalProfile
from staff_access.models import StaffRole, StaffRoleAssignment


HEADERS = tuple(aliases[0] for aliases in REQUIRED_COLUMN_ALIASES.values()) + ("Gross Sales", "Payment Status")


def workbook_bytes(headers=HEADERS, rows=()):
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Eventbrite export"
    worksheet.append(headers)
    for row in rows:
        worksheet.append(row)
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def eventbrite_row(**overrides):
    values = {
        "Buyer First Name": " Amina ",
        "Buyer Surname": " Zulu ",
        "Buyer Email": " AMINA@EXAMPLE.COM ",
        "Phone Number": "079 123-4567",
        "Purchaser Town/City": " Lilongwe ",
        "Purchaser County": " Central ",
        "Purchaser Country": " Malawi ",
        "Event ID": "1085298078769",
        "Event Name": " MK Professionals Meet Up ",
        "Event Start Date": date(2024, 8, 31),
        "Event Start Time": time(18, 30),
        "Event Timezone": "Africa/Blantyre",
        "Event Location": " Bingu Conference Centre ",
        "Order ID": "ORDER-123",
        "Order Date": date(2024, 8, 1),
        "Ticket Quantity": 2,
        "Guest": "No",
        "Gross Sales": "£20.00",
        "Payment Status": "Paid",
    }
    values.update(overrides)
    return [values.get(header) for header in HEADERS]


class EventbriteIngestionTests(TestCase):
    def test_stages_canonical_eventbrite_data_and_preserves_financial_columns_only_in_raw_data(self):
        batch = ingest_eventbrite_workbook(
            workbook_bytes=workbook_bytes(rows=[eventbrite_row()]), source_filename="eventbrite.xlsx"
        )
        record = batch.records.get()

        self.assertEqual(batch.source_type, ImportBatch.SourceType.EVENTBRITE)
        self.assertEqual(batch.status, ImportBatch.Status.STAGED)
        self.assertEqual(record.source_row_identifier, "sheet:2")
        self.assertEqual(record.status, ImportRecord.Status.STAGED)
        self.assertEqual(record.normalized_data["person"], {
            "first_name": "Amina", "last_name": "Zulu", "email": "amina@example.com", "mobile": "0791234567",
            "city": "Lilongwe", "county": "Central", "country": "Malawi",
        })
        self.assertEqual(record.normalized_data["event"]["external_event_id"], "1085298078769")
        self.assertEqual(record.normalized_data["event"]["name"], "MK Professionals Meet Up")
        self.assertEqual(record.normalized_data["event"]["start_at"], "2024-08-31T18:30:00+02:00")
        self.assertEqual(record.normalized_data["event"]["timezone"], "Africa/Blantyre")
        self.assertEqual(record.normalized_data["source"], {
            "provider": "EVENTBRITE", "external_order_id": "ORDER-123", "order_date": "2024-08-01",
            "ticket_quantity": 2, "guest": False,
        })
        self.assertEqual(record.raw_data["Gross Sales"], "£20.00")
        self.assertNotIn("Gross Sales", record.normalized_data)
        self.assertNotIn("external_participation_id", record.normalized_data["source"])

    def test_ignores_totals_and_empty_rows(self):
        batch = ingest_eventbrite_workbook(
            workbook_bytes=workbook_bytes(rows=[eventbrite_row(), [None] * len(HEADERS), eventbrite_row(**{"Buyer First Name": "Totals"})]),
            source_filename="totals.xlsx",
        )

        self.assertEqual(batch.records.count(), 1)

    def test_row_validation_is_safe_for_email_and_event_timing(self):
        batch = ingest_eventbrite_workbook(
            workbook_bytes=workbook_bytes(rows=[eventbrite_row(**{
                "Buyer Email": "invalid", "Event Start Date": "not-a-date", "Event Start Time": "not-a-time", "Event Timezone": "Unknown/Timezone",
            })]),
            source_filename="invalid.xlsx",
        )
        record = batch.records.get()

        self.assertEqual(record.status, ImportRecord.Status.INVALID)
        self.assertEqual(
            {(error["field"], error["code"]) for error in record.validation_errors},
            {("person.email", "invalid_email"), ("event.start_date", "invalid_event_date"), ("event.start_time", "invalid_event_time"), ("event.timezone", "invalid_timezone")},
        )
        self.assertIsNone(record.normalized_data["event"]["start_at"])

    def test_repeated_ingestion_is_deterministic_and_never_mutates_authoritative_models(self):
        before = {
            "people": Person.objects.count(), "memberships": Membership.objects.count(),
            "profiles": ProfessionalProfile.objects.count(), "events": Event.objects.count(),
            "participations": EventParticipation.objects.count(), "references": ExternalEventReference.objects.count(),
        }
        source = workbook_bytes(rows=[eventbrite_row()])
        first = ingest_eventbrite_workbook(workbook_bytes=source, source_filename="first.xlsx")
        second = ingest_eventbrite_workbook(workbook_bytes=source, source_filename="second.xlsx")

        self.assertEqual(first.source_fingerprint, second.source_fingerprint)
        self.assertEqual(first.records.get().source_fingerprint, second.records.get().source_fingerprint)
        self.assertNotEqual(first.id, second.id)
        self.assertEqual(before["people"], Person.objects.count())
        self.assertEqual(before["memberships"], Membership.objects.count())
        self.assertEqual(before["profiles"], ProfessionalProfile.objects.count())
        self.assertEqual(before["events"], Event.objects.count())
        self.assertEqual(before["participations"], EventParticipation.objects.count())
        self.assertEqual(before["references"], ExternalEventReference.objects.count())

    def test_missing_columns_marks_batch_failed_without_records(self):
        with self.assertRaises(EventbriteStructureError):
            ingest_eventbrite_workbook(
                workbook_bytes=workbook_bytes(headers=("Event ID",), rows=[]), source_filename="missing.xlsx"
            )
        batch = ImportBatch.objects.get(source_filename="missing.xlsx")
        self.assertEqual(batch.status, ImportBatch.Status.FAILED)
        self.assertFalse(batch.records.exists())


class EventbriteIdentityAnalysisTests(TestCase):
    def setUp(self):
        self.batch = ImportBatch.objects.create(
            source_type=ImportBatch.SourceType.EVENTBRITE,
            source_filename="eventbrite.xlsx",
            source_fingerprint="e" * 64,
            status=ImportBatch.Status.STAGED,
        )

    def record(self, person=None, status=ImportRecord.Status.STAGED, **event_source):
        return ImportRecord.objects.create(
            batch=self.batch,
            source_row_identifier=f"row-{ImportRecord.objects.count()}",
            source_fingerprint=str(ImportRecord.objects.count()).zfill(64),
            status=status,
            normalized_data={
                "person": person or {},
                "event": {"external_event_id": event_source.get("external_event_id", "event-1"), "name": event_source.get("name", "Event")},
                "source": {"external_order_id": event_source.get("external_order_id", "order-1")},
            },
        )

    def person(self, **overrides):
        values = {"first_name": "Amina", "last_name": "Zulu", "primary_email": "amina@example.com", "mobile": "0791234567"}
        values.update(overrides)
        return Person.objects.create(**values)

    def test_eventbrite_uses_shared_identity_rules_without_event_or_membership_mutations(self):
        matched = self.person()
        archived = self.person(first_name="Archived", last_name="Person", primary_email="archived@example.com", mobile="", archived_at=timezone.now())
        email_match = self.record(person={"first_name": "Amina", "last_name": "Zulu", "email": "amina@example.com", "mobile": "0791234567"})
        archived_match = self.record(person={"email": "archived@example.com"})
        mobile_only = self.record(person={"mobile": "0791234567"})
        no_match = self.record(person={"first_name": "Amina", "last_name": "Zulu"}, external_event_id="event-name-only", external_order_id="order-name-only")
        invalid = self.record(person={"email": "invalid"}, status=ImportRecord.Status.INVALID)
        before = (Membership.objects.count(), ProfessionalProfile.objects.count(), Event.objects.count(), EventParticipation.objects.count(), ExternalEventReference.objects.count())

        analyze_import_batch(self.batch)

        email_match.refresh_from_db(); archived_match.refresh_from_db(); mobile_only.refresh_from_db(); no_match.refresh_from_db(); invalid.refresh_from_db(); self.batch.refresh_from_db()
        self.assertEqual(email_match.resolution_method, ImportRecord.ResolutionMethod.AUTO_MATCH)
        self.assertEqual(email_match.resolved_person, matched)
        self.assertEqual(archived_match.resolution_method, ImportRecord.ResolutionMethod.AUTO_MATCH)
        self.assertEqual(archived_match.resolved_person, archived)
        self.assertEqual(mobile_only.status, ImportRecord.Status.REVIEW_REQUIRED)
        self.assertEqual(no_match.resolution_method, ImportRecord.ResolutionMethod.NO_MATCH)
        self.assertEqual(no_match.match_candidates, [])
        self.assertEqual(invalid.status, ImportRecord.Status.INVALID)
        self.assertEqual(self.batch.status, ImportBatch.Status.READY_FOR_REVIEW)
        self.assertEqual(before, (Membership.objects.count(), ProfessionalProfile.objects.count(), Event.objects.count(), EventParticipation.objects.count(), ExternalEventReference.objects.count()))

    def test_conflicting_and_multiple_strong_evidence_requires_review_and_no_review_reaches_ready_for_import(self):
        self.person()
        conflicting = self.record(person={"first_name": "Other", "last_name": "Name", "email": "amina@example.com", "mobile": "different"})
        self.person(first_name="Other", last_name="Email", primary_email="amina@example.com", mobile="")

        analyze_import_batch(self.batch)

        conflicting.refresh_from_db(); self.batch.refresh_from_db()
        self.assertEqual(conflicting.status, ImportRecord.Status.REVIEW_REQUIRED)
        self.assertEqual(self.batch.status, ImportBatch.Status.READY_FOR_REVIEW)

        ready_batch = ImportBatch.objects.create(source_type=ImportBatch.SourceType.EVENTBRITE, source_filename="ready.xlsx", source_fingerprint="r" * 64, status=ImportBatch.Status.STAGED)
        ImportRecord.objects.create(batch=ready_batch, source_row_identifier="no-match", source_fingerprint="n" * 64, normalized_data={"person": {"first_name": "No", "last_name": "Match"}, "event": {}, "source": {}})
        ImportRecord.objects.create(batch=ready_batch, source_row_identifier="invalid", source_fingerprint="i" * 64, status=ImportRecord.Status.INVALID, normalized_data={"person": {}, "event": {}, "source": {}})
        analyze_import_batch(ready_batch)
        ready_batch.refresh_from_db()
        self.assertEqual(ready_batch.status, ImportBatch.Status.READY_FOR_IMPORT)

    def test_eventbrite_analysis_accepts_only_staged_batches(self):
        for state in (ImportBatch.Status.PROCESSING, ImportBatch.Status.READY_FOR_REVIEW, ImportBatch.Status.READY_FOR_IMPORT, ImportBatch.Status.IMPORTED, ImportBatch.Status.FAILED):
            with self.subTest(state=state):
                self.batch.status = state
                self.batch.save(update_fields=["status"])
                with self.assertRaises(ValueError):
                    analyze_import_batch(self.batch)


class EventbriteUploadApiTests(APITestCase):
    def setUp(self):
        self.admin = self.create_user("event-admin@example.com")
        self.manager = self.create_user("event-manager@example.com")
        self.viewer = self.create_user("event-viewer@example.com")
        for code, user in ((StaffRole.CRM_ADMIN, self.admin), (StaffRole.CRM_MANAGER, self.manager), (StaffRole.CRM_VIEWER, self.viewer)):
            role, _ = StaffRole.objects.get_or_create(code=code, defaults={"name": code})
            StaffRoleAssignment.objects.assign_role(user=user, role=role)

    @staticmethod
    def create_user(email):
        return User.objects.create_user(email=email, password="safe-password", person_first_name="Event", person_last_name="Admin")

    def upload(self, payload):
        file = BytesIO(payload)
        file.name = "eventbrite.xlsx"
        return self.client.post("/api/v1/imports/eventbrite/", {"file": file}, format="multipart")

    def test_url_and_crm_admin_access(self):
        self.assertEqual(resolve("/api/v1/imports/eventbrite/").url_name, "import-eventbrite-upload")
        self.assertEqual(self.upload(workbook_bytes()).status_code, status.HTTP_401_UNAUTHORIZED)
        for user in (self.manager, self.viewer):
            self.client.force_authenticate(user=user)
            self.assertEqual(self.upload(workbook_bytes()).status_code, status.HTTP_403_FORBIDDEN)
        self.client.force_authenticate(user=self.admin)
        response = self.upload(workbook_bytes(rows=[eventbrite_row()]))
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(set(response.data), {
            "id", "source_type", "source_filename", "status", "created_at", "started_at", "completed_at", "total_count",
            "review_required_count", "resolved_count", "invalid_count", "committed_count", "auto_match_count", "new_person_count",
        })

    def test_corrupt_and_invalid_structure_fail_safely_and_preserve_failed_batch(self):
        self.client.force_authenticate(user=self.admin)
        response = self.upload(b"not a workbook")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(ImportBatch.objects.get(source_filename="eventbrite.xlsx").status, ImportBatch.Status.FAILED)
        response = self.upload(workbook_bytes(headers=("Event ID",)))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(ImportBatch.objects.latest("id").status, ImportBatch.Status.FAILED)

    def test_crm_admin_can_analyze_only_staged_eventbrite_batches(self):
        eventbrite = ImportBatch.objects.create(
            source_type=ImportBatch.SourceType.EVENTBRITE,
            source_filename="staged.xlsx",
            source_fingerprint="s" * 64,
            status=ImportBatch.Status.STAGED,
        )
        ImportRecord.objects.create(
            batch=eventbrite,
            source_row_identifier="row-1",
            source_fingerprint="1" * 64,
            normalized_data={"person": {"first_name": "No", "last_name": "Match"}, "event": {}, "source": {}},
        )
        self.client.force_authenticate(user=self.manager)
        self.assertEqual(self.client.post(f"/api/v1/imports/{eventbrite.id}/analyze/", {}, format="json").status_code, status.HTTP_403_FORBIDDEN)
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(f"/api/v1/imports/{eventbrite.id}/analyze/", {}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], ImportBatch.Status.READY_FOR_IMPORT)
        self.assertEqual(self.client.post(f"/api/v1/imports/{eventbrite.id}/analyze/", {}, format="json").status_code, status.HTTP_409_CONFLICT)

        membership_batch = ImportBatch.objects.create(
            source_type=ImportBatch.SourceType.MEMBERSHIP_FORM,
            source_filename="membership.xlsx",
            source_fingerprint="m" * 64,
            status=ImportBatch.Status.STAGED,
        )
        self.assertEqual(self.client.post(f"/api/v1/imports/{membership_batch.id}/analyze/", {}, format="json").status_code, status.HTTP_409_CONFLICT)
