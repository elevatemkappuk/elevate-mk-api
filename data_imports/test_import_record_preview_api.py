from django.urls import resolve
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import User
from data_imports.models import ImportBatch, ImportRecord
from memberships.models import Membership
from people.models import Person
from professional_profiles.models import ProfessionalProfile
from staff_access.models import StaffRole, StaffRoleAssignment


class ImportRecordPreviewApiTests(APITestCase):
    def setUp(self):
        self.admin = self.create_user("admin@example.com")
        self.manager = self.create_user("manager@example.com")
        self.viewer = self.create_user("viewer@example.com")
        self.superuser = self.create_user("superuser@example.com", is_staff=True, is_superuser=True)
        admin_role = StaffRole.objects.create(code=StaffRole.CRM_ADMIN, name="CRM Administrator")
        manager_role = StaffRole.objects.create(code=StaffRole.CRM_MANAGER, name="CRM Manager")
        viewer_role = StaffRole.objects.create(code=StaffRole.CRM_VIEWER, name="CRM Viewer")
        StaffRoleAssignment.objects.assign_role(user=self.admin, role=admin_role)
        StaffRoleAssignment.objects.assign_role(user=self.manager, role=manager_role)
        StaffRoleAssignment.objects.assign_role(user=self.viewer, role=viewer_role)
        self.batch = ImportBatch.objects.create(
            source_type=ImportBatch.SourceType.MEMBERSHIP_FORM,
            source_filename="source.xlsx",
            source_fingerprint="a" * 64,
            status=ImportBatch.Status.READY_FOR_REVIEW,
        )
        self.person = Person.objects.create(
            first_name="Existing", last_name="Person", primary_email="existing@example.com", mobile="0790000000"
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

    def record(self, identifier, **kwargs):
        defaults = {
            "batch": self.batch,
            "source_row_identifier": identifier,
            "source_fingerprint": identifier.zfill(64),
            "normalized_data": {"first_name": "Source", "last_name": identifier, "email": f"{identifier}@example.com"},
            "raw_data": {"private": "must-not-be-returned"},
            "status": ImportRecord.Status.RESOLVED,
        }
        defaults.update(kwargs)
        return ImportRecord.objects.create(**defaults)

    def authenticate_as(self, user):
        self.client.force_authenticate(user=user)

    def test_records_url_resolves(self):
        self.assertEqual(resolve("/api/v1/imports/1/records/").url_name, "import-record-list")

    def test_only_crm_admin_can_access_records_preview(self):
        for user in (self.manager, self.viewer, self.superuser):
            self.authenticate_as(user)
            self.assertEqual(self.client.get(f"/api/v1/imports/{self.batch.id}/records/").status_code, status.HTTP_403_FORBIDDEN)
        self.authenticate_as(self.admin)
        self.assertEqual(self.client.get(f"/api/v1/imports/{self.batch.id}/records/").status_code, status.HTTP_200_OK)

    def test_preview_returns_safe_resolution_semantics_in_deterministic_order(self):
        self.record(
            "a-auto",
            resolution_method=ImportRecord.ResolutionMethod.AUTO_MATCH,
            resolution_reason="UNIQUE_EMAIL_MATCH",
            resolved_person=self.person,
        )
        self.record("b-new", resolution_method=ImportRecord.ResolutionMethod.NO_MATCH, resolution_reason="NO_STRONG_CANDIDATE")
        self.record(
            "c-staff-match",
            resolution_method=ImportRecord.ResolutionMethod.STAFF_MATCH,
            resolution_reason="STAFF_CONFIRMED_SAME_PERSON",
            resolved_person=self.person,
            reviewed_at=timezone.now(),
        )
        self.record(
            "d-staff-new",
            resolution_method=ImportRecord.ResolutionMethod.STAFF_CREATE_NEW,
            resolution_reason="STAFF_CONFIRMED_DIFFERENT_PERSON",
        )
        self.record("e-review", status=ImportRecord.Status.REVIEW_REQUIRED, resolution_method=ImportRecord.ResolutionMethod.NOT_RESOLVED)
        self.record("f-invalid", status=ImportRecord.Status.INVALID)
        self.record("g-committed", status=ImportRecord.Status.COMMITTED, committed_at=timezone.now())
        other_batch = ImportBatch.objects.create(source_type=ImportBatch.SourceType.MEMBERSHIP_FORM, source_filename="other.xlsx", source_fingerprint="b" * 64)
        ImportRecord.objects.create(batch=other_batch, source_row_identifier="other", source_fingerprint="o" * 64)

        before_people = Person.objects.count()
        before_memberships = Membership.objects.count()
        before_profiles = ProfessionalProfile.objects.count()
        self.authenticate_as(self.admin)
        response = self.client.get(f"/api/v1/imports/{self.batch.id}/records/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 7)
        records = response.data["results"]
        self.assertEqual([record["source_row_identifier"] for record in records], ["a-auto", "b-new", "c-staff-match", "d-staff-new", "e-review", "f-invalid", "g-committed"])
        auto_match = records[0]
        self.assertEqual(auto_match["resolution_method"], ImportRecord.ResolutionMethod.AUTO_MATCH)
        self.assertEqual(auto_match["resolved_person"]["id"], self.person.id)
        self.assertEqual(records[1]["resolution_method"], ImportRecord.ResolutionMethod.NO_MATCH)
        self.assertIsNone(records[1]["resolved_person"])
        self.assertEqual(records[2]["resolution_method"], ImportRecord.ResolutionMethod.STAFF_MATCH)
        self.assertEqual(records[3]["resolution_method"], ImportRecord.ResolutionMethod.STAFF_CREATE_NEW)
        self.assertEqual(records[4]["status"], ImportRecord.Status.REVIEW_REQUIRED)
        self.assertEqual(records[5]["status"], ImportRecord.Status.INVALID)
        self.assertEqual(records[5]["validation_errors"], [])
        self.assertEqual(records[6]["status"], ImportRecord.Status.COMMITTED)
        self.assertIn("source", auto_match)
        self.assertNotIn("raw_data", auto_match)
        self.assertNotIn("normalized_data", auto_match)
        self.assertEqual(Person.objects.count(), before_people)
        self.assertEqual(Membership.objects.count(), before_memberships)
        self.assertEqual(ProfessionalProfile.objects.count(), before_profiles)

    def test_preview_exposes_only_safe_structured_validation_errors(self):
        self.record(
            "invalid-row",
            status=ImportRecord.Status.INVALID,
            validation_errors=[
                {"field": "age_range", "code": "unsupported_age_range", "message": "internal: 46 - 50"},
                {"field": "gender", "code": "unsupported_gender", "message": "internal: Prefer not to say"},
                {"field": "email", "code": "invalid_email", "message": "internal validation exception"},
                {"field": "linkedin_url", "code": "invalid_url", "message": "internal URL validator exception"},
                {"field": "mobile", "code": "unexpected", "message": "must never be exposed"},
            ],
        )
        self.authenticate_as(self.admin)

        response = self.client.get(f"/api/v1/imports/{self.batch.id}/records/")

        record = response.data["results"][0]
        self.assertEqual(
            record["validation_errors"],
            [
                {"field": "age_range", "code": "unsupported_age_range", "message": "Age range is not supported."},
                {"field": "gender", "code": "unsupported_gender", "message": "Gender is not supported."},
                {"field": "email", "code": "invalid_email", "message": "Email address is not valid."},
                {"field": "linkedin_url", "code": "invalid_url", "message": "LinkedIn URL is not valid."},
            ],
        )
        self.assertNotIn("raw_data", record)
        self.assertNotIn("normalized_data", record)
        self.assertNotIn("internal", str(record))
        self.assertNotIn("must never be exposed", str(record))

    def test_batch_counts_distinguish_existing_matches_from_future_new_people(self):
        self.record("auto", resolution_method=ImportRecord.ResolutionMethod.AUTO_MATCH, resolved_person=self.person)
        self.record("staff-match", resolution_method=ImportRecord.ResolutionMethod.STAFF_MATCH, resolved_person=self.person)
        self.record("no-match", resolution_method=ImportRecord.ResolutionMethod.NO_MATCH)
        self.record("staff-new", resolution_method=ImportRecord.ResolutionMethod.STAFF_CREATE_NEW)
        self.record("review", status=ImportRecord.Status.REVIEW_REQUIRED, resolution_method=ImportRecord.ResolutionMethod.NOT_RESOLVED)
        self.record("invalid", status=ImportRecord.Status.INVALID)
        self.record("committed", status=ImportRecord.Status.COMMITTED, committed_at=timezone.now())
        self.authenticate_as(self.admin)
        response = self.client.get(f"/api/v1/imports/{self.batch.id}/")
        self.assertEqual(response.data["total_count"], 7)
        self.assertEqual(response.data["resolved_count"], 4)
        self.assertEqual(response.data["auto_match_count"], 2)
        self.assertEqual(response.data["new_person_count"], 2)
        self.assertEqual(response.data["review_required_count"], 1)
        self.assertEqual(response.data["invalid_count"], 1)
        self.assertEqual(response.data["committed_count"], 1)

    def test_preview_uses_existing_page_and_page_size_conventions(self):
        for number in range(26):
            self.record(f"row-{number:02d}", resolution_method=ImportRecord.ResolutionMethod.NO_MATCH)
        self.authenticate_as(self.admin)
        response = self.client.get(f"/api/v1/imports/{self.batch.id}/records/?page_size=25")
        self.assertEqual(response.data["count"], 26)
        self.assertEqual(len(response.data["results"]), 25)
        self.assertIsNotNone(response.data["next"])
