from importlib import import_module

from django.apps import apps
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase

from accounts.models import User
from audit.models import AuditEvent, AuditEventSubject
from people.models import Person
from staff_access.models import CapabilityDefinition, CapabilityPolicyState, RoleCapabilityDefault


class NormalizedSubjectBackfillMigrationTests(TestCase):
    def test_backfill_preserves_valid_subject_and_skips_unresolved_references(self):
        person = Person.objects.create(
            first_name="Synthetic",
            last_name="Subject",
            record_type=Person.RecordType.COMMUNITY,
        )
        valid = AuditEvent.objects.create(
            action=AuditEvent.Action.PERSON_UPDATED,
            entity_type="Person",
            entity_id=str(person.pk),
            metadata={"person_id": str(person.pk)},
        )
        AuditEvent.objects.create(
            action=AuditEvent.Action.PERSON_UPDATED,
            entity_type="Person",
            entity_id="999999999",
            metadata={"person_id": "999999999"},
        )
        AuditEvent.objects.create(
            action=AuditEvent.Action.PERSON_UPDATED,
            entity_type="Person",
            entity_id="malformed",
            metadata={"person_id": "malformed"},
        )
        AuditEvent.objects.create(
            action=AuditEvent.Action.PERSON_UPDATED,
            entity_type="Person",
            entity_id="missing",
            metadata={},
        )

        migration = import_module(
            "audit.migrations.0022_auditeventsubject_legalhold_legalholdreleaserequest_and_more"
        )
        migration.backfill_normalized_subjects(apps, schema_editor=None)

        self.assertTrue(AuditEventSubject.objects.filter(event=valid, person=person).exists())
        self.assertEqual(AuditEventSubject.objects.count(), 1)

    def test_catalogue_extension_advances_policy_without_rewriting_baseline(self):
        state = CapabilityPolicyState.objects.get(singleton_key=1)
        self.assertEqual(CapabilityDefinition.objects.count(), 7)
        self.assertEqual(state.version, 2)
        self.assertEqual(
            AuditEvent.objects.filter(action=AuditEvent.Action.CAPABILITY_POLICY_BASELINED).count(),
            1,
        )
        self.assertEqual(
            AuditEvent.objects.filter(action=AuditEvent.Action.CAPABILITY_CATALOG_EXTENDED).count(),
            1,
        )
        extension = AuditEvent.objects.get(action=AuditEvent.Action.CAPABILITY_CATALOG_EXTENDED)
        self.assertEqual(extension.audit_domain, AuditEvent.Domain.PRIVACY)
        self.assertEqual(extension.provenance_source, AuditEvent.ProvenanceSource.MIGRATION)
        self.assertTrue(extension.provenance_trusted)
        self.assertEqual(extension.provenance_id, "staff_access.0008_legal_hold_admin")
        self.assertEqual(extension.metadata["actor_type"], "SYSTEM_MIGRATION")
        self.assertNotIn("LEGAL_HOLD_ADMIN", state.baseline_snapshot)
        self.assertFalse(
            RoleCapabilityDefault.objects.filter(capability__code="LEGAL_HOLD_ADMIN").exists()
        )


class LegalHoldReleaseRejectionMigrationTests(TransactionTestCase):
    reset_sequences = True

    migration_0022 = "0022_auditeventsubject_legalhold_legalholdreleaserequest_and_more"
    migration_0023 = "0023_legalhold_release_rejection"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.executor = MigrationExecutor(connection)
        cls.executor.migrate([("audit", cls.migration_0022)])
        cls.old_apps = cls.executor.loader.project_state(
            [("audit", cls.migration_0022)]
        ).apps

    @classmethod
    def tearDownClass(cls):
        cls.executor.migrate(cls.executor.loader.graph.leaf_nodes())
        connection.close()
        super().tearDownClass()

    def test_existing_pending_release_request_survives_rejection_migration(self):
        LegalHold = self.old_apps.get_model("audit", "LegalHold")
        ReleaseRequest = self.old_apps.get_model("audit", "LegalHoldReleaseRequest")

        person = Person.objects.create(
            first_name="Synthetic",
            last_name="Migration Subject",
            record_type="COMMUNITY",
        )
        user = User.objects.create_user(
            email="migration-release@example.com",
            password="testpass123",
            person=person,
        )
        hold = LegalHold.objects.create(
            scope="PERSON",
            status="ACTIVE",
            reason="Synthetic migration verification",
            person_id=person.pk,
            placed_by_id=user.pk,
        )
        release_request = ReleaseRequest.objects.create(
            legal_hold=hold,
            requested_by_id=user.pk,
            reason="Synthetic pending release",
        )

        self.executor = MigrationExecutor(connection)
        self.executor.migrate([("audit", self.migration_0023)])
        current_apps = self.executor.loader.project_state(
            [("audit", self.migration_0023)]
        ).apps
        current_request = current_apps.get_model("audit", "LegalHoldReleaseRequest").objects.get(
            pk=release_request.pk
        )

        self.assertEqual(current_request.status, "PENDING")
        self.assertEqual(current_request.reason, "Synthetic pending release")
        self.assertIsNone(current_request.rejected_at)
        self.assertIsNone(current_request.rejected_by_id)
        self.assertEqual(current_request.rejection_reason, "")
