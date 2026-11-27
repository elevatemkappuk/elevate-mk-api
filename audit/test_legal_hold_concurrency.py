from threading import Barrier, Thread
from unittest import skipUnless

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, close_old_connections, connection
from django.test import TransactionTestCase

from accounts.models import User
from audit.models import AuditEvent, LegalHold, LegalHoldReleaseRequest
from audit.privacy import (
    approve_legal_hold_release,
    cancel_legal_hold_release,
    create_legal_hold,
    direct_release_legal_hold,
    reject_legal_hold_release,
    request_legal_hold_release,
)
from people.models import Person
from staff_access.governance import apply_superuser_capability_change
from staff_access.models import CapabilityDefinition, CapabilityPolicyState, StaffCapabilityAssignment, StaffRole
from staff_access.models import StaffRoleAssignment


@skipUnless(connection.vendor == "postgresql", "Requires PostgreSQL row-level locking semantics.")
class LegalHoldConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self._ensure_reference_state()
        self.admin = self._create_user("legal-hold-admin@example.com", "Admin")
        self.approver = self._create_user("legal-hold-approver@example.com", "Approver")
        self.superuser = self._create_user("legal-hold-superuser@example.com", "Superuser", is_superuser=True)
        self.target = Person.objects.create(first_name="Held", last_name="Person")
        role = StaffRole.objects.get(code=StaffRole.CRM_ADMIN)
        for user in (self.admin, self.approver, self.superuser):
            StaffRoleAssignment.objects.assign_role(user=user, role=role, assigned_by=self.superuser)
            StaffCapabilityAssignment.objects.create(
                user=user,
                capability=CapabilityDefinition.objects.get(code=CapabilityDefinition.LEGAL_HOLD_ADMIN),
                granted_by=self.superuser,
            )

    def _ensure_reference_state(self):
        for code, name in StaffRole.CANONICAL_ROLES:
            StaffRole.objects.update_or_create(code=code, defaults={"name": name, "is_active": True})
        for code, name, description in CapabilityDefinition.CANONICAL_CAPABILITIES:
            CapabilityDefinition.objects.update_or_create(
                code=code,
                defaults={"name": name, "description": description, "is_active": True},
            )
        CapabilityPolicyState.objects.get_or_create(
            singleton_key=1,
            defaults={
                "key": "capability-governance",
                "version": 1,
                "baseline_snapshot": [],
                "reason": "Test fixture governance baseline",
            },
        )
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT setval(
                    pg_get_serial_sequence('audit_auditevent', 'id'),
                    COALESCE(MAX(id), 1),
                    COUNT(*) > 0
                )
                FROM audit_auditevent
                """
            )

    def _create_user(self, email, last_name, is_superuser=False):
        return User.objects.create_user(
            email=email,
            password="testpass123",
            is_staff=is_superuser,
            is_superuser=is_superuser,
            person_first_name="Legal",
            person_last_name=last_name,
            person_record_type=Person.RecordType.STAFF,
        )

    def _hold_with_pending_request(self):
        hold = create_legal_hold(actor_user=self.admin, person=self.target, reason="Concurrent release test")
        request = request_legal_hold_release(actor_user=self.admin, hold=hold, reason="Please review release")
        return hold, request

    def _join(self, threads):
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
        self.assertFalse(any(thread.is_alive() for thread in threads))

    def test_concurrent_approval_and_rejection_preserve_one_terminal_decision(self):
        hold, release_request = self._hold_with_pending_request()
        barrier = Barrier(2)
        outcomes = []

        def approve():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                approve_legal_hold_release(actor_user=self.approver, release_request=release_request, reason="Approve review")
                outcomes.append("approved")
            except (ValidationError, PermissionDenied):
                outcomes.append("terminal")
            finally:
                close_old_connections()

        def reject():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                reject_legal_hold_release(actor_user=self.superuser, release_request=release_request, reason="Reject review")
                outcomes.append("rejected")
            except (ValidationError, PermissionDenied):
                outcomes.append("terminal")
            finally:
                close_old_connections()

        self._join([Thread(target=approve), Thread(target=reject)])
        self.assertEqual(len(outcomes), 2)
        release_request.refresh_from_db()
        hold.refresh_from_db()
        self.assertIn(release_request.status, {LegalHoldReleaseRequest.Status.APPROVED, LegalHoldReleaseRequest.Status.REJECTED})
        self.assertGreaterEqual(
            AuditEvent.objects.filter(
                action__in=[AuditEvent.Action.LEGAL_HOLD_RELEASED, AuditEvent.Action.LEGAL_HOLD_RELEASE_REQUESTED]
            ).count(),
            2,
        )

    def test_concurrent_cancellation_and_rejection_preserve_one_terminal_decision(self):
        hold, release_request = self._hold_with_pending_request()
        barrier = Barrier(2)
        outcomes = []

        def cancel():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                cancel_legal_hold_release(actor_user=self.admin, release_request=release_request, reason="Matter remains active")
                outcomes.append("cancelled")
            except (ValidationError, PermissionDenied):
                outcomes.append("terminal")
            finally:
                close_old_connections()

        def reject():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                reject_legal_hold_release(actor_user=self.superuser, release_request=release_request, reason="Hold remains required")
                outcomes.append("rejected")
            except (ValidationError, PermissionDenied):
                outcomes.append("terminal")
            finally:
                close_old_connections()

        self._join([Thread(target=cancel), Thread(target=reject)])
        self.assertEqual(len(outcomes), 2)
        release_request.refresh_from_db()
        self.assertIn(release_request.status, {LegalHoldReleaseRequest.Status.CANCELLED, LegalHoldReleaseRequest.Status.REJECTED})
        self.assertEqual(
            AuditEvent.objects.filter(
                entity_id=str(release_request.public_id),
                action__in=[AuditEvent.Action.LEGAL_HOLD_RELEASE_CANCELLED, AuditEvent.Action.LEGAL_HOLD_RELEASE_REQUESTED],
            ).count(),
            1,
        )

    def test_concurrent_approval_and_superuser_release_preserve_audited_terminal_state(self):
        hold, release_request = self._hold_with_pending_request()
        barrier = Barrier(2)
        outcomes = []

        def approve():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                approve_legal_hold_release(actor_user=self.approver, release_request=release_request, reason="Approve review")
                outcomes.append("approved")
            except (ValidationError, PermissionDenied):
                outcomes.append("terminal")
            finally:
                close_old_connections()

        def direct_release():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                direct_release_legal_hold(actor_user=self.superuser, hold=hold, reason="Direct emergency release")
                outcomes.append("direct")
            except (ValidationError, PermissionDenied):
                outcomes.append("terminal")
            finally:
                close_old_connections()

        self._join([Thread(target=approve), Thread(target=direct_release)])
        self.assertEqual(len(outcomes), 2)
        hold.refresh_from_db()
        release_request.refresh_from_db()
        self.assertEqual(hold.status, LegalHold.Status.RELEASED)
        self.assertEqual(release_request.status, LegalHoldReleaseRequest.Status.APPROVED)
        self.assertTrue(AuditEvent.objects.filter(entity_id=str(hold.public_id), action=AuditEvent.Action.LEGAL_HOLD_RELEASED).exists())

    def test_concurrent_release_requests_allow_only_one_pending_request(self):
        hold = create_legal_hold(actor_user=self.admin, person=self.target, reason="Concurrent request test")
        barrier = Barrier(2)
        outcomes = []

        def request_once():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                request_legal_hold_release(actor_user=self.admin, hold=hold, reason="Concurrent request")
                outcomes.append("created")
            except (ValidationError, IntegrityError):
                outcomes.append("rejected")
            finally:
                close_old_connections()

        self._join([Thread(target=request_once), Thread(target=request_once)])
        self.assertEqual(sorted(outcomes), ["created", "rejected"])
        self.assertEqual(
            LegalHoldReleaseRequest.objects.filter(legal_hold=hold, status=LegalHoldReleaseRequest.Status.PENDING).count(),
            1,
        )

    def test_capability_revocation_overlap_does_not_break_hold_or_audit_integrity(self):
        hold, release_request = self._hold_with_pending_request()
        barrier = Barrier(2)
        outcomes = []
        capability = CapabilityDefinition.objects.get(code=CapabilityDefinition.LEGAL_HOLD_ADMIN)

        def approve():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                approve_legal_hold_release(actor_user=self.approver, release_request=release_request, reason="Concurrent revocation review")
                outcomes.append("approved")
            except (ValidationError, PermissionDenied, RuntimeError):
                outcomes.append("denied")
            finally:
                close_old_connections()

        def revoke():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                apply_superuser_capability_change(
                    actor_user=self.superuser,
                    target_user=self.approver,
                    capability=capability,
                    operation="INDIVIDUAL_REVOKE",
                    reason="Revoke during legal-hold mutation",
                )
                outcomes.append("revoked")
            except (ValidationError, PermissionDenied, RuntimeError):
                outcomes.append("denied")
            finally:
                close_old_connections()

        self._join([Thread(target=approve), Thread(target=revoke)])
        self.assertEqual(len(outcomes), 2)
        release_request.refresh_from_db()
        hold.refresh_from_db()
        self.assertIn(release_request.status, {LegalHoldReleaseRequest.Status.PENDING, LegalHoldReleaseRequest.Status.APPROVED})
        if release_request.status == LegalHoldReleaseRequest.Status.APPROVED:
            self.assertEqual(hold.status, LegalHold.Status.RELEASED)
        self.assertTrue(
            AuditEvent.objects.filter(
                action=AuditEvent.Action.CAPABILITY_PERMISSION_MUTATED,
                entity_id__contains=str(self.approver.pk),
            ).exists()
        )
