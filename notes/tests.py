from unittest.mock import patch

from django.contrib.admin.sites import AdminSite
from django.core.exceptions import ValidationError
from django.db.models import ProtectedError
from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from audit.models import AuditEvent
from notes.admin import InternalNoteAdmin
from notes.models import InternalNote, InternalNoteImmutableDeleteError
from people.models import Person
from staff_access.models import StaffRole, StaffRoleAssignment


class InternalNoteModelTests(TestCase):
    def setUp(self):
        self.person = Person.objects.create(first_name="Amina", last_name="Zulu")
        self.author = User.objects.create_user(
            email="author@example.com",
            password="testpass123",
            person_first_name="Author",
            person_last_name="User",
        )

    def test_create_active_note(self):
        note = InternalNote.objects.create(
            person=self.person,
            body="Initial internal note.",
            created_by=self.author,
        )

        self.assertIsNone(note.archived_at)
        self.assertIsNone(note.archived_by)
        self.assertEqual(note.archive_reason, "")

    def test_archived_note_requires_archived_by(self):
        note = InternalNote(
            person=self.person,
            body="Archive me.",
            created_by=self.author,
            archived_at=timezone.now(),
        )

        with self.assertRaises(ValidationError):
            note.full_clean()

    def test_active_note_rejects_stale_archived_by(self):
        note = InternalNote(
            person=self.person,
            body="Active note.",
            created_by=self.author,
            archived_by=self.author,
        )

        with self.assertRaises(ValidationError):
            note.full_clean()

    def test_restore_canonical_state_clears_archive_reason(self):
        note = InternalNote.objects.create(
            person=self.person,
            body="Will be restored.",
            created_by=self.author,
        )
        note.archive(archived_by=self.author, archive_reason="Duplicate context.")

        note.restore()
        note.refresh_from_db()

        self.assertIsNone(note.archived_at)
        self.assertIsNone(note.archived_by)
        self.assertEqual(note.archive_reason, "")

    def test_person_delete_is_protected(self):
        InternalNote.objects.create(
            person=self.person,
            body="Protected person reference.",
            created_by=self.author,
        )

        with self.assertRaises(ProtectedError):
            self.person.delete()

    def test_created_by_delete_is_protected(self):
        InternalNote.objects.create(
            person=self.person,
            body="Protected author reference.",
            created_by=self.author,
        )

        with self.assertRaises(ProtectedError):
            self.author.delete()

    def test_hard_delete_is_blocked(self):
        note = InternalNote.objects.create(
            person=self.person,
            body="Do not delete me.",
            created_by=self.author,
        )

        with self.assertRaises(InternalNoteImmutableDeleteError):
            note.delete()
        with self.assertRaises(InternalNoteImmutableDeleteError):
            InternalNote.objects.filter(pk=note.pk).delete()


class InternalNoteAdminTests(TestCase):
    def setUp(self):
        self.site = AdminSite()
        self.factory = RequestFactory()
        self.admin = InternalNoteAdmin(InternalNote, self.site)
        self.superuser = User.objects.create_superuser(
            email="super@example.com",
            password="testpass123",
            person_first_name="Super",
            person_last_name="User",
        )

    def test_admin_is_read_only(self):
        request = self.factory.get("/admin/")
        request.user = self.superuser

        self.assertFalse(self.admin.has_add_permission(request))
        self.assertFalse(self.admin.has_change_permission(request))
        self.assertFalse(self.admin.has_delete_permission(request))
        self.assertTrue(self.admin.has_view_permission(request))


@override_settings(ROOT_URLCONF="config.urls")
class InternalNoteApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.site = AdminSite()

        self.list_url_template = "/api/v1/people/{person_id}/notes/"
        self.detail_url_template = "/api/v1/people/{person_id}/notes/{note_id}/"
        self.archive_url_template = "/api/v1/people/{person_id}/notes/{note_id}/archive/"
        self.restore_url_template = "/api/v1/people/{person_id}/notes/{note_id}/restore/"

        self.non_staff_user = User.objects.create_user(
            email="nonstaff-notes@example.com",
            password="testpass123",
            person_first_name="Non",
            person_last_name="Staff",
        )
        self.admin_user = User.objects.create_user(
            email="admin-notes@example.com",
            password="testpass123",
            person_first_name="Admin",
            person_last_name="User",
        )
        self.manager_user = User.objects.create_user(
            email="manager-notes@example.com",
            password="testpass123",
            person_first_name="Manager",
            person_last_name="User",
        )
        self.viewer_user = User.objects.create_user(
            email="viewer-notes@example.com",
            password="testpass123",
            person_first_name="Viewer",
            person_last_name="User",
        )
        self.other_author = User.objects.create_user(
            email="other-author@example.com",
            password="testpass123",
            person_first_name="Other",
            person_last_name="Author",
        )

        admin_role = StaffRole.objects.get(code=StaffRole.CRM_ADMIN)
        manager_role = StaffRole.objects.get(code=StaffRole.CRM_MANAGER)
        viewer_role = StaffRole.objects.get(code=StaffRole.CRM_VIEWER)

        StaffRoleAssignment.objects.assign_role(user=self.admin_user, role=admin_role)
        StaffRoleAssignment.objects.assign_role(user=self.manager_user, role=manager_role)
        StaffRoleAssignment.objects.assign_role(user=self.viewer_user, role=viewer_role)

        self.active_business_person = Person.objects.create(first_name="Amina", last_name="Zulu")
        self.archived_business_person = Person.objects.create(
            first_name="Archived",
            last_name="Business",
            archived_at=timezone.now(),
        )
        self.technical_person = Person.objects.create(
            first_name="Root",
            last_name="Operator",
            record_type=Person.RecordType.TECHNICAL,
        )

        self.active_note = InternalNote.objects.create(
            person=self.active_business_person,
            body="First active note.",
            created_by=self.other_author,
        )
        self.archived_note = InternalNote.objects.create(
            person=self.active_business_person,
            body="Archived note.",
            created_by=self.other_author,
        )
        self.archived_note.archive(archived_by=self.admin_user, archive_reason="Resolved")
        self.other_person = Person.objects.create(first_name="Other", last_name="Person")
        self.other_person_note = InternalNote.objects.create(
            person=self.other_person,
            body="Different person note.",
            created_by=self.other_author,
        )

    def authenticate(self, user):
        self.client.force_authenticate(user=user)

    def get_list_url(self, person_id):
        return self.list_url_template.format(person_id=person_id)

    def get_detail_url(self, person_id, note_id):
        return self.detail_url_template.format(person_id=person_id, note_id=note_id)

    def get_archive_url(self, person_id, note_id):
        return self.archive_url_template.format(person_id=person_id, note_id=note_id)

    def get_restore_url(self, person_id, note_id):
        return self.restore_url_template.format(person_id=person_id, note_id=note_id)

    def test_admin_can_list_notes(self):
        self.authenticate(self.admin_user)
        response = self.client.get(self.get_list_url(self.active_business_person.id))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["id"], self.active_note.id)

    def test_manager_can_list_notes(self):
        self.authenticate(self.manager_user)
        response = self.client.get(self.get_list_url(self.active_business_person.id))
        self.assertEqual(response.status_code, 200)

    def test_viewer_cannot_access_notes_collection_or_mutations(self):
        self.authenticate(self.viewer_user)

        list_response = self.client.get(self.get_list_url(self.active_business_person.id))
        create_response = self.client.post(
            self.get_list_url(self.active_business_person.id),
            {"body": "Blocked"},
            format="json",
        )
        update_response = self.client.patch(
            self.get_detail_url(self.active_business_person.id, self.active_note.id),
            {"body": "Blocked"},
            format="json",
        )
        archive_response = self.client.post(
            self.get_archive_url(self.active_business_person.id, self.active_note.id),
            {"archive_reason": "Blocked"},
            format="json",
        )
        restore_response = self.client.post(
            self.get_restore_url(self.active_business_person.id, self.archived_note.id),
            {},
            format="json",
        )

        self.assertEqual(list_response.status_code, 403)
        self.assertEqual(create_response.status_code, 403)
        self.assertEqual(update_response.status_code, 403)
        self.assertEqual(archive_response.status_code, 403)
        self.assertEqual(restore_response.status_code, 403)

    def test_nonstaff_receives_403_and_anonymous_receives_401(self):
        anonymous_response = self.client.get(self.get_list_url(self.active_business_person.id))
        self.authenticate(self.non_staff_user)
        nonstaff_response = self.client.get(self.get_list_url(self.active_business_person.id))

        self.assertEqual(anonymous_response.status_code, 401)
        self.assertEqual(nonstaff_response.status_code, 403)

    def test_archived_business_person_notes_remain_readable(self):
        archived_person_note = InternalNote.objects.create(
            person=self.archived_business_person,
            body="Archived person note.",
            created_by=self.other_author,
        )

        self.authenticate(self.admin_user)
        response = self.client.get(self.get_list_url(self.archived_business_person.id))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["results"][0]["id"], archived_person_note.id)

    def test_technical_person_returns_404(self):
        self.authenticate(self.admin_user)
        response = self.client.get(self.get_list_url(self.technical_person.id))
        self.assertEqual(response.status_code, 404)

    def test_record_state_filters_and_pagination_shape(self):
        older_note = InternalNote.objects.create(
            person=self.active_business_person,
            body="Older active note.",
            created_by=self.other_author,
        )
        InternalNote.objects.filter(pk=older_note.pk).update(
            created_at=timezone.now() - timezone.timedelta(days=1)
        )
        older_note.refresh_from_db()

        self.authenticate(self.admin_user)
        active_response = self.client.get(self.get_list_url(self.active_business_person.id))
        archived_response = self.client.get(
            self.get_list_url(self.active_business_person.id),
            {"record_state": "archived"},
        )
        all_response = self.client.get(
            self.get_list_url(self.active_business_person.id),
            {"record_state": "all"},
        )

        self.assertEqual(active_response.status_code, 200)
        self.assertEqual(set(active_response.data.keys()), {"count", "next", "previous", "results"})
        self.assertEqual([item["id"] for item in active_response.data["results"]], [self.active_note.id, older_note.id])
        self.assertEqual([item["id"] for item in archived_response.data["results"]], [self.archived_note.id])
        self.assertEqual(all_response.data["count"], 3)

    def test_invalid_record_state_returns_400(self):
        self.authenticate(self.admin_user)
        response = self.client.get(
            self.get_list_url(self.active_business_person.id),
            {"record_state": "invalid"},
        )
        self.assertEqual(response.status_code, 400)

    def test_create_note_sets_created_by_and_active_state_and_audits_without_body(self):
        self.authenticate(self.admin_user)
        response = self.client.post(
            self.get_list_url(self.active_business_person.id),
            {"body": "  Staff-authored context.  "},
            format="json",
        )

        note = InternalNote.objects.get(person=self.active_business_person, body="Staff-authored context.")
        event = AuditEvent.objects.get(action=AuditEvent.Action.NOTE_CREATED)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(note.created_by, self.admin_user)
        self.assertIsNone(note.archived_at)
        self.assertIsNone(note.archived_by)
        self.assertEqual(note.archive_reason, "")
        self.assertEqual(event.actor_user, self.admin_user)
        self.assertEqual(event.entity_type, "InternalNote")
        self.assertEqual(event.entity_id, str(note.id))
        self.assertEqual(event.metadata, {"person_id": str(self.active_business_person.id)})
        self.assertEqual(event.changes, {"created": {"from": False, "to": True}})
        self.assertNotIn("body", str(event.metadata))
        self.assertNotIn("body", event.changes)

    def test_create_rejects_unknown_or_immutable_fields_and_whitespace_body(self):
        self.authenticate(self.admin_user)
        immutable_response = self.client.post(
            self.get_list_url(self.active_business_person.id),
            {"body": "Valid", "created_by": self.admin_user.id},
            format="json",
        )
        blank_response = self.client.post(
            self.get_list_url(self.active_business_person.id),
            {"body": "   "},
            format="json",
        )

        self.assertEqual(immutable_response.status_code, 400)
        self.assertEqual(blank_response.status_code, 400)
        self.assertEqual(AuditEvent.objects.filter(action=AuditEvent.Action.NOTE_CREATED).count(), 0)

    def test_create_rejects_archived_business_person(self):
        self.authenticate(self.manager_user)
        response = self.client.post(
            self.get_list_url(self.archived_business_person.id),
            {"body": "Blocked on archived person."},
            format="json",
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(AuditEvent.objects.filter(action=AuditEvent.Action.NOTE_CREATED).count(), 0)

    def test_note_create_rolls_back_when_audit_write_fails(self):
        self.authenticate(self.admin_user)
        with patch("notes.views.record_audit_event", side_effect=RuntimeError("audit down")):
            response = self.client.post(
                self.get_list_url(self.active_business_person.id),
                {"body": "Rollback me."},
                format="json",
            )

        self.assertEqual(response.status_code, 500)
        self.assertFalse(InternalNote.objects.filter(person=self.active_business_person, body="Rollback me.").exists())
        self.assertEqual(AuditEvent.objects.filter(action=AuditEvent.Action.NOTE_CREATED).count(), 0)

    def test_admin_can_edit_another_authors_note_and_created_by_remains_unchanged(self):
        self.authenticate(self.admin_user)
        response = self.client.patch(
            self.get_detail_url(self.active_business_person.id, self.active_note.id),
            {"body": "Updated sensitive note."},
            format="json",
        )

        self.active_note.refresh_from_db()
        event = AuditEvent.objects.get(action=AuditEvent.Action.NOTE_UPDATED)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.active_note.body, "Updated sensitive note.")
        self.assertEqual(self.active_note.created_by, self.other_author)
        self.assertEqual(event.metadata, {"person_id": str(self.active_business_person.id), "body_changed": True})
        self.assertEqual(event.changes, {"body": {"changed": True}})
        self.assertNotIn("Updated sensitive note.", str(event.changes))

    def test_no_op_edit_emits_no_update_event(self):
        self.authenticate(self.manager_user)
        response = self.client.patch(
            self.get_detail_url(self.active_business_person.id, self.active_note.id),
            {"body": self.active_note.body},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(AuditEvent.objects.filter(action=AuditEvent.Action.NOTE_UPDATED).count(), 0)

    def test_edit_conflicts_and_cross_person_access(self):
        self.authenticate(self.admin_user)
        archived_response = self.client.patch(
            self.get_detail_url(self.active_business_person.id, self.archived_note.id),
            {"body": "Nope"},
            format="json",
        )
        cross_person_response = self.client.patch(
            self.get_detail_url(self.active_business_person.id, self.other_person_note.id),
            {"body": "Nope"},
            format="json",
        )
        archived_person_response = self.client.patch(
            self.get_detail_url(self.archived_business_person.id, self.active_note.id),
            {"body": "Nope"},
            format="json",
        )

        self.assertEqual(archived_response.status_code, 409)
        self.assertEqual(cross_person_response.status_code, 404)
        self.assertEqual(archived_person_response.status_code, 409)
        self.assertEqual(AuditEvent.objects.filter(action=AuditEvent.Action.NOTE_UPDATED).count(), 0)

    def test_note_update_rolls_back_when_audit_write_fails(self):
        self.authenticate(self.admin_user)
        original_body = self.active_note.body
        with patch("notes.views.record_audit_event", side_effect=RuntimeError("audit down")):
            response = self.client.patch(
                self.get_detail_url(self.active_business_person.id, self.active_note.id),
                {"body": "Should rollback"},
                format="json",
            )

        self.active_note.refresh_from_db()
        self.assertEqual(response.status_code, 500)
        self.assertEqual(self.active_note.body, original_body)
        self.assertEqual(AuditEvent.objects.filter(action=AuditEvent.Action.NOTE_UPDATED).count(), 0)

    def test_archive_persists_reason_and_audits_without_reason_or_body(self):
        self.authenticate(self.manager_user)
        response = self.client.post(
            self.get_archive_url(self.active_business_person.id, self.active_note.id),
            {"archive_reason": "Superseded"},
            format="json",
        )

        self.active_note.refresh_from_db()
        event = AuditEvent.objects.get(action=AuditEvent.Action.NOTE_ARCHIVED)
        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(self.active_note.archived_at)
        self.assertEqual(self.active_note.archived_by, self.manager_user)
        self.assertEqual(self.active_note.archive_reason, "Superseded")
        self.assertEqual(event.changes, {"archived": {"from": False, "to": True}})
        self.assertEqual(event.metadata, {"person_id": str(self.active_business_person.id)})
        self.assertNotIn("Superseded", str(event.metadata))
        self.assertNotIn("Superseded", str(event.changes))

    def test_archive_conflicts_and_unexpected_fields_emit_no_event(self):
        self.authenticate(self.admin_user)
        already_archived_response = self.client.post(
            self.get_archive_url(self.active_business_person.id, self.archived_note.id),
            {},
            format="json",
        )
        invalid_field_response = self.client.post(
            self.get_archive_url(self.active_business_person.id, self.active_note.id),
            {"body": "invalid"},
            format="json",
        )

        self.assertEqual(already_archived_response.status_code, 409)
        self.assertEqual(invalid_field_response.status_code, 400)
        self.assertEqual(AuditEvent.objects.filter(action=AuditEvent.Action.NOTE_ARCHIVED).count(), 0)

    def test_note_archive_rolls_back_when_audit_write_fails(self):
        self.authenticate(self.admin_user)
        with patch("notes.views.record_audit_event", side_effect=RuntimeError("audit down")):
            response = self.client.post(
                self.get_archive_url(self.active_business_person.id, self.active_note.id),
                {"archive_reason": "Rollback"},
                format="json",
            )

        self.active_note.refresh_from_db()
        self.assertEqual(response.status_code, 500)
        self.assertIsNone(self.active_note.archived_at)
        self.assertIsNone(self.active_note.archived_by)
        self.assertEqual(self.active_note.archive_reason, "")
        self.assertEqual(AuditEvent.objects.filter(action=AuditEvent.Action.NOTE_ARCHIVED).count(), 0)

    def test_restore_clears_archive_fields_and_audits(self):
        self.authenticate(self.admin_user)
        response = self.client.post(
            self.get_restore_url(self.active_business_person.id, self.archived_note.id),
            {},
            format="json",
        )

        self.archived_note.refresh_from_db()
        event = AuditEvent.objects.get(action=AuditEvent.Action.NOTE_RESTORED)
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(self.archived_note.archived_at)
        self.assertIsNone(self.archived_note.archived_by)
        self.assertEqual(self.archived_note.archive_reason, "")
        self.assertEqual(event.changes, {"archived": {"from": True, "to": False}})
        self.assertEqual(event.metadata, {"person_id": str(self.active_business_person.id)})

    def test_restore_conflicts_and_preserves_no_delete_api_shape(self):
        self.authenticate(self.admin_user)
        already_active_response = self.client.post(
            self.get_restore_url(self.active_business_person.id, self.active_note.id),
            {},
            format="json",
        )
        unexpected_body_response = self.client.post(
            self.get_restore_url(self.active_business_person.id, self.archived_note.id),
            {"archive_reason": "invalid"},
            format="json",
        )
        delete_response = self.client.delete(
            self.get_detail_url(self.active_business_person.id, self.active_note.id)
        )

        self.assertEqual(already_active_response.status_code, 409)
        self.assertEqual(unexpected_body_response.status_code, 400)
        self.assertEqual(delete_response.status_code, 405)
        self.assertEqual(AuditEvent.objects.filter(action=AuditEvent.Action.NOTE_RESTORED).count(), 0)

    def test_note_restore_rolls_back_when_audit_write_fails(self):
        self.authenticate(self.admin_user)
        archived_at = self.archived_note.archived_at
        archived_by = self.archived_note.archived_by
        archive_reason = self.archived_note.archive_reason

        with patch("notes.views.record_audit_event", side_effect=RuntimeError("audit down")):
            response = self.client.post(
                self.get_restore_url(self.active_business_person.id, self.archived_note.id),
                {},
                format="json",
            )

        self.archived_note.refresh_from_db()
        self.assertEqual(response.status_code, 500)
        self.assertEqual(self.archived_note.archived_at, archived_at)
        self.assertEqual(self.archived_note.archived_by, archived_by)
        self.assertEqual(self.archived_note.archive_reason, archive_reason)
        self.assertEqual(AuditEvent.objects.filter(action=AuditEvent.Action.NOTE_RESTORED).count(), 0)
