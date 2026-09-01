from django.urls import resolve
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import User
from audit.models import AuditEvent
from data_imports.models import ImportBatch, ImportRecord
from memberships.models import Membership
from people.models import Person
from staff_access.models import StaffRole, StaffRoleAssignment


class MembershipFormImportApiTests(APITestCase):
    def setUp(self):
        self.admin = self.create_user("admin@example.com")
        self.manager = self.create_user("manager@example.com")
        self.viewer = self.create_user("viewer@example.com")
        self.non_staff = self.create_user("member@example.com")
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

    def create_batch(self, *, status=ImportBatch.Status.READY_FOR_IMPORT, source_type=ImportBatch.SourceType.MEMBERSHIP_FORM):
        return ImportBatch.objects.create(
            source_type=source_type,
            source_filename="membership.xlsx",
            source_fingerprint=f"batch-{ImportBatch.objects.count() + 1}".ljust(64, "0"),
            status=status,
        )

    def create_record(self, batch, *, method=ImportRecord.ResolutionMethod.NO_MATCH, person=None, status=ImportRecord.Status.RESOLVED):
        sequence = ImportRecord.objects.count() + 1
        return ImportRecord.objects.create(
            batch=batch,
            source_row_identifier=f"row-{sequence}",
            source_fingerprint=str(sequence).zfill(64),
            status=status,
            resolution_method=method,
            resolved_person=person,
            raw_data={"private_source_value": "must-not-be-returned"},
            normalized_data={
                "source_timestamp": "2024-04-12T09:30:00Z",
                "first_name": "Source",
                "last_name": f"Person {sequence}",
                "email": f"sensitive-{sequence}@example.com",
                "mobile": "0791234567",
            },
        )

    @staticmethod
    def import_url(batch):
        return f"/api/v1/imports/{batch.id}/import/"

    def authenticate_as(self, user):
        self.client.force_authenticate(user=user)

    def test_import_url_resolves(self):
        self.assertEqual(resolve("/api/v1/imports/1/import/").url_name, "import-batch-import")

    def test_crm_admin_imports_ready_batch_with_safe_summary_and_actor_provenance(self):
        batch = self.create_batch()
        self.create_record(batch)
        self.create_record(batch, status=ImportRecord.Status.INVALID)
        self.authenticate_as(self.admin)
        people_before = Person.objects.count()
        memberships_before = Membership.objects.count()

        response = self.client.post(self.import_url(batch), {}, format="json")

        batch.refresh_from_db()
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["batch"]["status"], ImportBatch.Status.IMPORTED)
        self.assertEqual(batch.status, ImportBatch.Status.IMPORTED)
        self.assertEqual(
            response.data["result"],
            {
                "processed_count": 1,
                "people_created_count": 1,
                "people_matched_count": 0,
                "people_enriched_count": 0,
                "memberships_created_count": 1,
                "memberships_reused_count": 0,
                "profiles_created_count": 0,
                "profiles_enriched_count": 0,
                "skipped_count": 1,
            },
        )
        self.assertEqual(Person.objects.count(), people_before + 1)
        self.assertEqual(Membership.objects.count(), memberships_before + 1)
        event = AuditEvent.objects.get(action=AuditEvent.Action.IMPORT_BATCH_IMPORTED, entity_id=str(batch.id))
        self.assertEqual(event.actor_user, self.admin)
        self.assertNotIn("raw_data", response.data)
        self.assertNotIn("must-not-be-returned", str(response.data))
        self.assertNotIn("sensitive-1@example.com", str(response.data))

    def test_only_crm_admin_can_import(self):
        batch = self.create_batch()
        self.create_record(batch)

        for user in (self.manager, self.viewer, self.non_staff, self.superuser):
            with self.subTest(user=user.email):
                self.authenticate_as(user)
                self.assertEqual(self.client.post(self.import_url(batch), {}, format="json").status_code, status.HTTP_403_FORBIDDEN)

        self.client.force_authenticate(user=None)
        self.assertEqual(self.client.post(self.import_url(batch), {}, format="json").status_code, status.HTTP_401_UNAUTHORIZED)

    def test_missing_batch_returns_not_found(self):
        self.authenticate_as(self.admin)

        response = self.client.post("/api/v1/imports/999999/import/", {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_non_importable_batch_states_return_safe_conflict(self):
        self.authenticate_as(self.admin)
        for batch_status in (
            ImportBatch.Status.PROCESSING,
            ImportBatch.Status.READY_FOR_REVIEW,
            ImportBatch.Status.FAILED,
            ImportBatch.Status.IMPORTED,
        ):
            with self.subTest(batch_status=batch_status):
                batch = self.create_batch(status=batch_status)
                response = self.client.post(self.import_url(batch), {}, format="json")
                self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
                self.assertNotIn("membership.xlsx", str(response.data))

    def test_non_membership_form_batch_returns_conflict(self):
        batch = self.create_batch(source_type=ImportBatch.SourceType.EVENTBRITE)
        self.authenticate_as(self.admin)

        self.assertEqual(self.client.post(self.import_url(batch), {}, format="json").status_code, status.HTTP_409_CONFLICT)

    def test_former_membership_conflict_returns_safe_conflict_without_new_mutations(self):
        batch = self.create_batch()
        person = Person.objects.create(first_name="Former", last_name="Member")
        Membership.objects.create(
            person=person,
            status=Membership.Status.FORMER,
            joined_at="2020-01-01",
            ended_at="2021-01-01",
            membership_source=Membership.Source.STAFF,
        )
        self.create_record(batch, method=ImportRecord.ResolutionMethod.AUTO_MATCH, person=person)
        self.authenticate_as(self.admin)

        response = self.client.post(self.import_url(batch), {}, format="json")

        batch.refresh_from_db()
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(batch.status, ImportBatch.Status.READY_FOR_IMPORT)
        self.assertEqual(Membership.objects.count(), 1)
        self.assertNotIn("Former", str(response.data))

    def test_inconsistent_resolution_state_returns_safe_conflict(self):
        batch = self.create_batch()
        self.create_record(batch, method=ImportRecord.ResolutionMethod.NOT_RESOLVED)
        self.authenticate_as(self.admin)

        response = self.client.post(self.import_url(batch), {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertNotIn("sensitive-1@example.com", str(response.data))

    def test_repeat_post_cannot_duplicate_authoritative_data(self):
        batch = self.create_batch()
        self.create_record(batch)
        self.authenticate_as(self.admin)
        people_before = Person.objects.count()
        memberships_before = Membership.objects.count()

        first = self.client.post(self.import_url(batch), {}, format="json")
        second = self.client.post(self.import_url(batch), {}, format="json")

        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(second.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(Person.objects.count(), people_before + 1)
        self.assertEqual(Membership.objects.count(), memberships_before + 1)
