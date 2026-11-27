from io import BytesIO
from unittest.mock import patch
from zipfile import ZipFile

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import User
from audit.models import AuditEvent
from people.models import Person
from staff_access.models import CapabilityDefinition, StaffCapabilityAssignment, StaffCapabilityRestriction, StaffRole, StaffRoleAssignment


@override_settings(ROOT_URLCONF="config.urls")
class PersonExportApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_user(
            email="export-admin@example.com", password="testpass123",
            person_first_name="Export", person_last_name="Admin",
            person_record_type=Person.RecordType.STAFF,
        )
        self.viewer = User.objects.create_user(
            email="export-viewer@example.com", password="testpass123",
            person_first_name="Export", person_last_name="Viewer",
            person_record_type=Person.RecordType.STAFF,
        )
        StaffRoleAssignment.objects.assign_role(user=self.admin, role=StaffRole.objects.get(code=StaffRole.CRM_ADMIN))
        StaffRoleAssignment.objects.assign_role(user=self.viewer, role=StaffRole.objects.get(code=StaffRole.CRM_VIEWER))
        self.person = Person.objects.create(
            first_name="=Formula", last_name="Person", primary_email="formula@example.com",
            mobile="+441234567890", age_range="30_39", gender="FEMALE",
        )
        self.technical = Person.objects.create(first_name="Technical", last_name="Only", record_type=Person.RecordType.TECHNICAL)
        self.url = "/api/v1/people/export/"

    def test_admin_csv_export_is_audited_and_formula_safe(self):
        self.client.force_authenticate(self.admin)
        response = self.client.get(self.url, {"format": "csv"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv; charset=utf-8")
        self.assertIn("'=Formula", response.content.decode("utf-8-sig"))
        self.assertNotIn(str(self.technical.id), response.content.decode("utf-8-sig"))
        event = AuditEvent.objects.get(action=AuditEvent.Action.PERSON_EXPORT_GENERATED)
        self.assertEqual(event.metadata["record_count"], 3)
        self.assertEqual(event.metadata["outcome"], "SUCCESS")
        self.assertIn("CONTACT", event.metadata["field_categories"])
        self.assertIn("DEMOGRAPHIC", event.metadata["field_categories"])

    def test_viewer_requires_export_capability(self):
        self.client.force_authenticate(self.viewer)
        response = self.client.get(self.url, {"format": "csv"})

        self.assertEqual(response.status_code, 403)
        self.assertTrue(AuditEvent.objects.filter(action=AuditEvent.Action.PERSON_EXPORT_DENIED).exists())

    def test_export_capability_does_not_grant_contact_or_demographic_fields(self):
        export = CapabilityDefinition.objects.get(code=CapabilityDefinition.PERSON_EXPORT)
        StaffCapabilityAssignment.objects.create(user=self.viewer, capability=export, granted_by=self.admin)
        self.client.force_authenticate(self.viewer)

        response = self.client.get(self.url, {"format": "xlsx"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        self.assertNotIn(b"formula@example.com", response.content)
        self.assertNotIn(b"30_39", response.content)
        with ZipFile(BytesIO(response.content)) as workbook:
            worksheet = workbook.read("xl/worksheets/sheet1.xml")
        self.assertNotIn(b">=Formula<", worksheet)
        self.assertIn(b">'=Formula<", worksheet)

    def test_restriction_overrides_export_assignment(self):
        export = CapabilityDefinition.objects.get(code=CapabilityDefinition.PERSON_EXPORT)
        StaffCapabilityAssignment.objects.create(user=self.viewer, capability=export, granted_by=self.admin)
        StaffCapabilityRestriction.objects.create(user=self.viewer, capability=export, restricted_by=self.admin)
        self.client.force_authenticate(self.viewer)

        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_active_superuser_export_is_audited_as_bypass(self):
        superuser = User.objects.create_superuser(
            email="export-superuser@example.com", password="testpass123",
            person_first_name="Export", person_last_name="Superuser",
            person_record_type=Person.RecordType.STAFF,
        )
        StaffRoleAssignment.objects.assign_role(user=superuser, role=StaffRole.objects.get(code=StaffRole.CRM_ADMIN))
        self.client.force_authenticate(superuser)

        response = self.client.get(self.url, {"format": "csv"})

        self.assertEqual(response.status_code, 200)
        event = AuditEvent.objects.filter(action=AuditEvent.Action.PERSON_EXPORT_GENERATED).latest("id")
        self.assertTrue(event.metadata["superuser_authority"])

    @patch("people.views.record_export_audit", side_effect=RuntimeError("audit unavailable"))
    def test_audit_failure_prevents_export_release(self, _record_export_audit):
        self.client.force_authenticate(self.admin)

        with self.assertRaises(RuntimeError):
            self.client.get(self.url, {"format": "csv"})
