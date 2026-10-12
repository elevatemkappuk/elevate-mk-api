from datetime import timedelta

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from audit.models import AuditEvent
from community.connections import canonical_person_pair
from community.models import CommunityConnection, CommunityPost, CommunityProfile
from memberships.models import Membership
from people.models import Person
from professional_profiles.models import Industry, ProfessionalProfile


class CommunityPostApiTests(TestCase):
    list_url = "/api/v1/community/posts/"

    def setUp(self):
        self.client = APIClient()
        cache.clear()
        self.industry = Industry.objects.get(slug="technology")
        self.author = self.create_member("Amina", "Author", "author@example.com")
        self.viewer = self.create_member("Ben", "Viewer", "viewer@example.com")
        self.other = self.create_member("Cara", "Other", "other@example.com")
        ProfessionalProfile.objects.create(person=self.author.person, job_title="Engineer", industry=self.industry)
        self.client.force_authenticate(user=self.viewer)

    def create_member(self, first_name, last_name, email, *, directory_visible=True):
        person = Person.objects.create(
            first_name=first_name,
            last_name=last_name,
            primary_email=email,
            mobile="+447700900123",
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

    def create_post(self, *, author=None, audience=CommunityPost.Audience.ELEVATE_COMMUNITY, purpose=CommunityPost.Purpose.ASK, status=CommunityPost.Status.ACTIVE):
        return CommunityPost.objects.create(
            author=(author or self.author).person,
            purpose=purpose,
            headline=f"{purpose.title()} headline",
            body="A useful Community post.",
            audience=audience,
            status=status,
        )

    def create_connection(self, first, second, status):
        low, high = canonical_person_pair(first.person_id, second.person_id)
        return CommunityConnection.objects.create(
            person_low_id=low,
            person_high_id=high,
            requester=first.person,
            status=status,
        )

    def test_list_returns_safe_newest_first_projection_without_contact_data(self):
        older = self.create_post(purpose=CommunityPost.Purpose.ASK)
        newer = self.create_post(purpose=CommunityPost.Purpose.OFFER)

        response = self.client.get(self.list_url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["public_id"] for item in response.data["results"]], [str(newer.public_id), str(older.public_id)])
        result = response.data["results"][0]
        self.assertEqual(result["author"]["first_name"], "Amina")
        self.assertEqual(result["author"]["professional"]["industry"], {"slug": "technology", "label": "Technology"})
        self.assertEqual(result["reply_count"], 0)
        self.assertFalse(result["is_own_post"])
        self.assertNotIn("author_id", result)
        self.assertNotIn("email", result["author"])
        self.assertNotIn("mobile", result["author"])
        self.assertNotIn("primary_email", str(result))
        self.assertNotIn("membership", str(result))
        self.assertNotIn("provider", str(result))

    def test_purpose_filter_is_validated_and_does_not_weaken_visibility(self):
        for purpose in CommunityPost.Purpose.values:
            self.create_post(purpose=purpose)

        for purpose in CommunityPost.Purpose.values:
            response = self.client.get(self.list_url, {"purpose": purpose})
            self.assertEqual(response.status_code, 200)
            self.assertTrue(all(item["purpose"] == purpose for item in response.data["results"]))

        self.assertEqual(self.client.get(self.list_url, {"purpose": "INVALID"}).status_code, 400)
        hidden = self.create_post(audience=CommunityPost.Audience.CONNECTIONS)
        response = self.client.get(self.list_url, {"purpose": CommunityPost.Purpose.ASK})
        self.assertNotIn(str(hidden.public_id), {item["public_id"] for item in response.data["results"]})

    def test_detail_uses_same_visibility_policy_and_generic_not_found(self):
        visible = self.create_post()
        hidden = self.create_post(audience=CommunityPost.Audience.CONNECTIONS)

        detail = self.client.get(f"{self.list_url}{visible.public_id}/")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.data["public_id"], str(visible.public_id))
        self.assertEqual(self.client.get(f"{self.list_url}{hidden.public_id}/").status_code, 404)
        self.assertEqual(self.client.get(f"{self.list_url}00000000-0000-0000-0000-000000000000/").status_code, 404)

    def test_connection_visibility_tracks_current_state_for_list_and_detail(self):
        post = self.create_post(author=self.author, audience=CommunityPost.Audience.CONNECTIONS)
        detail_url = f"{self.list_url}{post.public_id}/"
        self.assertEqual(self.client.get(detail_url).status_code, 404)

        connection = self.create_connection(self.author, self.viewer, CommunityConnection.Status.PENDING)
        self.assertEqual(self.client.get(detail_url).status_code, 404)
        connection.status = CommunityConnection.Status.ACCEPTED
        connection.save(update_fields=["status", "updated_at"])
        self.assertEqual(self.client.get(detail_url).status_code, 200)
        connection.status = CommunityConnection.Status.DISCONNECTED
        connection.save(update_fields=["status", "updated_at"])
        self.assertEqual(self.client.get(detail_url).status_code, 404)

        self.client.force_authenticate(user=self.author)
        self.assertEqual(self.client.get(detail_url).status_code, 200)

    def test_create_normalizes_text_derives_author_and_audits_without_content(self):
        response = self.client.post(
            self.list_url,
            {
                "purpose": CommunityPost.Purpose.UPDATE,
                "headline": "  A clear update  ",
                "body": "  Keep internal\nformatting.  ",
                "audience": CommunityPost.Audience.ELEVATE_COMMUNITY,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        post = CommunityPost.objects.get(public_id=response.data["public_id"])
        self.assertEqual(post.author_id, self.viewer.person_id)
        self.assertEqual(post.headline, "A clear update")
        self.assertEqual(post.body, "Keep internal\nformatting.")
        audit = AuditEvent.objects.get(action=AuditEvent.Action.COMMUNITY_POST_CREATED)
        self.assertEqual(audit.metadata, {"public_id": str(post.public_id), "purpose": "UPDATE", "audience": "ELEVATE_COMMUNITY"})
        self.assertNotIn("A clear update", str(audit.metadata))

    def test_create_accepts_each_locked_purpose_and_audience(self):
        responses = []
        for index, purpose in enumerate(CommunityPost.Purpose.values):
            for audience in CommunityPost.Audience.values:
                responses.append(
                    self.client.post(
                        self.list_url,
                        {
                            "purpose": purpose,
                            "headline": f"Headline {index}-{audience}",
                            "body": "Body",
                            "audience": audience,
                        },
                        format="json",
                    )
                )
        self.assertTrue(all(response.status_code == 201 for response in responses))

    def test_create_rejects_unknown_fields_and_ineligible_member(self):
        response = self.client.post(
            self.list_url,
            {"purpose": "ASK", "headline": "Headline", "body": "Body", "audience": "ELEVATE_COMMUNITY", "author": 1},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.viewer.person.membership.status = Membership.Status.FORMER
        self.viewer.person.membership.ended_at = timezone.localdate()
        self.viewer.person.membership.save(update_fields=["status", "ended_at", "updated_at"])
        response = self.client.post(
            self.list_url,
            {"purpose": "ASK", "headline": "Headline", "body": "Body", "audience": "ELEVATE_COMMUNITY"},
            format="json",
        )
        self.assertEqual(response.status_code, 403)

    def test_create_rejects_missing_blank_oversized_and_invalid_values(self):
        valid = {"purpose": "ASK", "headline": "Headline", "body": "Body", "audience": "ELEVATE_COMMUNITY"}
        invalid_payloads = [
            {},
            {**valid, "headline": "   "},
            {**valid, "body": "   "},
            {**valid, "headline": "H" * 121},
            {**valid, "body": "B" * 2001},
            {**valid, "purpose": "INVALID"},
            {**valid, "audience": "INVALID"},
        ]
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                self.assertEqual(self.client.post(self.list_url, payload, format="json").status_code, 400)

    def test_create_idempotency_replays_once_and_conflicts_on_payload_change(self):
        payload = {"purpose": "ASK", "headline": "Same request", "body": "Same body", "audience": "ELEVATE_COMMUNITY"}
        first = self.client.post(self.list_url, payload, format="json", HTTP_IDEMPOTENCY_KEY="post-key")
        replay = self.client.post(self.list_url, payload, format="json", HTTP_IDEMPOTENCY_KEY="post-key")
        conflict = self.client.post(self.list_url, {**payload, "body": "Different body"}, format="json", HTTP_IDEMPOTENCY_KEY="post-key")

        self.assertEqual(first.status_code, 201)
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.data, first.data)
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(CommunityPost.objects.filter(author=self.viewer.person).count(), 1)
        self.assertEqual(AuditEvent.objects.filter(action=AuditEvent.Action.COMMUNITY_POST_CREATED).count(), 1)

        self.client.force_authenticate(user=self.other)
        other = self.client.post(self.list_url, payload, format="json", HTTP_IDEMPOTENCY_KEY="post-key")
        self.assertEqual(other.status_code, 201)
        self.assertEqual(CommunityPost.objects.count(), 2)

    def test_directory_hidden_author_remains_identified_without_contact_data(self):
        self.author.person.community_profile.directory_visible = False
        self.author.person.community_profile.save(update_fields=["directory_visible"])
        post = self.create_post()
        result = self.client.get(self.list_url).data["results"][0]
        self.assertEqual(result["public_id"], str(post.public_id))
        self.assertEqual(result["author"]["first_name"], "Amina")
        self.assertNotIn("email", result["author"])
        self.assertNotIn("mobile", result["author"])

    def test_deleted_removed_and_ineligible_author_posts_are_not_member_visible(self):
        deleted = self.create_post(status=CommunityPost.Status.AUTHOR_DELETED)
        removed = self.create_post(status=CommunityPost.Status.MODERATOR_REMOVED)
        archived = self.create_post(author=self.author)
        self.author.person.archived_at = timezone.localdate()
        self.author.person.save(update_fields=["archived_at", "updated_at"])

        response = self.client.get(self.list_url)
        ids = {item["public_id"] for item in response.data["results"]}
        self.assertNotIn(str(deleted.public_id), ids)
        self.assertNotIn(str(removed.public_id), ids)
        self.assertNotIn(str(archived.public_id), ids)
        self.assertTrue(CommunityPost.objects.filter(pk=archived.pk).exists())
        self.assertEqual(self.client.get(f"{self.list_url}{deleted.public_id}/").status_code, 404)
        self.assertEqual(self.client.get(f"{self.list_url}{removed.public_id}/").status_code, 404)
        self.assertEqual(self.client.get(f"{self.list_url}{archived.public_id}/").status_code, 404)
