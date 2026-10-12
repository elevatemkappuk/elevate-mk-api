from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

from django.core.cache import cache
from django.db import connection, close_old_connections, transaction
from django.test import TestCase, TransactionTestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from audit.models import AuditEvent
from community.feed import CommunityPostConversationLocked, edit_community_post
from community.models import CommunityContentReport, CommunityPost, CommunityPostReply, CommunityProfile
from community.replies import create_community_reply
from memberships.models import Membership
from people.models import Person
from staff_access.models import StaffRole, StaffRoleAssignment


class CommunityPostManagementApiTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.author = self.member("Amina", "Author", "author@example.com")
        self.viewer = self.member("Ben", "Viewer", "viewer@example.com")
        self.post = self.create_post()
        self.client.force_authenticate(self.author)

    def member(self, first_name, last_name, email):
        person = Person.objects.create(first_name=first_name, last_name=last_name, primary_email=email)
        Membership.objects.create(
            person=person,
            status=Membership.Status.ACTIVE,
            joined_at=timezone.localdate() - timedelta(days=1),
            membership_source=Membership.Source.COMMUNITY_PLATFORM,
        )
        user = User.objects.create_user(email=email, password="Strong-password-123!", person=person)
        CommunityProfile.objects.create(person=person)
        return user

    def create_post(self, *, status=CommunityPost.Status.ACTIVE):
        return CommunityPost.objects.create(
            author=self.author.person,
            purpose=CommunityPost.Purpose.ASK,
            headline="A useful headline",
            body="A useful post body.",
            audience=CommunityPost.Audience.ELEVATE_COMMUNITY,
            status=status,
        )

    def post_url(self, post=None):
        return f"/api/v1/community/posts/{(post or self.post).public_id}/"

    def reply(self, *, status=CommunityPostReply.Status.ACTIVE):
        return CommunityPostReply.objects.create(
            post=self.post,
            author=self.viewer.person,
            body="A useful reply.",
            status=status,
        )

    def test_capabilities_are_explicit_and_reply_history_is_permanent(self):
        own = self.client.get(self.post_url()).data
        self.assertEqual(own["capabilities"], {"can_edit": True, "can_delete": True, "can_edit_purpose": True, "can_edit_audience": True})

        self.reply()
        locked = self.client.get(self.post_url()).data["capabilities"]
        self.assertEqual(locked, {"can_edit": True, "can_delete": True, "can_edit_purpose": False, "can_edit_audience": False})

        self.reply(status=CommunityPostReply.Status.AUTHOR_DELETED)
        self.reply(status=CommunityPostReply.Status.MODERATOR_REMOVED)
        self.assertFalse(self.client.get(self.post_url()).data["capabilities"]["can_edit_purpose"])
        self.assertFalse(self.client.get(self.post_url()).data["capabilities"]["can_edit_audience"])

        self.client.force_authenticate(self.viewer)
        other = self.client.get(self.post_url()).data["capabilities"]
        self.assertEqual(other, {"can_edit": False, "can_delete": False, "can_edit_purpose": False, "can_edit_audience": False})

    def test_feed_capabilities_do_not_add_one_reply_history_query_per_post(self):
        for index in range(5):
            CommunityPost.objects.create(
                author=self.author.person,
                purpose=CommunityPost.Purpose.ASK,
                headline=f"Headline {index}", body="Body", audience=CommunityPost.Audience.ELEVATE_COMMUNITY,
            )
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get("/api/v1/community/posts/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["results"]), 6)
        self.assertLessEqual(len(queries), 12)

    def test_edit_all_fields_before_reply_normalizes_and_audits(self):
        response = self.client.patch(
            self.post_url(),
            {"headline": "  Updated headline  ", "body": "  Updated body  ", "purpose": "OFFER", "audience": "CONNECTIONS"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.post.refresh_from_db()
        self.assertEqual(self.post.headline, "Updated headline")
        self.assertEqual(self.post.body, "Updated body")
        self.assertEqual(self.post.purpose, "OFFER")
        self.assertEqual(self.post.audience, "CONNECTIONS")
        self.assertIsNotNone(self.post.edited_at)
        event = AuditEvent.objects.get(action=AuditEvent.Action.COMMUNITY_POST_EDITED)
        self.assertEqual(event.metadata["changed_fields"], ["headline", "body", "purpose", "audience"])
        self.assertNotIn("Updated headline", str(event.metadata))

    def test_noop_edit_is_quiet_and_locked_fields_are_rejected_after_any_reply(self):
        before = AuditEvent.objects.filter(action=AuditEvent.Action.COMMUNITY_POST_EDITED).count()
        self.assertEqual(self.client.patch(self.post_url(), {"headline": " A useful headline "}, format="json").status_code, 200)
        self.post.refresh_from_db()
        self.assertIsNone(self.post.edited_at)
        self.assertEqual(AuditEvent.objects.filter(action=AuditEvent.Action.COMMUNITY_POST_EDITED).count(), before)

        self.reply(status=CommunityPostReply.Status.AUTHOR_DELETED)
        locked = self.client.patch(self.post_url(), {"purpose": "OFFER", "audience": "CONNECTIONS"}, format="json")
        self.assertEqual(locked.status_code, 400)
        self.assertIn("purpose", locked.data)
        editable = self.client.patch(self.post_url(), {"headline": "New headline", "body": "New body"}, format="json")
        self.assertEqual(editable.status_code, 200)

    def test_edit_validation_and_authorization_are_safe(self):
        valid = {"headline": "Headline", "body": "Body", "purpose": "ASK", "audience": "ELEVATE_COMMUNITY"}
        for payload in [{"unexpected": "value"}, {"headline": "   "}, {"body": "   "}, {"headline": "H" * 121}, {"body": "B" * 2001}, {"purpose": "INVALID"}, {"audience": "INVALID"}]:
            self.assertEqual(self.client.patch(self.post_url(), {**valid, **payload}, format="json").status_code, 400)
        self.client.force_authenticate(self.viewer)
        self.assertEqual(self.client.patch(self.post_url(), valid, format="json").status_code, 404)
        self.author.person.membership.status = Membership.Status.FORMER
        self.author.person.membership.ended_at = timezone.localdate()
        self.author.person.membership.save(update_fields=["status", "ended_at", "updated_at"])
        self.client.force_authenticate(self.author)
        self.assertEqual(self.client.patch(self.post_url(), {"body": "No longer eligible"}, format="json").status_code, 403)
        self.assertEqual(self.client.delete(self.post_url()).status_code, 404)
        self.author.person.membership.status = Membership.Status.ACTIVE
        self.author.person.membership.ended_at = None
        self.author.person.membership.save(update_fields=["status", "ended_at", "updated_at"])
        self.post.status = CommunityPost.Status.AUTHOR_DELETED
        self.post.deleted_at = timezone.now()
        self.post.save(update_fields=["status", "deleted_at", "updated_at"])
        self.client.force_authenticate(self.author)
        self.assertEqual(self.client.patch(self.post_url(), valid, format="json").status_code, 404)

    def test_delete_is_soft_retains_replies_and_reports_and_is_not_repeatable(self):
        reply = self.reply()
        self.client.force_authenticate(self.viewer)
        report = self.client.post(f"{self.post_url()}report/", {"reason": "OTHER"}, format="json")
        self.assertEqual(report.status_code, 201)
        self.client.force_authenticate(self.author)
        self.assertEqual(self.client.delete(self.post_url()).status_code, 204)
        self.post.refresh_from_db()
        self.assertEqual(self.post.status, CommunityPost.Status.AUTHOR_DELETED)
        self.assertIsNotNone(self.post.deleted_at)
        self.assertTrue(CommunityPostReply.objects.filter(pk=reply.pk).exists())
        self.assertTrue(CommunityContentReport.objects.filter(post=self.post).exists())
        self.assertEqual(self.client.delete(self.post_url()).status_code, 404)
        self.assertEqual(AuditEvent.objects.filter(action=AuditEvent.Action.COMMUNITY_POST_DELETED).count(), 1)
        self.assertNotIn(self.post.headline, str(AuditEvent.objects.get(action=AuditEvent.Action.COMMUNITY_POST_DELETED).metadata))
        self.assertEqual(self.client.get("/api/v1/community/posts/").data["results"], [])
        self.assertEqual(self.client.get(self.post_url()).status_code, 404)
        self.assertEqual(self.client.get(f"{self.post_url()}replies/").status_code, 404)
        self.assertEqual(self.client.post(f"{self.post_url()}replies/", {"body": "Unavailable"}, format="json").status_code, 404)
        manager = self.member("Mara", "Manager", "deleted-manager@example.com")
        role, _ = StaffRole.objects.get_or_create(code=StaffRole.CRM_MANAGER, defaults={"name": "CRM Manager"})
        StaffRoleAssignment.objects.create(user=manager, role=role)
        self.client.force_authenticate(manager)
        report_id = str(CommunityContentReport.objects.get(post=self.post).public_id)
        self.assertEqual(self.client.post(f"/api/v1/community/moderation/reports/{report_id}/restore/", {}, format="json").status_code, 409)

    def test_removed_post_cannot_be_author_managed_and_moderator_restoration_still_works(self):
        self.client.force_authenticate(self.viewer)
        report = self.client.post(f"{self.post_url()}report/", {"reason": "OFF_TOPIC"}, format="json")
        manager = self.member("Mara", "Manager", "manager@example.com")
        role, _ = StaffRole.objects.get_or_create(code=StaffRole.CRM_MANAGER, defaults={"name": "CRM Manager"})
        StaffRoleAssignment.objects.create(user=manager, role=role)
        self.client.force_authenticate(manager)
        removed = self.client.post(f"/api/v1/community/moderation/reports/{report.data['report_id']}/remove/", {"resolution": "Removed."}, format="json")
        self.assertEqual(removed.status_code, 200)
        self.client.force_authenticate(self.author)
        self.assertEqual(self.client.patch(self.post_url(), {"body": "No"}, format="json").status_code, 404)
        self.assertEqual(self.client.delete(self.post_url()).status_code, 404)
        self.client.force_authenticate(manager)
        restored = self.client.post(f"/api/v1/community/moderation/reports/{report.data['report_id']}/restore/", {}, format="json")
        self.assertEqual(restored.status_code, 200)
        self.post.refresh_from_db()
        self.assertEqual(self.post.status, CommunityPost.Status.ACTIVE)


class CommunityPostManagementConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        if connection.vendor != "postgresql":
            self.skipTest("PostgreSQL row-lock concurrency is not equivalent on this database.")
        self.author = self.member("Amina", "Author", "race-author@example.com")
        self.viewer = self.member("Ben", "Viewer", "race-viewer@example.com")
        self.post = CommunityPost.objects.create(
            author=self.author.person, purpose=CommunityPost.Purpose.ASK, headline="Headline", body="Body", audience=CommunityPost.Audience.ELEVATE_COMMUNITY,
        )
        self.barrier = Barrier(2)

    def member(self, first_name, last_name, email):
        person = Person.objects.create(first_name=first_name, last_name=last_name, primary_email=email)
        Membership.objects.create(person=person, status=Membership.Status.ACTIVE, joined_at=timezone.localdate(), membership_source=Membership.Source.COMMUNITY_PLATFORM)
        user = User.objects.create_user(email=email, password="Strong-password-123!", person=person)
        CommunityProfile.objects.create(person=person)
        return user

    def edit(self):
        close_old_connections()
        try:
            self.barrier.wait(timeout=10)
            try:
                return ("edited", edit_community_post(user=User.objects.get(pk=self.author.pk), public_id=self.post.public_id, data={"purpose": "OFFER"}))
            except CommunityPostConversationLocked:
                return ("locked", None)
        finally:
            close_old_connections()

    def reply(self):
        close_old_connections()
        try:
            self.barrier.wait(timeout=10)
            with transaction.atomic():
                return ("replied", create_community_reply(user=User.objects.get(pk=self.viewer.pk), post_id=self.post.pk, body="First reply"))
        finally:
            close_old_connections()

    def test_first_reply_and_purpose_edit_are_serialized_on_post_lock(self):
        with ThreadPoolExecutor(max_workers=2) as executor:
            edit_result, reply_result = [future.result(timeout=30) for future in [executor.submit(self.edit), executor.submit(self.reply)]]
        self.post.refresh_from_db()
        self.assertTrue(CommunityPostReply.objects.filter(post=self.post).exists())
        self.assertIn(self.post.purpose, [CommunityPost.Purpose.ASK, CommunityPost.Purpose.OFFER])
        self.assertIn(edit_result[0], ["edited", "locked"])
        self.assertEqual(reply_result[0], "replied")
        if edit_result[0] == "locked":
            self.assertEqual(self.post.purpose, CommunityPost.Purpose.ASK)
