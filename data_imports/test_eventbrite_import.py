from django.test import TestCase
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import User
from audit.models import AuditEvent
from data_imports.models import ImportBatch, ImportRecord
from data_imports.services.eventbrite_import import import_eventbrite_batch
from data_imports.services.membership_form_import import ImportBatchNotImportable, ImportBatchPreflightError
from events.models import Event, EventParticipation, ExternalEventReference
from memberships.models import Membership
from people.models import Person
from staff_access.models import StaffRole, StaffRoleAssignment


class EventbriteImportServiceTests(TestCase):
    def setUp(self):
        self.batch = ImportBatch.objects.create(
            source_type=ImportBatch.SourceType.EVENTBRITE,
            source_filename="eventbrite.xlsx",
            source_fingerprint="e" * 64,
            status=ImportBatch.Status.READY_FOR_IMPORT,
        )

    def source(self, **person_overrides):
        person = {
            "first_name": "Amina",
            "last_name": "Zulu",
            "email": "amina@example.com",
            "mobile": "0791234567",
            "city": "Lilongwe",
        }
        person.update(person_overrides)
        return {
            "person": person,
            "event": {
                "external_event_id": "1085298078769",
                "name": "MK Professionals Meet Up",
                "start_at": "2024-08-31T18:30:00+02:00",
                "timezone": "Africa/Blantyre",
                "location_name": "Bingu Conference Centre",
            },
            "source": {"provider": "EVENTBRITE", "external_order_id": "ORDER-123", "ticket_quantity": 3},
        }

    def record(self, method=ImportRecord.ResolutionMethod.NO_MATCH, *, person=None, source=None, status=ImportRecord.Status.RESOLVED):
        sequence = ImportRecord.objects.count() + 1
        return ImportRecord.objects.create(
            batch=self.batch,
            source_row_identifier=f"row-{sequence}",
            source_fingerprint=str(sequence).zfill(64),
            status=status,
            resolution_method=method,
            resolved_person=person,
            normalized_data=self.source() if source is None else source,
        )

    def test_import_creates_business_person_event_reference_and_one_registered_participation(self):
        record = self.record()

        result = import_eventbrite_batch(batch_id=self.batch.id)

        record.refresh_from_db()
        event = Event.objects.get()
        participation = EventParticipation.objects.get()
        self.assertEqual(result.people_created, 1)
        self.assertEqual(result.events_created, 1)
        self.assertEqual(result.participations_created, 1)
        self.assertEqual(record.resolved_person.record_type, Person.RecordType.BUSINESS)
        self.assertFalse(Membership.objects.filter(person=record.resolved_person).exists())
        self.assertEqual(participation.status, EventParticipation.Status.REGISTERED)
        self.assertIsNone(participation.ticket_quantity)
        self.assertTrue(ExternalEventReference.objects.filter(
            provider="EVENTBRITE",
            reference_type=ExternalEventReference.ReferenceType.EVENT,
            external_id="1085298078769",
            event=event,
        ).exists())
        self.assertFalse(ExternalEventReference.objects.filter(
            reference_type=ExternalEventReference.ReferenceType.PARTICIPATION
        ).exists())
        self.assertEqual(record.status, ImportRecord.Status.COMMITTED)
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.status, ImportBatch.Status.IMPORTED)

    def test_multiple_rows_reuse_one_event_and_one_participation_without_using_order_or_ticket_quantity_as_identity(self):
        first = self.record()
        self.record(source=self.source())

        result = import_eventbrite_batch(batch_id=self.batch.id)

        self.assertEqual(result.events_created, 1)
        self.assertEqual(result.events_reused, 1)
        self.assertEqual(result.participations_created, 1)
        self.assertEqual(result.participations_reused, 1)
        self.assertEqual(Event.objects.count(), 1)
        self.assertEqual(EventParticipation.objects.count(), 1)
        first.refresh_from_db()
        self.assertEqual(first.resolved_person_id, ImportRecord.objects.exclude(pk=first.pk).get().resolved_person_id)

    def test_repeated_no_match_buyer_across_different_events_creates_one_person_and_two_participations(self):
        first = self.record()
        second_source = self.source()
        second_source["event"] = {**second_source["event"], "external_event_id": "event-2", "name": "Second Event"}
        second = self.record(source=second_source)

        result = import_eventbrite_batch(batch_id=self.batch.id)

        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(result.people_created, 1)
        self.assertEqual(first.resolved_person_id, second.resolved_person_id)
        self.assertEqual(Event.objects.count(), 2)
        self.assertEqual(EventParticipation.objects.count(), 2)

    def test_name_only_equal_no_match_buyers_are_not_merged(self):
        first_source = self.source(email="", mobile="")
        second_source = self.source(email="", mobile="")
        second_source["event"] = {**second_source["event"], "external_event_id": "event-2", "name": "Second Event"}
        first = self.record(source=first_source)
        second = self.record(source=second_source)

        result = import_eventbrite_batch(batch_id=self.batch.id)

        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(result.people_created, 2)
        self.assertNotEqual(first.resolved_person_id, second.resolved_person_id)

    def test_conflicting_same_batch_create_new_identity_fails_before_mutation(self):
        self.record(source=self.source(mobile="0791234567"))
        conflicting_source = self.source(mobile="0797654321")
        conflicting_source["event"] = {**conflicting_source["event"], "external_event_id": "event-2", "name": "Second Event"}
        self.record(source=conflicting_source)

        with self.assertRaises(ImportBatchPreflightError):
            import_eventbrite_batch(batch_id=self.batch.id)

        self.assertFalse(Person.objects.exists())
        self.assertFalse(Event.objects.exists())
        self.assertFalse(EventParticipation.objects.exists())

    def test_reimport_is_rejected_without_duplicate_records_or_audit_events(self):
        self.record()
        import_eventbrite_batch(batch_id=self.batch.id)

        with self.assertRaises(ImportBatchNotImportable):
            import_eventbrite_batch(batch_id=self.batch.id)

        self.assertEqual(Person.objects.count(), 1)
        self.assertEqual(Event.objects.count(), 1)
        self.assertEqual(EventParticipation.objects.count(), 1)
        self.assertEqual(ExternalEventReference.objects.count(), 1)
        self.assertEqual(AuditEvent.objects.filter(
            action=AuditEvent.Action.IMPORT_BATCH_IMPORTED,
            entity_id=str(self.batch.id),
        ).count(), 1)

    def test_provider_namespace_does_not_reuse_another_provider_event_reference(self):
        community_event = Event.objects.create(
            name="Community Event", start_at="2024-08-30T16:30:00Z", timezone="Africa/Blantyre"
        )
        ExternalEventReference.objects.create(
            provider="COMMUNITY",
            reference_type=ExternalEventReference.ReferenceType.EVENT,
            external_id="1085298078769",
            event=community_event,
        )
        self.record()

        result = import_eventbrite_batch(batch_id=self.batch.id)

        self.assertEqual(result.events_created, 1)
        self.assertEqual(Event.objects.count(), 2)

    def test_inconsistent_existing_event_reference_blocks_the_whole_batch(self):
        event = Event.objects.create(name="Existing", start_at="2024-08-31T16:30:00Z", timezone="Africa/Blantyre")
        person = Person.objects.create(first_name="Existing", last_name="Person")
        participation = EventParticipation.objects.create(event=event, person=person)
        ExternalEventReference.objects.create(
            provider="EVENTBRITE",
            reference_type=ExternalEventReference.ReferenceType.EVENT,
            external_id="1085298078769",
            event=event,
            participation=participation,
        )
        self.record()

        with self.assertRaises(ImportBatchPreflightError):
            import_eventbrite_batch(batch_id=self.batch.id)

        self.assertEqual(Person.objects.count(), 1)
        self.assertEqual(Event.objects.count(), 1)
        self.assertEqual(EventParticipation.objects.count(), 1)

    def test_existing_participation_status_and_membership_are_preserved(self):
        person = Person.objects.create(first_name="Amina", last_name="Zulu", primary_email="amina@example.com")
        membership = Membership.objects.create(
            person=person,
            status=Membership.Status.FORMER,
            joined_at="2020-01-01",
            ended_at="2021-01-01",
            membership_source=Membership.Source.STAFF,
        )
        event = Event.objects.create(name="Existing", start_at="2024-08-31T16:30:00Z", timezone="Africa/Blantyre")
        participation = EventParticipation.objects.create(
            event=event, person=person, status=EventParticipation.Status.ATTENDED
        )
        ExternalEventReference.objects.create(
            provider="EVENTBRITE",
            reference_type=ExternalEventReference.ReferenceType.EVENT,
            external_id="1085298078769",
            event=event,
        )
        self.record(ImportRecord.ResolutionMethod.AUTO_MATCH, person=person)

        result = import_eventbrite_batch(batch_id=self.batch.id)

        membership.refresh_from_db()
        participation.refresh_from_db()
        self.assertEqual(result.people_matched, 1)
        self.assertEqual(result.participations_preserved, 1)
        self.assertEqual(membership.status, Membership.Status.FORMER)
        self.assertEqual(participation.status, EventParticipation.Status.ATTENDED)

    def test_active_membership_is_untouched_and_does_not_block_participation_creation(self):
        person = Person.objects.create(first_name="Amina", last_name="Zulu", primary_email="amina@example.com")
        membership = Membership.objects.create(
            person=person,
            status=Membership.Status.ACTIVE,
            joined_at="2020-01-01",
            membership_source=Membership.Source.STAFF,
        )
        self.record(ImportRecord.ResolutionMethod.AUTO_MATCH, person=person)

        result = import_eventbrite_batch(batch_id=self.batch.id)

        membership.refresh_from_db()
        self.assertEqual(result.participations_created, 1)
        self.assertEqual(membership.status, Membership.Status.ACTIVE)

    def test_existing_registered_and_cancelled_participations_are_reused_without_status_changes(self):
        for participation_status in (EventParticipation.Status.REGISTERED, EventParticipation.Status.CANCELLED):
            with self.subTest(participation_status=participation_status):
                batch = ImportBatch.objects.create(
                    source_type=ImportBatch.SourceType.EVENTBRITE,
                    source_filename=f"{participation_status}.xlsx",
                    source_fingerprint=f"{participation_status.lower():0<64}",
                    status=ImportBatch.Status.READY_FOR_IMPORT,
                )
                person = Person.objects.create(
                    first_name=participation_status, last_name="Person", primary_email=f"{participation_status.lower()}@example.com"
                )
                event = Event.objects.create(
                    name=participation_status, start_at="2024-08-31T16:30:00Z", timezone="Africa/Blantyre"
                )
                participation = EventParticipation.objects.create(event=event, person=person, status=participation_status)
                ExternalEventReference.objects.create(
                    provider="EVENTBRITE",
                    reference_type=ExternalEventReference.ReferenceType.EVENT,
                    external_id=f"event-{participation_status}",
                    event=event,
                )
                ImportRecord.objects.create(
                    batch=batch,
                    source_row_identifier="row-1",
                    source_fingerprint=f"{participation_status.lower():0<64}",
                    status=ImportRecord.Status.RESOLVED,
                    resolution_method=ImportRecord.ResolutionMethod.AUTO_MATCH,
                    resolved_person=person,
                    normalized_data={
                        "person": {"first_name": person.first_name, "last_name": person.last_name, "email": person.primary_email, "mobile": ""},
                        "event": {"external_event_id": f"event-{participation_status}", "name": event.name, "start_at": "2024-08-31T18:30:00+02:00", "timezone": "Africa/Blantyre", "location_name": ""},
                        "source": {"provider": "EVENTBRITE", "external_order_id": "ORDER-1", "ticket_quantity": 4},
                    },
                )

                result = import_eventbrite_batch(batch_id=batch.id)

                participation.refresh_from_db()
                self.assertEqual(participation.status, participation_status)
                self.assertEqual(result.participations_reused if participation_status == EventParticipation.Status.REGISTERED else result.participations_preserved, 1)

    def test_invalid_and_stale_create_new_records_prevent_partial_mutation(self):
        self.record(ImportRecord.ResolutionMethod.STAFF_CREATE_NEW)
        record = self.batch.records.get()
        record.match_evidence = {"staff_create_new_review": {"collision": "MOBILE_COLLISION", "matched_person_ids": [999]}}
        record.save(update_fields=["match_evidence"])

        with self.assertRaises(ImportBatchPreflightError):
            import_eventbrite_batch(batch_id=self.batch.id)

        self.assertEqual(Person.objects.count(), 0)
        self.assertEqual(Event.objects.count(), 0)
        self.assertEqual(EventParticipation.objects.count(), 0)
        self.assertEqual(self.batch.records.get().status, ImportRecord.Status.RESOLVED)

    def test_invalid_records_are_skipped_without_creating_authoritative_records(self):
        self.record(status=ImportRecord.Status.INVALID)

        result = import_eventbrite_batch(batch_id=self.batch.id)

        record = self.batch.records.get()
        self.assertEqual(result.invalid_skipped, 1)
        self.assertEqual(record.status, ImportRecord.Status.INVALID)
        self.assertEqual(record.outcome, ImportRecord.Outcome.SKIPPED)
        self.assertFalse(Person.objects.exists())


class EventbriteImportApiTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            email="event-import-admin@example.com",
            password="safe-password",
            person_first_name="Event",
            person_last_name="Admin",
        )
        self.manager = User.objects.create_user(
            email="event-import-manager@example.com",
            password="safe-password",
            person_first_name="Event",
            person_last_name="Manager",
        )
        admin_role = StaffRole.objects.create(code=StaffRole.CRM_ADMIN, name="CRM Administrator")
        manager_role = StaffRole.objects.create(code=StaffRole.CRM_MANAGER, name="CRM Manager")
        StaffRoleAssignment.objects.assign_role(user=self.admin, role=admin_role)
        StaffRoleAssignment.objects.assign_role(user=self.manager, role=manager_role)

    def test_import_endpoint_dispatches_eventbrite_only_for_crm_admin_and_returns_safe_event_counts(self):
        batch = ImportBatch.objects.create(
            source_type=ImportBatch.SourceType.EVENTBRITE,
            source_filename="eventbrite.xlsx",
            source_fingerprint="a" * 64,
            status=ImportBatch.Status.READY_FOR_IMPORT,
        )
        ImportRecord.objects.create(
            batch=batch,
            source_row_identifier="row-1",
            source_fingerprint="1" * 64,
            status=ImportRecord.Status.RESOLVED,
            resolution_method=ImportRecord.ResolutionMethod.NO_MATCH,
            normalized_data={
                "person": {"first_name": "Amina", "last_name": "Zulu", "email": "amina@example.com", "mobile": ""},
                "event": {
                    "external_event_id": "1085298078769", "name": "Meet Up",
                    "start_at": "2024-08-31T18:30:00+02:00", "timezone": "Africa/Blantyre", "location_name": "",
                },
                "source": {"provider": "EVENTBRITE", "external_order_id": "ORDER-123"},
            },
        )
        url = f"/api/v1/imports/{batch.id}/import/"

        self.assertEqual(self.client.post(url, {}, format="json").status_code, status.HTTP_401_UNAUTHORIZED)
        self.client.force_authenticate(user=self.manager)
        self.assertEqual(self.client.post(url, {}, format="json").status_code, status.HTTP_403_FORBIDDEN)
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(url, {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["result"]["events_created_count"], 1)
        self.assertEqual(response.data["result"]["participations_created_count"], 1)
        self.assertNotIn("amina@example.com", str(response.data))
        audit_event = AuditEvent.objects.get(action=AuditEvent.Action.IMPORT_BATCH_IMPORTED, entity_id=str(batch.id))
        self.assertEqual(audit_event.metadata["source_type"], ImportBatch.SourceType.EVENTBRITE)
        self.assertNotIn("external_order_id", str(audit_event.metadata))
