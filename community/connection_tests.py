from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from django.conf import settings
from django.db import IntegrityError, close_old_connections, connection, transaction
from django.test import TestCase, TransactionTestCase
from django.test.client import RequestFactory
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from audit.models import AuditEvent
from community.models import CommunityConnection, CommunityProfile
from community.connections import send_connection_request
from memberships.models import Membership
from people.models import Person


class CommunityConnectionApiTests(TestCase):
    connections_url = "/api/v1/community/connections/"
    requests_url = "/api/v1/community/connections/requests/"

    def setUp(self):
        self.client = APIClient()
        self.viewer = self.create_member("Amina", "Viewer", "amina@example.com")
        self.target = self.create_member("Ben", "Target", "ben@example.com")
        self.other = self.create_member("Cara", "Other", "cara@example.com")
        self.client.force_authenticate(user=self.viewer)

    def create_member(self, first_name, last_name, email, *, visible=True):
        person = Person.objects.create(
            first_name=first_name,
            last_name=last_name,
            primary_email=email,
            mobile=f"+447700900{Person.objects.count():03d}",
        )
        Membership.objects.create(
            person=person,
            status=Membership.Status.ACTIVE,
            joined_at=timezone.localdate() - timedelta(days=1),
            membership_source=Membership.Source.COMMUNITY_PLATFORM,
        )
        user = User.objects.create_user(email=email, password="Strong-password-123!", person=person)
        CommunityProfile.objects.create(person=person, directory_visible=visible)
        return user

    def target_directory_url(self, user=None):
        return str((user or self.target).person.community_profile.directory_id)

    def request_payload(self, user=None):
        return {"directory_id": self.target_directory_url(user)}

    def send_request(self, user=None):
        return self.client.post(self.requests_url, self.request_payload(user), format="json")

    def test_model_enforces_canonical_pair_self_pair_requester_and_public_id(self):
        low, high = sorted([self.viewer.person_id, self.target.person_id])
        connection = CommunityConnection.objects.create(
            person_low_id=low,
            person_high_id=high,
            requester_id=self.viewer.person_id,
            status=CommunityConnection.Status.PENDING,
        )
        self.assertEqual(connection.recipient.pk, self.target.person_id)
        self.assertIsNotNone(connection.public_id)

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                CommunityConnection.objects.create(
                    person_low_id=high,
                    person_high_id=low,
                    requester_id=self.target.person_id,
                    status=CommunityConnection.Status.PENDING,
                )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                CommunityConnection.objects.create(
                    person_low_id=low,
                    person_high_id=low,
                    requester_id=low,
                    status=CommunityConnection.Status.PENDING,
                )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                CommunityConnection.objects.create(
                    person_low_id=low,
                    person_high_id=high,
                    requester_id=self.other.person_id,
                    status=CommunityConnection.Status.PENDING,
                )

    def test_eligible_request_is_idempotent_and_does_not_expose_internal_data(self):
        response = self.send_request()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(CommunityConnection.objects.count(), 1)
        self.assertEqual(response.data["member"]["directory_id"], str(self.target.person.community_profile.directory_id))
        self.assertNotIn("contact", response.data["member"])
        for field in ("id", "person_id", "user_id", "membership", "asset_namespace_id", "email", "mobile"):
            self.assertNotIn(field, response.data["member"])
        second = self.send_request()
        self.assertEqual(second.status_code, 200)
        self.assertEqual(CommunityConnection.objects.count(), 1)
        self.assertEqual(second.data["connection_id"], response.data["connection_id"])

    def test_self_hidden_and_ineligible_targets_are_rejected(self):
        self.assertEqual(
            self.client.post(self.requests_url, {"directory_id": self.viewer.person.community_profile.directory_id}, format="json").status_code,
            400,
        )
        self.target.person.community_profile.directory_visible = False
        self.target.person.community_profile.save(update_fields=["directory_visible"])
        self.assertEqual(self.send_request().status_code, 404)
        self.target.person.membership.status = Membership.Status.FORMER
        self.target.person.membership.ended_at = timezone.localdate()
        self.target.person.membership.save(update_fields=["status", "ended_at"])
        self.target.person.community_profile.directory_visible = True
        self.target.person.community_profile.save(update_fields=["directory_visible"])
        self.assertEqual(self.send_request().status_code, 404)

    def test_crossed_request_remains_pending_for_explicit_recipient_decision(self):
        first = self.send_request()
        self.client.force_authenticate(user=self.target)
        crossed = self.client.post(
            self.requests_url,
            {"directory_id": self.viewer.person.community_profile.directory_id},
            format="json",
        )
        self.assertEqual(crossed.status_code, 200)
        self.assertEqual(CommunityConnection.objects.count(), 1)
        self.assertEqual(crossed.data["connection_id"], first.data["connection_id"])
        self.assertEqual(crossed.data["member"]["directory_id"], str(self.viewer.person.community_profile.directory_id))
        self.assertEqual(CommunityConnection.objects.get().status, CommunityConnection.Status.PENDING)

    def test_detail_relationship_states_and_contact_privacy(self):
        detail_url = f"/api/v1/community/directory/{self.target_directory_url()}/"
        response = self.client.get(detail_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["relationship"]["state"], "NO_RELATIONSHIP")
        self.assertFalse(response.data["relationship"]["can_accept"])
        self.assertEqual(response.data["contact"], {"email": None, "mobile": None})

        request_response = self.send_request()
        self.assertEqual(request_response.status_code, 200)
        response = self.client.get(detail_url)
        self.assertEqual(response.data["relationship"]["state"], "OUTGOING_PENDING")
        self.assertEqual(response.data["contact"], {"email": None, "mobile": None})

        connection_id = request_response.data["connection_id"]
        self.client.force_authenticate(user=self.target)
        incoming = self.client.get(f"{self.requests_url}?direction=incoming")
        self.assertEqual(incoming.status_code, 200)
        self.assertEqual(incoming.data["results"][0]["state"], "INCOMING_PENDING")
        self.assertEqual(incoming.data["results"][0]["member"]["directory_id"], str(self.viewer.person.community_profile.directory_id))
        accepted = self.client.post(f"{self.connections_url}{connection_id}/accept/")
        self.assertEqual(accepted.status_code, 200)

        self.client.force_authenticate(user=self.viewer)
        response = self.client.get(detail_url)
        self.assertEqual(response.data["relationship"]["state"], "CONNECTED")
        self.assertEqual(response.data["contact"], {"email": "ben@example.com", "mobile": self.target.person.mobile})

    def test_public_contact_flags_are_independent_and_connection_only_contact_is_removed(self):
        profile = self.target.person.community_profile
        profile.email_visible = True
        profile.mobile_visible = False
        profile.save(update_fields=["email_visible", "mobile_visible"])
        detail_url = f"/api/v1/community/directory/{self.target_directory_url()}/"

        public_contact = self.client.get(detail_url)
        self.assertEqual(public_contact.status_code, 200)
        self.assertEqual(public_contact.data["contact"], {"email": "ben@example.com", "mobile": None})

        profile.email_visible = False
        profile.mobile_visible = False
        profile.save(update_fields=["email_visible", "mobile_visible"])
        request_response = self.send_request()
        connection_id = request_response.data["connection_id"]
        self.client.force_authenticate(user=self.target)
        self.assertEqual(self.client.post(f"{self.connections_url}{connection_id}/accept/").status_code, 200)

        self.client.force_authenticate(user=self.viewer)
        connected = self.client.get(detail_url)
        self.assertEqual(connected.data["contact"], {"email": "ben@example.com", "mobile": self.target.person.mobile})
        self.assertEqual(self.client.delete(f"{self.connections_url}{connection_id}/").status_code, 204)
        disconnected = self.client.get(detail_url)
        self.assertEqual(disconnected.data["contact"], {"email": None, "mobile": None})

    def test_hidden_accepted_profile_is_available_only_to_connection(self):
        request_response = self.send_request()
        connection_id = request_response.data["connection_id"]
        self.client.force_authenticate(user=self.target)
        self.assertEqual(self.client.post(f"{self.connections_url}{connection_id}/accept/").status_code, 200)
        self.target.person.community_profile.directory_visible = False
        self.target.person.community_profile.email_visible = False
        self.target.person.community_profile.mobile_visible = False
        self.target.person.community_profile.save(update_fields=["directory_visible", "email_visible", "mobile_visible"])

        self.client.force_authenticate(user=self.viewer)
        hidden_url = f"/api/v1/community/directory/{self.target_directory_url()}/"
        response = self.client.get(hidden_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["relationship"]["state"], "CONNECTED")
        self.assertEqual(response.data["contact"], {"email": "ben@example.com", "mobile": self.target.person.mobile})

        self.client.force_authenticate(user=self.other)
        self.assertEqual(self.client.get(hidden_url).status_code, 404)

    def test_pending_declined_and_disconnected_relationships_do_not_grant_hidden_access(self):
        request_response = self.send_request()
        connection_id = request_response.data["connection_id"]
        self.target.person.community_profile.directory_visible = False
        self.target.person.community_profile.save(update_fields=["directory_visible"])
        hidden_url = f"/api/v1/community/directory/{self.target_directory_url()}/"
        self.assertEqual(self.client.get(hidden_url).status_code, 404)

        self.client.force_authenticate(user=self.target)
        self.assertEqual(self.client.post(f"{self.connections_url}{connection_id}/decline/").status_code, 200)
        self.client.force_authenticate(user=self.viewer)
        self.assertEqual(self.client.get(hidden_url).status_code, 404)

        self.target.person.community_profile.directory_visible = True
        self.target.person.community_profile.save(update_fields=["directory_visible"])
        reopened = self.send_request()
        self.client.force_authenticate(user=self.target)
        self.assertEqual(self.client.post(f"{self.connections_url}{reopened.data['connection_id']}/accept/").status_code, 200)
        self.client.force_authenticate(user=self.viewer)
        self.assertEqual(self.client.delete(f"{self.connections_url}{reopened.data['connection_id']}/").status_code, 204)
        self.target.person.community_profile.directory_visible = False
        self.target.person.community_profile.save(update_fields=["directory_visible"])
        self.assertEqual(self.client.get(hidden_url).status_code, 404)

    def test_connection_lists_and_eligibility_loss(self):
        request_response = self.send_request()
        connection_id = request_response.data["connection_id"]
        self.client.force_authenticate(user=self.target)
        self.assertEqual(self.client.post(f"{self.connections_url}{connection_id}/accept/").status_code, 200)
        self.client.force_authenticate(user=self.viewer)
        connections = self.client.get(self.connections_url)
        self.assertEqual(connections.status_code, 200)
        self.assertEqual(len(connections.data["results"]), 1)
        self.assertNotIn("contact", connections.data["results"][0]["member"])
        self.target.person.membership.status = Membership.Status.FORMER
        self.target.person.membership.ended_at = timezone.localdate()
        self.target.person.membership.save(update_fields=["status", "ended_at"])
        self.assertEqual(self.client.get(self.connections_url).data["count"], 0)
        self.assertTrue(CommunityConnection.objects.filter(public_id=connection_id, status=CommunityConnection.Status.ACCEPTED).exists())

    def test_decline_and_remove_are_authorized_and_audited_without_contact_pii(self):
        request_response = self.send_request()
        connection_id = request_response.data["connection_id"]
        self.client.force_authenticate(user=self.viewer)
        self.assertEqual(self.client.post(f"{self.connections_url}{connection_id}/decline/").status_code, 409)
        self.client.force_authenticate(user=self.target)
        declined = self.client.post(f"{self.connections_url}{connection_id}/decline/")
        self.assertEqual(declined.status_code, 200)
        self.assertEqual(self.client.post(f"{self.connections_url}{connection_id}/accept/").status_code, 409)
        self.assertEqual(self.client.delete(f"{self.connections_url}{connection_id}/").status_code, 409)
        events = AuditEvent.objects.filter(entity_type="CommunityConnection")
        self.assertEqual(events.count(), 2)
        for event in events:
            self.assertEqual(event.metadata["source"], "COMMUNITY_CONNECTIONS")
            self.assertNotIn("ben@example.com", str(event.changes))
            self.assertNotIn("+447700900", str(event.changes))

    def test_scopes_are_configured(self):
        self.assertEqual(settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]["community_connections"], "60/hour")
        self.assertEqual(settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]["community_connection_requests"], "60/hour")
        self.assertEqual(settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]["community_connection_create"], "10/hour")
        self.assertEqual(settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]["community_connection_mutations"], "30/hour")


class CommunityConnectionConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.viewer = self.create_member("Amina", "Viewer", "concurrent-amina@example.com")
        self.target = self.create_member("Ben", "Target", "concurrent-ben@example.com")
        self.barrier = Barrier(2)

    def create_member(self, first_name, last_name, email):
        person = Person.objects.create(
            first_name=first_name,
            last_name=last_name,
            primary_email=email,
        )
        Membership.objects.create(
            person=person,
            status=Membership.Status.ACTIVE,
            joined_at=timezone.localdate(),
            membership_source=Membership.Source.COMMUNITY_PLATFORM,
        )
        user = User.objects.create_user(email=email, password="Strong-password-123!", person=person)
        CommunityProfile.objects.create(person=person, directory_visible=True)
        return user

    def submit(self, user, target):
        close_old_connections()
        try:
            self.barrier.wait(timeout=10)
            request = RequestFactory().post("/api/v1/community/connections/requests/", data={}, content_type="application/json")
            request.user = user
            with transaction.atomic():
                return send_connection_request(
                    request=request,
                    directory_id=target.person.community_profile.directory_id,
                ).public_id
        finally:
            close_old_connections()

    def test_crossed_requests_create_one_pair_on_postgresql(self):
        if connection.vendor != "postgresql":
            self.skipTest("PostgreSQL row-lock concurrency is not equivalent on this database.")

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(self.submit, self.viewer, self.target),
                executor.submit(self.submit, self.target, self.viewer),
            ]
            public_ids = [future.result(timeout=30) for future in futures]

        self.assertEqual(public_ids[0], public_ids[1])
        self.assertEqual(CommunityConnection.objects.count(), 1)
        self.assertEqual(CommunityConnection.objects.get().status, CommunityConnection.Status.PENDING)
