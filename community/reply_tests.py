from datetime import timedelta

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from audit.models import AuditEvent
from community.connections import canonical_person_pair
from community.feed import visible_community_posts
from community.models import CommunityConnection, CommunityPost, CommunityPostReply, CommunityProfile
from community.replies import post_has_replies
from memberships.models import Membership
from people.models import Person


class CommunityPostReplyApiTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.author = self.create_member("Amina", "Author", "author@example.com")
        self.viewer = self.create_member("Ben", "Viewer", "viewer@example.com")
        self.other = self.create_member("Cara", "Other", "other@example.com")
        self.post = self.create_post(author=self.author)
        self.client.force_authenticate(user=self.viewer)

    def create_member(self, first_name, last_name, email, *, directory_visible=True):
        person = Person.objects.create(
            first_name=first_name,
            last_name=last_name,
            primary_email=email,
        )
        Membership.objects.create(
            person=person,
            status=Membership.Status.ACTIVE,
            joined_at=timezone.localdate() - timedelta(days=1),
            membership_source=Membership.Source.COMMUNITY_PLATFORM,
        )
        user = User.objects.create_user(email=email, password="Strong-password-123!", person=person)
        CommunityProfile.objects.create(person=person, directory_visible=directory_visible)
        return user

    def create_post(self, *, author=None, audience=CommunityPost.Audience.ELEVATE_COMMUNITY, status=CommunityPost.Status.ACTIVE):
        return CommunityPost.objects.create(
            author=(author or self.author).person,
            purpose=CommunityPost.Purpose.ASK,
            headline="A useful headline",
            body="A useful post body.",
            audience=audience,
            status=status,
        )

    def create_reply(self, *, post=None, author=None, body="A useful reply.", reply_to=None, status=CommunityPostReply.Status.ACTIVE):
        return CommunityPostReply.objects.create(
            post=post or self.post,
            author=(author or self.author).person,
            body=body,
            reply_to=reply_to,
            status=status,
        )

    def replies_url(self, post=None):
        return f"/api/v1/community/posts/{(post or self.post).public_id}/replies/"

    def reply_url(self, reply, post=None):
        return f"{self.replies_url(post)}{reply.public_id}/"

    def connect(self, first, second, status):
        low, high = canonical_person_pair(first.person_id, second.person_id)
        return CommunityConnection.objects.create(
            person_low_id=low,
            person_high_id=high,
            requester=first.person,
            status=status,
        )

    def test_model_defaults_validation_reply_to_invariant_and_history_protection(self):
        reply = self.create_reply()
        self.assertIsNotNone(reply.public_id)
        self.assertEqual(reply.status, CommunityPostReply.Status.ACTIVE)
        self.assertIsNotNone(reply.created_at)
        self.assertIsNotNone(reply.updated_at)
        self.assertTrue(post_has_replies(self.post))

        with self.assertRaises(ValidationError):
            self.create_reply(body="   ")
        with self.assertRaises(ValidationError):
            self.create_reply(body="R" * 1001)

        other_post = self.create_post(author=self.viewer)
        with self.assertRaises(ValidationError):
            self.create_reply(reply_to=CommunityPostReply.objects.create(
                post=other_post,
                author=self.author.person,
                body="Other post reply",
            ))

        technical = Person.objects.create(
            first_name="Technical",
            last_name="User",
            primary_email="technical@example.com",
            record_type=Person.RecordType.TECHNICAL,
        )
        with self.assertRaises(ValidationError):
            CommunityPostReply.objects.create(post=self.post, author=technical, body="Not allowed")

    def test_create_list_oldest_first_reply_to_is_flat_and_safe(self):
        first = self.client.post(self.replies_url(), {"body": "  First reply  "}, format="json")
        self.assertEqual(first.status_code, 201)
        second = self.client.post(
            self.replies_url(),
            {"body": "Second reply", "reply_to_id": first.data["public_id"]},
            format="json",
        )
        self.assertEqual(second.status_code, 201)

        listed = self.client.get(self.replies_url())
        self.assertEqual(listed.status_code, 200)
        self.assertEqual([item["public_id"] for item in listed.data["results"]], [first.data["public_id"], second.data["public_id"]])
        self.assertEqual(listed.data["results"][0]["body"], "First reply")
        context = listed.data["results"][1]["replying_to"]
        self.assertEqual(context["reply_id"], first.data["public_id"])
        self.assertIn("author", context)
        self.assertNotIn("body", context)
        self.assertNotIn("children", listed.data["results"][1])
        self.assertNotIn("email", str(listed.data))
        self.assertNotIn("mobile", str(listed.data))
        self.assertNotIn("person_id", str(listed.data))
        self.assertNotIn("membership", str(listed.data))

    def test_reply_validation_rejects_unknown_fields_blank_oversized_and_unavailable_targets(self):
        valid = {"body": "A reply"}
        for payload in [
            {**valid, "unexpected": "value"},
            {"body": "   "},
            {"body": "R" * 1001},
        ]:
            with self.subTest(payload=payload):
                self.assertEqual(self.client.post(self.replies_url(), payload, format="json").status_code, 400)

        other_post = self.create_post(author=self.viewer)
        other_reply = self.create_reply(post=other_post)
        self.assertEqual(
            self.client.post(self.replies_url(), {"body": "Cross post", "reply_to_id": str(other_reply.public_id)}, format="json").status_code,
            404,
        )
        other_reply.status = CommunityPostReply.Status.AUTHOR_DELETED
        other_reply.deleted_at = timezone.now()
        other_reply.save(update_fields=["status", "deleted_at", "updated_at"])
        self.assertEqual(
            self.client.post(self.replies_url(other_post), {"body": "Deleted target", "reply_to_id": str(other_reply.public_id)}, format="json").status_code,
            404,
        )

    def test_reply_idempotency_replays_once_conflicts_and_is_scoped_by_member(self):
        payload = {"body": "Retry-safe reply"}
        first = self.client.post(self.replies_url(), payload, format="json", HTTP_IDEMPOTENCY_KEY="reply-key")
        replay = self.client.post(self.replies_url(), payload, format="json", HTTP_IDEMPOTENCY_KEY="reply-key")
        conflict = self.client.post(self.replies_url(), {"body": "Changed"}, format="json", HTTP_IDEMPOTENCY_KEY="reply-key")

        self.assertEqual(first.status_code, 201)
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.data, first.data)
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(CommunityPostReply.objects.filter(post=self.post).count(), 1)
        self.assertEqual(AuditEvent.objects.filter(action=AuditEvent.Action.COMMUNITY_REPLY_CREATED).count(), 1)

        self.client.force_authenticate(user=self.other)
        other = self.client.post(self.replies_url(), payload, format="json", HTTP_IDEMPOTENCY_KEY="reply-key")
        self.assertEqual(other.status_code, 201)

    def test_edit_own_reply_sets_edited_at_noop_is_quiet_and_fields_are_immutable(self):
        reply = self.create_reply(author=self.viewer, body="Original")
        audit_before = AuditEvent.objects.filter(action=AuditEvent.Action.COMMUNITY_REPLY_EDITED).count()
        noop = self.client.patch(self.reply_url(reply), {"body": " Original "}, format="json")
        self.assertEqual(noop.status_code, 200)
        reply.refresh_from_db()
        self.assertIsNone(reply.edited_at)
        self.assertEqual(AuditEvent.objects.filter(action=AuditEvent.Action.COMMUNITY_REPLY_EDITED).count(), audit_before)

        changed = self.client.patch(self.reply_url(reply), {"body": "Updated"}, format="json")
        self.assertEqual(changed.status_code, 200)
        reply.refresh_from_db()
        self.assertEqual(reply.body, "Updated")
        self.assertIsNotNone(reply.edited_at)
        self.assertEqual(changed.data["body"], "Updated")
        self.assertEqual(AuditEvent.objects.filter(action=AuditEvent.Action.COMMUNITY_REPLY_EDITED).count(), 1)
        immutable = self.client.patch(self.reply_url(reply), {"body": "Again", "reply_to_id": None}, format="json")
        self.assertEqual(immutable.status_code, 400)

        self.client.force_authenticate(user=self.other)
        self.assertEqual(self.client.patch(self.reply_url(reply), {"body": "No"}, format="json").status_code, 404)

    def test_delete_is_soft_and_referenced_reply_keeps_safe_context(self):
        first = self.create_reply(author=self.author, body="Original author reply")
        second = self.create_reply(author=self.viewer, body="Follow-up", reply_to=first)
        self.client.force_authenticate(user=self.author)
        response = self.client.delete(self.reply_url(first))
        self.assertEqual(response.status_code, 204)
        first.refresh_from_db()
        self.assertEqual(first.status, CommunityPostReply.Status.AUTHOR_DELETED)
        self.assertIsNotNone(first.deleted_at)
        self.assertTrue(CommunityPostReply.objects.filter(pk=first.pk).exists())
        self.assertEqual(self.client.delete(self.reply_url(first)).status_code, 404)

        self.client.force_authenticate(user=self.viewer)
        listed = self.client.get(self.replies_url()).data["results"]
        deleted = next(item for item in listed if item["public_id"] == str(first.public_id))
        follow_up = next(item for item in listed if item["public_id"] == str(second.public_id))
        self.assertEqual(deleted["body"], "This reply was removed by the author.")
        self.assertIsNone(deleted["author"])
        self.assertIsNone(follow_up["replying_to"]["author"])
        self.assertNotIn("Original author reply", str(listed))

    def test_active_reply_count_excludes_deleted_and_removed_replies(self):
        active = self.create_reply()
        deleted = self.create_reply()
        deleted.status = CommunityPostReply.Status.AUTHOR_DELETED
        deleted.deleted_at = timezone.now()
        deleted.save(update_fields=["status", "deleted_at", "updated_at"])
        removed = self.create_reply()
        removed.status = CommunityPostReply.Status.MODERATOR_REMOVED
        removed.removed_at = timezone.now()
        removed.save(update_fields=["status", "removed_at", "updated_at"])

        feed_item = self.client.get(f"/api/v1/community/posts/{self.post.public_id}/").data
        self.assertEqual(feed_item["reply_count"], 1)
        self.assertEqual(active.status, CommunityPostReply.Status.ACTIVE)
        self.assertTrue(post_has_replies(self.post))

    def test_reply_list_projection_does_not_add_queries_per_reply(self):
        for index in range(5):
            self.create_reply(body=f"Reply {index}")

        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(self.replies_url())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["results"]), 5)
        self.assertLessEqual(len(queries), 8)

    def test_connections_parent_visibility_controls_all_reply_operations(self):
        connection_post = self.create_post(author=self.author, audience=CommunityPost.Audience.CONNECTIONS)
        self.assertEqual(self.client.get(self.replies_url(connection_post)).status_code, 404)
        self.assertEqual(self.client.post(self.replies_url(connection_post), {"body": "No access"}, format="json").status_code, 404)
        self.connect(self.author, self.viewer, CommunityConnection.Status.ACCEPTED)
        created = self.client.post(self.replies_url(connection_post), {"body": "Now connected"}, format="json")
        self.assertEqual(created.status_code, 201)
        connection = CommunityConnection.objects.get()
        connection.status = CommunityConnection.Status.DISCONNECTED
        connection.save(update_fields=["status", "updated_at"])
        reply = CommunityPostReply.objects.get(post=connection_post)
        self.assertEqual(self.client.get(self.replies_url(connection_post)).status_code, 404)
        self.assertEqual(self.client.patch(self.reply_url(reply, connection_post), {"body": "No longer"}, format="json").status_code, 404)
        self.assertEqual(self.client.delete(self.reply_url(reply, connection_post)).status_code, 404)

    def test_ineligible_reply_author_is_retained_but_body_and_identity_are_hidden(self):
        visible_post = self.create_post(author=self.viewer)
        reply = self.create_reply(post=visible_post, author=self.author, body="Private active participation")
        self.author.person.membership.status = Membership.Status.FORMER
        self.author.person.membership.ended_at = timezone.localdate()
        self.author.person.membership.save(update_fields=["status", "ended_at", "updated_at"])

        listed = self.client.get(self.replies_url(visible_post)).data["results"]
        item = next(value for value in listed if value["public_id"] == str(reply.public_id))
        self.assertEqual(item["body"], "This reply is no longer available.")
        self.assertIsNone(item["author"])
        self.assertTrue(CommunityPostReply.objects.filter(pk=reply.pk).exists())

    def test_inaccessible_or_removed_parent_does_not_reveal_reply_conversation(self):
        removed_post = self.create_post(status=CommunityPost.Status.MODERATOR_REMOVED)
        self.create_reply(post=removed_post)
        self.assertEqual(self.client.get(self.replies_url(removed_post)).status_code, 404)
        self.assertEqual(self.client.post(self.replies_url(removed_post), {"body": "Hidden"}, format="json").status_code, 404)
