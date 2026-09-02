from django.urls import resolve
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import User
from audit.models import AuditEvent
from data_imports.models import ImportBatch, ImportRecord
from people.models import Person
from staff_access.models import StaffRole, StaffRoleAssignment


class ImportReconciliationApiTests(APITestCase):
    def setUp(self):
        self.admin = self.create_user("admin@example.com")
        self.manager = self.create_user("manager@example.com")
        self.viewer = self.create_user("viewer@example.com")
        self.superuser = self.create_user("superuser@example.com", is_staff=True, is_superuser=True)
        self.crm_admin_role = StaffRole.objects.create(code=StaffRole.CRM_ADMIN, name="CRM Administrator")
        manager_role = StaffRole.objects.create(code=StaffRole.CRM_MANAGER, name="CRM Manager")
        viewer_role = StaffRole.objects.create(code=StaffRole.CRM_VIEWER, name="CRM Viewer")
        StaffRoleAssignment.objects.assign_role(user=self.admin, role=self.crm_admin_role)
        StaffRoleAssignment.objects.assign_role(user=self.manager, role=manager_role)
        StaffRoleAssignment.objects.assign_role(user=self.viewer, role=viewer_role)
        self.batch = ImportBatch.objects.create(
            source_type=ImportBatch.SourceType.MEMBERSHIP_FORM,
            source_filename="membership.xlsx",
            source_fingerprint="b" * 64,
            status=ImportBatch.Status.READY_FOR_REVIEW,
        )
        self.person = Person.objects.create(
            first_name="Current",
            last_name="Person",
            primary_email="current@example.com",
            mobile="0712300000",
        )

    @staticmethod
    def create_user(email, **extra_fields):
        return User.objects.create_user(
            email=email,
            password="safe-password",
            person_first_name=email.split("@")[0],
            person_last_name="User",
            **extra_fields,
        )

    def create_review_record(self, *, candidate_id=None, source_overrides=None):
        candidate_id = self.person.id if candidate_id is None else candidate_id
        source = {
            "first_name": "Source",
            "last_name": "Record",
            "email": "source@example.com",
            "mobile": "0799999999",
            "location": "Milton Keynes",
            "industry": "Technology",
            "job_title": "Engineer",
            "linkedin_url": "https://www.linkedin.com/in/source",
        }
        source.update(source_overrides or {})
        return ImportRecord.objects.create(
            batch=self.batch,
            source_row_identifier=f"row-{ImportRecord.objects.count()}",
            source_fingerprint=str(ImportRecord.objects.count()).zfill(64),
            normalized_data=source,
            raw_data={"private_spreadsheet_column": "must never be returned"},
            status=ImportRecord.Status.REVIEW_REQUIRED,
            resolution_reason="UNIQUE_EMAIL_WITH_CONTRADICTION",
            match_candidates=[
                {
                    "person_id": candidate_id,
                    "matched_on": ["email"],
                    "email_agreement": True,
                    "mobile_agreement": False,
                    "name_agreement": False,
                    "contradiction_codes": ["MOBILE_CONFLICT"],
                }
            ],
        )

    def authenticate_as(self, user):
        self.client.force_authenticate(user=user)

    def test_import_list_url_resolves(self):
        match = resolve("/api/v1/imports/")
        self.assertEqual(match.url_name, "import-batch-list")

    def test_only_crm_admin_can_access_import_api(self):
        for user in (self.manager, self.viewer, self.superuser):
            self.authenticate_as(user)
            self.assertEqual(self.client.get("/api/v1/imports/").status_code, status.HTTP_403_FORBIDDEN)
        self.authenticate_as(self.admin)
        self.assertEqual(self.client.get("/api/v1/imports/").status_code, status.HTTP_200_OK)

    def test_batch_list_returns_efficient_summary_counts(self):
        self.create_review_record()
        ImportRecord.objects.create(
            batch=self.batch,
            source_row_identifier="invalid-row",
            source_fingerprint="i" * 64,
            status=ImportRecord.Status.INVALID,
        )
        self.authenticate_as(self.admin)
        response = self.client.get("/api/v1/imports/")
        data = response.data[0]
        self.assertEqual(data["total_count"], 2)
        self.assertEqual(data["review_required_count"], 1)
        self.assertEqual(data["invalid_count"], 1)

    def test_review_queue_exposes_only_safe_normalized_fields_and_current_candidates(self):
        review_record = self.create_review_record()
        ImportRecord.objects.create(
            batch=self.batch,
            source_row_identifier="resolved-row",
            source_fingerprint="r" * 64,
            status=ImportRecord.Status.RESOLVED,
        )
        self.person.first_name = "Updated Current"
        self.person.save(update_fields=["first_name", "updated_at"])
        self.authenticate_as(self.admin)
        response = self.client.get(f"/api/v1/imports/{self.batch.id}/review/")
        self.assertEqual(response.data["count"], 1)
        data = response.data["results"][0]
        self.assertEqual(data["id"], review_record.id)
        self.assertEqual(data["source"]["email"], "source@example.com")
        self.assertNotIn("raw_data", data)
        self.assertNotIn("user", data)
        self.assertEqual(data["candidates"][0]["first_name"], "Updated Current")
        self.assertEqual(data["candidates"][0]["matched_on"], ["EXACT_EMAIL"])

    def test_same_person_resolution_sets_staff_metadata_without_mutating_person(self):
        record = self.create_review_record()
        person_count = Person.objects.count()
        self.authenticate_as(self.admin)
        response = self.client.post(
            f"/api/v1/imports/{self.batch.id}/review/{record.id}/resolve/",
            {"resolution": "SAME_PERSON", "person_id": self.person.id},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        record.refresh_from_db()
        self.assertEqual(record.status, ImportRecord.Status.RESOLVED)
        self.assertEqual(record.resolution_method, ImportRecord.ResolutionMethod.STAFF_MATCH)
        self.assertEqual(record.resolved_person, self.person)
        self.assertEqual(record.reviewed_by, self.admin)
        self.assertIsNotNone(record.reviewed_at)
        self.assertIsNone(record.outcome)
        self.assertIsNone(record.committed_at)
        self.assertEqual(Person.objects.count(), person_count)
        audit_event = AuditEvent.objects.get(entity_id=str(record.id))
        self.assertEqual(audit_event.action, AuditEvent.Action.IMPORT_RECORD_MATCH_CONFIRMED)
        self.assertEqual(set(audit_event.metadata), {"import_batch_id", "import_record_id", "resolution_method", "resolved_person_id"})
        self.assertNotIn("source@example.com", str(audit_event.metadata))

    def test_same_person_rejects_non_candidates_and_technical_people(self):
        record = self.create_review_record()
        non_candidate = Person.objects.create(first_name="Other", last_name="Person")
        technical = Person.objects.create(first_name="Technical", last_name="Person", record_type=Person.RecordType.TECHNICAL)
        self.authenticate_as(self.admin)
        url = f"/api/v1/imports/{self.batch.id}/review/{record.id}/resolve/"
        self.assertEqual(self.client.post(url, {"resolution": "SAME_PERSON", "person_id": non_candidate.id}, format="json").status_code, status.HTTP_400_BAD_REQUEST)
        record.match_candidates[0]["person_id"] = technical.id
        record.save(update_fields=["match_candidates"])
        self.assertEqual(self.client.post(url, {"resolution": "SAME_PERSON", "person_id": technical.id}, format="json").status_code, status.HTTP_400_BAD_REQUEST)

    def test_different_person_resolution_never_creates_person_and_finishes_review_lifecycle(self):
        record = self.create_review_record()
        person_count = Person.objects.count()
        self.authenticate_as(self.admin)
        url = f"/api/v1/imports/{self.batch.id}/review/{record.id}/resolve/"
        response = self.client.post(url, {"resolution": "DIFFERENT_PERSON"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        record.refresh_from_db()
        self.batch.refresh_from_db()
        self.assertEqual(record.resolution_method, ImportRecord.ResolutionMethod.STAFF_CREATE_NEW)
        self.assertIsNone(record.resolved_person)
        self.assertEqual(self.batch.status, ImportBatch.Status.READY_FOR_IMPORT)
        self.assertEqual(Person.objects.count(), person_count)
        self.assertEqual(self.client.post(url, {"resolution": "DIFFERENT_PERSON"}, format="json").status_code, status.HTTP_409_CONFLICT)

    def test_different_person_accepts_mobile_only_collision_with_safe_audit_evidence(self):
        record = self.create_review_record(source_overrides={"email": "new@example.com", "mobile": self.person.mobile})
        self.authenticate_as(self.admin)

        response = self.client.post(
            f"/api/v1/imports/{self.batch.id}/review/{record.id}/resolve/",
            {"resolution": "DIFFERENT_PERSON"},
            format="json",
        )

        record.refresh_from_db()
        self.batch.refresh_from_db()
        event = AuditEvent.objects.get(entity_id=str(record.id))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(record.resolution_method, ImportRecord.ResolutionMethod.STAFF_CREATE_NEW)
        self.assertEqual(self.batch.status, ImportBatch.Status.READY_FOR_IMPORT)
        self.assertEqual(event.metadata["identity_collision"], "MOBILE_COLLISION")
        self.assertEqual(event.metadata["identity_collision_person_ids"], [str(self.person.id)])
        self.assertNotIn("new@example.com", str(event.metadata))
        self.assertNotIn(self.person.mobile, str(event.metadata))

    def test_different_person_rejects_exact_email_collision_without_finalizing_resolution(self):
        record = self.create_review_record(source_overrides={"email": self.person.primary_email, "mobile": "0799999999"})
        self.authenticate_as(self.admin)

        response = self.client.post(
            f"/api/v1/imports/{self.batch.id}/review/{record.id}/resolve/",
            {"resolution": "DIFFERENT_PERSON"},
            format="json",
        )

        record.refresh_from_db()
        self.batch.refresh_from_db()
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(
            response.data["detail"],
            "This record uses an email address already assigned to an existing CRM Person and cannot be created as a separate Person.",
        )
        self.assertEqual(record.status, ImportRecord.Status.REVIEW_REQUIRED)
        self.assertNotEqual(record.resolution_method, ImportRecord.ResolutionMethod.STAFF_CREATE_NEW)
        self.assertEqual(self.batch.status, ImportBatch.Status.READY_FOR_REVIEW)

    def test_batch_remains_ready_for_review_until_the_final_review_record_is_resolved(self):
        first_record = self.create_review_record()
        self.create_review_record()
        self.authenticate_as(self.admin)
        response = self.client.post(
            f"/api/v1/imports/{self.batch.id}/review/{first_record.id}/resolve/",
            {"resolution": "DIFFERENT_PERSON"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.status, ImportBatch.Status.READY_FOR_REVIEW)

    def test_batch_api_exposes_ready_for_import_after_identity_decisions(self):
        self.batch.status = ImportBatch.Status.READY_FOR_IMPORT
        self.batch.save(update_fields=["status", "updated_at"])
        self.authenticate_as(self.admin)

        response = self.client.get(f"/api/v1/imports/{self.batch.id}/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], ImportBatch.Status.READY_FOR_IMPORT)
