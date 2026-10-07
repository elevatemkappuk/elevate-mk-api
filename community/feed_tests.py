from datetime import date

from django.core.exceptions import ValidationError
from django.db.models import ProtectedError
from django.test import TestCase

from community.connections import canonical_person_pair
from community.feed import visible_community_posts
from community.models import CommunityConnection, CommunityPost, CommunityProfile
from memberships.models import Membership
from people.models import Person
from accounts.models import User


class CommunityPostModelTests(TestCase):
    def setUp(self):
        self.user = self._member("post-author@example.com", "Amina", "Zulu")

    def _member(self, email, first_name, last_name, *, record_type=Person.RecordType.BUSINESS):
        user = User.objects.create_user(
            email=email,
            password="testpass123",
            person_first_name=first_name,
            person_last_name=last_name,
            person_record_type=record_type,
        )
        Membership.objects.create(
            person=user.person,
            status=Membership.Status.ACTIVE,
            joined_at=date(2026, 1, 1),
            membership_source=Membership.Source.COMMUNITY_PLATFORM,
        )
        return user

    def test_defaults_uuid_timestamps_and_locked_choices(self):
        post = CommunityPost.objects.create(
            author=self.user.person,
            purpose=CommunityPost.Purpose.ASK,
            headline="Need advice",
            body="How should I approach this?",
            audience=CommunityPost.Audience.ELEVATE_COMMUNITY,
        )

        self.assertIsNotNone(post.public_id)
        self.assertIsNotNone(post.created_at)
        self.assertIsNotNone(post.updated_at)
        self.assertEqual(post.status, CommunityPost.Status.ACTIVE)
        self.assertEqual(set(CommunityPost.Purpose.values), {"ASK", "OFFER", "OPPORTUNITY", "UPDATE"})
        self.assertEqual(set(CommunityPost.Audience.values), {"ELEVATE_COMMUNITY", "CONNECTIONS"})
        self.assertEqual(set(CommunityPost.Status.values), {"ACTIVE", "AUTHOR_DELETED", "MODERATOR_REMOVED"})

    def test_content_is_trimmed_for_validation_and_length_limited(self):
        with self.assertRaises(ValidationError):
            CommunityPost.objects.create(
                author=self.user.person,
                purpose=CommunityPost.Purpose.ASK,
                headline="   ",
                body="Useful body",
                audience=CommunityPost.Audience.ELEVATE_COMMUNITY,
            )
        with self.assertRaises(ValidationError):
            CommunityPost.objects.create(
                author=self.user.person,
                purpose=CommunityPost.Purpose.ASK,
                headline="A" * 121,
                body="Useful body",
                audience=CommunityPost.Audience.ELEVATE_COMMUNITY,
            )
        with self.assertRaises(ValidationError):
            CommunityPost.objects.create(
                author=self.user.person,
                purpose=CommunityPost.Purpose.ASK,
                headline="Useful headline",
                body="B" * 2001,
                audience=CommunityPost.Audience.ELEVATE_COMMUNITY,
            )
        with self.assertRaises(ValidationError):
            CommunityPost.objects.create(
                author=self.user.person,
                purpose="INVALID",
                headline="Useful headline",
                body="Useful body",
                audience=CommunityPost.Audience.ELEVATE_COMMUNITY,
            )

    def test_author_must_be_business_and_person_is_protected(self):
        technical = self._member(
            "technical@example.com",
            "Technical",
            "User",
            record_type=Person.RecordType.TECHNICAL,
        )
        with self.assertRaises(ValidationError):
            CommunityPost.objects.create(
                author=technical.person,
                purpose=CommunityPost.Purpose.UPDATE,
                headline="Update",
                body="A relevant update",
                audience=CommunityPost.Audience.ELEVATE_COMMUNITY,
            )

        post = CommunityPost.objects.create(
            author=self.user.person,
            purpose=CommunityPost.Purpose.OFFER,
            headline="An offer",
            body="I can help.",
            audience=CommunityPost.Audience.ELEVATE_COMMUNITY,
        )
        with self.assertRaises(ProtectedError):
            self.user.person.delete()
        self.assertTrue(CommunityPost.objects.filter(pk=post.pk).exists())


class CommunityPostVisibilityTests(TestCase):
    def setUp(self):
        self.author = self._member("author@example.com", "Author", "Member")
        self.connected = self._member("connected@example.com", "Connected", "Member")
        self.unrelated = self._member("unrelated@example.com", "Unrelated", "Member")
        self.client = self._member("client@example.com", "Client", "Member")

    def _member(self, email, first_name, last_name):
        user = User.objects.create_user(
            email=email,
            password="testpass123",
            person_first_name=first_name,
            person_last_name=last_name,
        )
        Membership.objects.create(
            person=user.person,
            status=Membership.Status.ACTIVE,
            joined_at=date(2026, 1, 1),
            membership_source=Membership.Source.COMMUNITY_PLATFORM,
        )
        CommunityProfile.objects.create(person=user.person)
        return user

    def _post(self, *, audience, status=CommunityPost.Status.ACTIVE, author=None):
        return CommunityPost.objects.create(
            author=(author or self.author).person,
            purpose=CommunityPost.Purpose.ASK,
            headline="Useful headline",
            body="Useful Community content.",
            audience=audience,
            status=status,
        )

    def _connection(self, first, second, status):
        low, high = canonical_person_pair(first.person_id, second.person_id)
        return CommunityConnection.objects.create(
            person_low_id=low,
            person_high_id=high,
            requester=first.person,
            status=status,
        )

    def test_community_posts_are_visible_and_purpose_filter_is_supported(self):
        ask = self._post(audience=CommunityPost.Audience.ELEVATE_COMMUNITY)
        offer = CommunityPost.objects.create(
            author=self.author.person,
            purpose=CommunityPost.Purpose.OFFER,
            headline="Offer",
            body="I can offer useful help.",
            audience=CommunityPost.Audience.ELEVATE_COMMUNITY,
        )

        visible = visible_community_posts(viewer=self.client)
        self.assertEqual(set(visible.values_list("id", flat=True)), {ask.id, offer.id})
        self.assertEqual(list(visible_community_posts(viewer=self.client, purpose=CommunityPost.Purpose.ASK)), [ask])

    def test_directory_hidden_does_not_hide_intentional_community_post(self):
        self.author.person.community_profile.directory_visible = False
        self.author.person.community_profile.save(update_fields=["directory_visible", "updated_at"])
        post = self._post(audience=CommunityPost.Audience.ELEVATE_COMMUNITY)

        self.assertTrue(visible_community_posts(viewer=self.client).filter(pk=post.pk).exists())

    def test_ineligible_viewer_gets_no_posts(self):
        self.client.person.membership.status = Membership.Status.FORMER
        self.client.person.membership.ended_at = date(2026, 2, 1)
        self.client.person.membership.save(update_fields=["status", "ended_at", "updated_at"])
        self._post(audience=CommunityPost.Audience.ELEVATE_COMMUNITY)

        self.assertFalse(visible_community_posts(viewer=self.client).exists())

    def test_author_ineligibility_hides_content_but_restoring_eligibility_restores_active_content(self):
        post = self._post(audience=CommunityPost.Audience.ELEVATE_COMMUNITY)
        self.author.person.archived_at = date(2026, 2, 1)
        self.author.person.save(update_fields=["archived_at", "updated_at"])
        self.assertFalse(visible_community_posts(viewer=self.client).filter(pk=post.pk).exists())
        self.assertTrue(CommunityPost.objects.filter(pk=post.pk).exists())

        self.author.person.archived_at = None
        self.author.person.save(update_fields=["archived_at", "updated_at"])
        self.assertTrue(visible_community_posts(viewer=self.client).filter(pk=post.pk).exists())

    def test_former_or_inactive_author_content_is_retained_and_hidden(self):
        post = self._post(audience=CommunityPost.Audience.ELEVATE_COMMUNITY)
        membership = self.author.person.membership
        membership.status = Membership.Status.FORMER
        membership.ended_at = date(2026, 2, 1)
        membership.save(update_fields=["status", "ended_at", "updated_at"])
        self.assertFalse(visible_community_posts(viewer=self.client).filter(pk=post.pk).exists())
        self.assertTrue(CommunityPost.objects.filter(pk=post.pk).exists())

        membership.status = Membership.Status.ACTIVE
        membership.ended_at = None
        membership.save(update_fields=["status", "ended_at", "updated_at"])
        self.author.is_active = False
        self.author.save(update_fields=["is_active"])
        self.assertFalse(visible_community_posts(viewer=self.client).filter(pk=post.pk).exists())

        self.author.is_active = True
        self.author.save(update_fields=["is_active"])
        self.assertTrue(visible_community_posts(viewer=self.client).filter(pk=post.pk).exists())

    def test_deleted_or_moderator_removed_content_stays_hidden_after_restoration(self):
        deleted = self._post(audience=CommunityPost.Audience.ELEVATE_COMMUNITY, status=CommunityPost.Status.AUTHOR_DELETED)
        removed = self._post(audience=CommunityPost.Audience.ELEVATE_COMMUNITY, status=CommunityPost.Status.MODERATOR_REMOVED)
        self.assertFalse(visible_community_posts(viewer=self.client).filter(pk__in=[deleted.pk, removed.pk]).exists())

    def test_connections_audience_uses_current_accepted_state(self):
        post = self._post(audience=CommunityPost.Audience.CONNECTIONS)
        self.assertFalse(visible_community_posts(viewer=self.connected).filter(pk=post.pk).exists())
        self._connection(self.author, self.connected, CommunityConnection.Status.PENDING)
        self.assertFalse(visible_community_posts(viewer=self.connected).filter(pk=post.pk).exists())

        connection = CommunityConnection.objects.get()
        connection.status = CommunityConnection.Status.ACCEPTED
        connection.save(update_fields=["status", "updated_at"])
        self.assertTrue(visible_community_posts(viewer=self.connected).filter(pk=post.pk).exists())
        self.assertTrue(visible_community_posts(viewer=self.author).filter(pk=post.pk).exists())
        self.assertFalse(visible_community_posts(viewer=self.unrelated).filter(pk=post.pk).exists())

        connection.status = CommunityConnection.Status.DISCONNECTED
        connection.save(update_fields=["status", "updated_at"])
        self.assertFalse(visible_community_posts(viewer=self.connected).filter(pk=post.pk).exists())

    def test_community_audience_is_independent_of_connection_state(self):
        post = self._post(audience=CommunityPost.Audience.ELEVATE_COMMUNITY)
        self._connection(self.author, self.connected, CommunityConnection.Status.DECLINED)
        self.assertTrue(visible_community_posts(viewer=self.unrelated).filter(pk=post.pk).exists())
