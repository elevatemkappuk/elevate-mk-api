from datetime import timedelta

from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from events.models import Event, EventParticipation, ExternalEventReference
from events.services import (
    attach_participation_reference,
    get_or_create_event_from_reference,
    get_or_create_event_participation,
)
from memberships.models import Membership
from people.models import Person


class EventsDomainTests(TestCase):
    def setUp(self):
        self.starts_at = timezone.now()
        self.event = Event.objects.create(
            name="MK Professionals Meet Up",
            start_at=self.starts_at,
            timezone="Africa/Blantyre",
        )
        self.person_one = Person.objects.create(first_name="Amina", last_name="Zulu")
        self.person_two = Person.objects.create(first_name="Brian", last_name="Kali")

    def test_event_exists_without_provider_reference(self):
        self.assertEqual(self.event.name, "MK Professionals Meet Up")
        self.assertFalse(self.event.external_references.exists())

    def test_people_and_events_have_many_to_many_participation_through_model(self):
        second_event = Event.objects.create(
            name="Leadership Forum", start_at=self.starts_at + timedelta(days=1), timezone="Africa/Blantyre"
        )
        EventParticipation.objects.create(event=self.event, person=self.person_one)
        EventParticipation.objects.create(event=self.event, person=self.person_two)
        EventParticipation.objects.create(event=second_event, person=self.person_one)

        self.assertEqual(self.event.participations.count(), 2)
        self.assertEqual(self.person_one.event_participations.count(), 2)

    def test_participation_never_changes_membership_lifecycle(self):
        active_person = Person.objects.create(first_name="Active", last_name="Member")
        active = Membership.objects.create(
            person=active_person,
            status=Membership.Status.ACTIVE,
            joined_at="2025-01-01",
            membership_source=Membership.Source.STAFF,
        )
        former_person = Person.objects.create(first_name="Former", last_name="Member")
        former = Membership.objects.create(
            person=former_person,
            status=Membership.Status.FORMER,
            joined_at="2024-01-01",
            ended_at="2025-01-01",
            membership_source=Membership.Source.STAFF,
        )
        contact = Person.objects.create(first_name="Contact", last_name="Only")

        for person in (active_person, former_person, contact):
            EventParticipation.objects.create(event=self.event, person=person)

        active.refresh_from_db()
        former.refresh_from_db()
        self.assertEqual(active.status, Membership.Status.ACTIVE)
        self.assertEqual(former.status, Membership.Status.FORMER)
        self.assertFalse(Membership.objects.filter(person=contact).exists())

    def test_participation_is_idempotent_per_event_and_person_and_can_reference_archived_people(self):
        archived = Person.objects.create(first_name="Archived", last_name="Person", archived_at=timezone.now())
        participation, created = get_or_create_event_participation(event=self.event, person=archived)
        repeated, repeated_created = get_or_create_event_participation(event=self.event, person=archived)

        self.assertTrue(created)
        self.assertFalse(repeated_created)
        self.assertEqual(participation.id, repeated.id)
        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                EventParticipation.objects.create(event=self.event, person=archived)

    def test_provider_references_are_scoped_by_provider_and_type(self):
        participation = EventParticipation.objects.create(event=self.event, person=self.person_one)
        event_reference = ExternalEventReference.objects.create(
            provider="EVENTBRITE",
            reference_type=ExternalEventReference.ReferenceType.EVENT,
            external_id="1085298078769",
            event=self.event,
        )
        registration_reference = ExternalEventReference.objects.create(
            provider="EVENTBRITE",
            reference_type=ExternalEventReference.ReferenceType.PARTICIPATION,
            external_id="1085298078769",
            event=self.event,
            participation=participation,
        )
        community_reference = ExternalEventReference.objects.create(
            provider="COMMUNITY",
            reference_type=ExternalEventReference.ReferenceType.EVENT,
            external_id="1085298078769",
            event=self.event,
        )

        self.assertNotEqual(event_reference.id, registration_reference.id)
        self.assertNotEqual(event_reference.id, community_reference.id)
        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                ExternalEventReference.objects.create(
                    provider="EVENTBRITE",
                    reference_type=ExternalEventReference.ReferenceType.EVENT,
                    external_id="1085298078769",
                    event=self.event,
                )

    def test_repeated_provider_lookup_and_participation_provenance_reuse_authoritative_records(self):
        defaults = {"name": "Imported event", "start_at": self.starts_at, "timezone": "Africa/Blantyre"}
        event, created = get_or_create_event_from_reference(
            provider="EVENTBRITE", external_event_id="1085298078769", event_defaults=defaults
        )
        repeated_event, repeated_created = get_or_create_event_from_reference(
            provider="EVENTBRITE", external_event_id="1085298078769", event_defaults=defaults
        )
        participation, participation_created = get_or_create_event_participation(event=event, person=self.person_one)
        reference, reference_created = attach_participation_reference(
            provider="EVENTBRITE", external_participation_id="registration-1", event=event, participation=participation
        )
        repeated_reference, repeated_reference_created = attach_participation_reference(
            provider="EVENTBRITE", external_participation_id="registration-1", event=event, participation=participation
        )

        self.assertTrue(created)
        self.assertFalse(repeated_created)
        self.assertEqual(event.id, repeated_event.id)
        self.assertTrue(participation_created)
        self.assertTrue(reference_created)
        self.assertFalse(repeated_reference_created)
        self.assertEqual(reference.id, repeated_reference.id)
