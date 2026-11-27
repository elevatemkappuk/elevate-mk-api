from datetime import timedelta

from django.contrib.admin.sites import AdminSite
from django.test import TestCase
from django.utils import timezone

from accounts.models import User
from audit.admin import AuditEventAdmin
from audit.models import AuditEvent, AuditEventSubject, LegalHold
from audit.policies import get_privacy_audit_queryset_for_user
from audit.privacy import approve_legal_hold_release, create_legal_hold, evaluate_retention, request_legal_hold_release
from audit.services import record_audit_event
from people.models import Person
from staff_access.models import CapabilityDefinition, StaffCapabilityAssignment, StaffRole, StaffRoleAssignment


class PrivacyAuditFoundationTests(TestCase):
    def setUp(self):
        self.person = Person.objects.create(first_name="Audit", last_name="Subject")
        self.superuser = User.objects.create_user(
            email="privacy-superuser@example.com",
            password="testpass123",
            is_staff=True,
            is_superuser=True,
            person=self.person,
        )
        self.other_superuser = User.objects.create_user(
            email="privacy-approver@example.com",
            password="testpass123",
            is_staff=True,
            is_superuser=True,
            person=Person.objects.create(first_name="Approver", last_name="User"),
        )

    def test_raw_audit_admin_is_superuser_only(self):
        admin = AuditEventAdmin(AuditEvent, AdminSite())
        ordinary = User.objects.create_user(
            email="technical@example.com",
            password="testpass123",
            person_first_name="Technical",
            person_last_name="User",
            is_staff=True,
        )
        request = type("Request", (), {"user": ordinary})()
        super_request = type("Request", (), {"user": self.superuser})()
        self.assertFalse(admin.has_view_permission(request))
        self.assertTrue(admin.has_view_permission(super_request))

    def test_record_event_creates_normalized_person_subject(self):
        event = record_audit_event(
            action=AuditEvent.Action.PERSON_UPDATED,
            actor_user=self.superuser,
            entity_type="Person",
            entity_id=self.person.pk,
            metadata={"person_id": str(self.person.pk)},
        )
        self.assertTrue(AuditEventSubject.objects.filter(event=event, person=self.person).exists())

    def test_privacy_payload_rejects_nested_secret(self):
        with self.assertRaises(ValueError):
            record_audit_event(
                action=AuditEvent.Action.LEGAL_HOLD_PLACED,
                actor_user=self.superuser,
                entity_type="LegalHold",
                entity_id="hold-1",
                metadata={"context": {"token": "secret"}},
                audit_domain=AuditEvent.Domain.PRIVACY,
            )

    def test_person_hold_covers_future_subject_events_and_release_requires_distinct_actor(self):
        hold = create_legal_hold(actor_user=self.superuser, person=self.person, reason="Investigation")
        from audit.models import RetentionPolicyVersion
        RetentionPolicyVersion.objects.create(
            audit_domain=AuditEvent.Domain.PRIVACY,
            retention_class="PRIVACY",
            version=1,
            retention_days=730,
            effective_at=timezone.now() - timedelta(days=1),
            reason="Test policy",
            created_by=self.superuser,
        )
        event = AuditEvent.objects.create(
            action=AuditEvent.Action.PERSON_UPDATED,
            actor_user=self.superuser,
            entity_type="Person",
            entity_id=self.person.pk,
            audit_domain=AuditEvent.Domain.PRIVACY,
            retention_class="PRIVACY",
            retention_policy_version=1,
        )
        AuditEventSubject.objects.create(event=event, person=self.person)
        request = request_legal_hold_release(actor_user=self.superuser, hold=hold, reason="Review complete")
        self.assertIn(str(event.pk), evaluate_retention(actor_user=self.superuser)["held"])
        approved = approve_legal_hold_release(actor_user=self.other_superuser, release_request=request, reason="Independent review")
        self.assertEqual(approved.status, LegalHold.Status.RELEASED)
        self.assertTrue(AuditEvent.objects.filter(pk=event.pk).exists())

    def test_dry_run_uses_creation_timestamp_and_opaque_ids(self):
        now = timezone.now()
        event = AuditEvent.objects.create(
            action=AuditEvent.Action.LEGAL_HOLD_PLACED,
            actor_user=self.superuser,
            entity_type="LegalHold",
            entity_id="opaque-hold",
            audit_domain=AuditEvent.Domain.OPERATIONAL,
            retention_class="PRIVACY",
            retention_policy_version=1,
            occurred_at=now - timedelta(days=800),
        )
        from audit.models import RetentionPolicyVersion
        RetentionPolicyVersion.objects.create(
            audit_domain=AuditEvent.Domain.OPERATIONAL,
            retention_class="PRIVACY",
            version=1,
            retention_days=730,
            effective_at=now - timedelta(days=900),
            reason="Test policy",
            created_by=self.superuser,
        )
        report = evaluate_retention(actor_user=self.superuser, evaluation_timestamp=now)
        self.assertIn(str(event.pk), report["eligible"])
        self.assertNotIn("opaque-hold", report["eligible"])

    def test_privacy_projection_requires_capability_and_person_scope(self):
        ordinary = User.objects.create_user(
            email="ordinary-privacy@example.com",
            password="testpass123",
            person=Person.objects.create(first_name="Ordinary", last_name="Viewer"),
        )
        self.assertFalse(get_privacy_audit_queryset_for_user(ordinary, person=self.person).exists())

        privacy_event = AuditEvent.objects.create(
            action=AuditEvent.Action.LEGAL_HOLD_PLACED,
            actor_user=self.superuser,
            entity_type="LegalHold",
            entity_id="hold-1",
            metadata={"person_id": str(self.person.pk)},
            audit_domain=AuditEvent.Domain.PRIVACY,
        )
        AuditEvent.objects.create(
            action=AuditEvent.Action.PERSON_UPDATED,
            actor_user=self.superuser,
            entity_type="Person",
            entity_id=str(self.person.pk),
            metadata={"person_id": str(self.person.pk)},
            audit_domain=AuditEvent.Domain.OPERATIONAL,
        )
        projection = get_privacy_audit_queryset_for_user(self.superuser, person=self.person)
        self.assertEqual(list(projection.values_list("id", flat=True)), [privacy_event.id])

        crm_admin = User.objects.create_user(
            email="privacy-admin@example.com",
            password="testpass123",
            person=Person.objects.create(
                first_name="Privacy",
                last_name="Admin",
                record_type=Person.RecordType.STAFF,
            ),
        )
        StaffRoleAssignment.objects.assign_role(
            user=crm_admin,
            role=StaffRole.objects.get(code=StaffRole.CRM_ADMIN),
            assigned_by=self.superuser,
        )
        StaffCapabilityAssignment.objects.create(
            user=crm_admin,
            capability=CapabilityDefinition.objects.get(code=CapabilityDefinition.PRIVACY_AUDIT_READ),
            granted_by=self.superuser,
        )
        self.assertEqual(
            list(get_privacy_audit_queryset_for_user(crm_admin, person=self.person).values_list("id", flat=True)),
            [privacy_event.id],
        )

    def test_privacy_projection_cannot_cross_record_scope(self):
        technical_person = Person.objects.create(
            first_name="Technical",
            last_name="Subject",
            record_type=Person.RecordType.TECHNICAL,
        )
        projection = get_privacy_audit_queryset_for_user(self.superuser, person=technical_person)
        self.assertFalse(projection.exists())
