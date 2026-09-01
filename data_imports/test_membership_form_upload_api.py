from io import BytesIO

from django.urls import resolve
from openpyxl import Workbook
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import User
from data_imports.adapters.membership_form import REQUIRED_HEADERS, SHEET_NAME
from data_imports.models import ImportBatch, ImportRecord
from memberships.models import Membership
from people.models import Person
from professional_profiles.models import ProfessionalProfile
from staff_access.models import StaffRole, StaffRoleAssignment


class MembershipFormUploadApiTests(APITestCase):
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

    @staticmethod
    def create_user(email, **extra_fields):
        return User.objects.create_user(
            email=email,
            password="safe-password",
            person_first_name=email.split("@")[0],
            person_last_name="User",
            **extra_fields,
        )

    def workbook_file(self, rows=(), filename="membership.xlsx", headers=REQUIRED_HEADERS):
        workbook = Workbook()
        worksheet = workbook.active
        worksheet.title = SHEET_NAME
        worksheet.append(headers)
        for row in rows:
            worksheet.append(row)
        content = BytesIO()
        workbook.save(content)
        content.seek(0)
        content.name = filename
        return content

    @staticmethod
    def membership_row(*, email="source@example.com", mobile="0790000000"):
        return [
            "2026-09-01T10:00:00Z", "Source", "Member", "", "", email, mobile,
            "Milton Keynes", "Technology", "Engineer", "https://www.linkedin.com/in/source",
        ]

    def upload(self, file):
        return self.client.post("/api/v1/imports/membership-form/", {"file": file}, format="multipart")

    def test_upload_url_resolves(self):
        self.assertEqual(resolve("/api/v1/imports/membership-form/").url_name, "import-membership-form-upload")

    def test_only_crm_admin_can_upload(self):
        for user in (self.manager, self.viewer, self.superuser):
            self.client.force_authenticate(user=user)
            self.assertEqual(self.upload(self.workbook_file()).status_code, status.HTTP_403_FORBIDDEN)
        self.client.force_authenticate(user=self.admin)
        self.assertEqual(self.upload(self.workbook_file()).status_code, status.HTTP_201_CREATED)

    def test_missing_empty_and_non_xlsx_files_are_rejected(self):
        self.client.force_authenticate(user=self.admin)
        self.assertEqual(self.client.post("/api/v1/imports/membership-form/", {}, format="multipart").status_code, status.HTTP_400_BAD_REQUEST)
        empty = BytesIO(b""); empty.name = "empty.xlsx"
        self.assertEqual(self.upload(empty).status_code, status.HTTP_400_BAD_REQUEST)
        text = BytesIO(b"not an xlsx"); text.name = "members.csv"
        self.assertEqual(self.upload(text).status_code, status.HTTP_400_BAD_REQUEST)

    def test_corrupt_and_structurally_invalid_workbooks_fail_safely(self):
        self.client.force_authenticate(user=self.admin)
        corrupt = BytesIO(b"not a zip workbook"); corrupt.name = "bad.xlsx"
        self.assertEqual(self.upload(corrupt).status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(ImportBatch.objects.get(source_filename="bad.xlsx").status, ImportBatch.Status.FAILED)
        response = self.upload(self.workbook_file(headers=("Timestamp",)))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(ImportBatch.objects.latest("id").status, ImportBatch.Status.FAILED)

    def test_upload_reuses_staging_and_analysis_without_authoritative_mutation(self):
        existing_person = Person.objects.create(
            first_name="Source", last_name="Member", primary_email="source@example.com", mobile="0790000000"
        )
        before_people = Person.objects.count()
        before_memberships = Membership.objects.count()
        before_profiles = ProfessionalProfile.objects.count()
        self.client.force_authenticate(user=self.admin)
        response = self.upload(self.workbook_file(rows=[self.membership_row()]))
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        batch = ImportBatch.objects.get(pk=response.data["id"])
        record = batch.records.get()
        self.assertEqual(batch.source_type, ImportBatch.SourceType.MEMBERSHIP_FORM)
        self.assertEqual(batch.source_filename, "membership.xlsx")
        self.assertEqual(batch.status, ImportBatch.Status.ANALYZED)
        self.assertEqual(record.status, ImportRecord.Status.RESOLVED)
        self.assertEqual(record.resolved_person, existing_person)
        self.assertEqual(record.resolution_method, ImportRecord.ResolutionMethod.AUTO_MATCH)
        self.assertEqual(Person.objects.count(), before_people)
        self.assertEqual(Membership.objects.count(), before_memberships)
        self.assertEqual(ProfessionalProfile.objects.count(), before_profiles)

    def test_invalid_rows_remain_invalid_and_ambiguous_rows_require_review(self):
        Person.objects.create(first_name="One", last_name="Person", primary_email="duplicate@example.com")
        Person.objects.create(first_name="Two", last_name="Person", mobile="0791111111")
        self.client.force_authenticate(user=self.admin)
        response = self.upload(
            self.workbook_file(
                rows=[
                    self.membership_row(email="not-an-email"),
                    self.membership_row(email="duplicate@example.com", mobile="0791111111"),
                ]
            )
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        batch = ImportBatch.objects.get(pk=response.data["id"])
        self.assertEqual(batch.status, ImportBatch.Status.READY_FOR_REVIEW)
        self.assertEqual(batch.records.filter(status=ImportRecord.Status.INVALID).count(), 1)
        self.assertEqual(batch.records.filter(status=ImportRecord.Status.REVIEW_REQUIRED).count(), 1)

    def test_repeated_upload_creates_a_new_batch(self):
        self.client.force_authenticate(user=self.admin)
        first = self.upload(self.workbook_file())
        second = self.upload(self.workbook_file())
        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        self.assertEqual(second.status_code, status.HTTP_201_CREATED)
        self.assertNotEqual(first.data["id"], second.data["id"])
