from django.core.exceptions import ValidationError
import threading
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch

from django.db import close_old_connections, connection
from django.test import TestCase, TransactionTestCase

from accounts.models import User
from audit.models import AuditEvent
from people.models import Person
from staff_access.authorization import has_usable_crm_administrator
from staff_access.exceptions import FinalCrmAdminProtectionError
from staff_access.models import StaffRole, StaffRoleAssignment
from staff_access.services import replace_staff_role


class StaffRoleIntegrityTests(TestCase):
    password = "testpass123"

    def setUp(self):
        self.bootstrap = User.objects.create_superuser(
            email="role-integrity-bootstrap@example.com",
            password=self.password,
            person_first_name="Role",
            person_last_name="Bootstrap",
            person_record_type=Person.RecordType.STAFF,
        )
        self.actor = User.objects.create_user(
            email="role-integrity-actor@example.com",
            password=self.password,
            person_first_name="Role",
            person_last_name="Actor",
            person_record_type=Person.RecordType.STAFF,
        )
        self.target = User.objects.create_user(
            email="role-integrity-target@example.com",
            password=self.password,
            person_first_name="Role",
            person_last_name="Target",
            person_record_type=Person.RecordType.STAFF,
        )
        self.admin_role = StaffRole.objects.get(code=StaffRole.CRM_ADMIN)
        self.manager_role = StaffRole.objects.get(code=StaffRole.CRM_MANAGER)
        self.viewer_role = StaffRole.objects.get(code=StaffRole.CRM_VIEWER)
        StaffRoleAssignment.objects.assign_role(
            user=self.actor,
            role=self.admin_role,
            assigned_by=self.bootstrap,
        )
        StaffRoleAssignment.objects.assign_role(
            user=self.target,
            role=self.manager_role,
            assigned_by=self.bootstrap,
        )

    def test_replacement_is_atomic_and_preserves_history(self):
        previous_session_version = self.target.crm_session_version
        replacement = replace_staff_role(
            user=self.target,
            role=self.viewer_role,
            replaced_by=self.actor,
            reason="Least-privilege role adjustment",
        )

        self.assertEqual(replacement.role_id, self.viewer_role.pk)
        active = StaffRoleAssignment.objects.active().filter(user=self.target)
        self.assertEqual(active.count(), 1)
        self.assertEqual(active.get().role_id, self.viewer_role.pk)
        self.assertTrue(
            StaffRoleAssignment.objects.filter(
                user=self.target,
                role=self.manager_role,
                is_active=False,
            ).exists()
        )
        self.target.refresh_from_db()
        self.assertEqual(self.target.crm_session_version, previous_session_version + 1)
        events = AuditEvent.objects.filter(
            entity_type="StaffRoleAssignment",
            metadata__operation="CRM_ROLE_REPLACED",
        )
        self.assertEqual(events.count(), 2)

    def test_replacement_requires_reason_and_does_not_create_second_active_role(self):
        with self.assertRaises(ValidationError):
            replace_staff_role(
                user=self.target,
                role=self.viewer_role,
                replaced_by=self.actor,
                reason="",
            )
        self.assertEqual(StaffRoleAssignment.objects.active().filter(user=self.target).count(), 1)

    def test_replacement_audit_failure_rolls_back_role_and_session(self):
        previous_session_version = self.target.crm_session_version
        with patch("staff_access.services.record_audit_event", side_effect=RuntimeError("audit unavailable")):
            with self.assertRaises(RuntimeError):
                replace_staff_role(
                    user=self.target,
                    role=self.viewer_role,
                    replaced_by=self.actor,
                    reason="Audit rollback verification",
                )
        self.assertTrue(
            StaffRoleAssignment.objects.active().filter(
                user=self.target,
                role=self.manager_role,
            ).exists()
        )
        self.assertFalse(
            StaffRoleAssignment.objects.active().filter(
                user=self.target,
                role=self.viewer_role,
            ).exists()
        )
        self.target.refresh_from_db()
        self.assertEqual(self.target.crm_session_version, previous_session_version)

    def test_final_usable_administrator_cannot_be_replaced_or_revoked(self):
        self.assertTrue(has_usable_crm_administrator())
        with self.assertRaises(FinalCrmAdminProtectionError):
            replace_staff_role(
                user=self.actor,
                role=self.viewer_role,
                replaced_by=self.bootstrap,
                reason="Attempted final administrator demotion",
            )


@skipUnless(connection.vendor == "postgresql", "Requires PostgreSQL row-level locking semantics.")
class ConcurrentCrmRoleAssignmentTests(TransactionTestCase):
    def setUp(self):
        self.bootstrap = User.objects.create_superuser(
            email="role-concurrency-bootstrap@example.com",
            password="testpass123",
            person_first_name="Role",
            person_last_name="Bootstrap",
            person_record_type=Person.RecordType.STAFF,
        )
        self.actor = User.objects.create_user(
            email="role-concurrency-actor@example.com",
            password="testpass123",
            person_first_name="Role",
            person_last_name="Actor",
            person_record_type=Person.RecordType.STAFF,
        )
        StaffRoleAssignment.objects.assign_role(
            user=self.actor,
            role=StaffRole.CRM_ADMIN,
            assigned_by=self.bootstrap,
        )
        self.target = User.objects.create_user(
            email="role-concurrency-target@example.com",
            password="testpass123",
            person_first_name="Role",
            person_last_name="Target",
            person_record_type=Person.RecordType.STAFF,
        )

    def test_concurrent_assignments_leave_one_active_crm_role(self):
        barrier = Barrier(2)
        errors = []

        def assign(role_code):
            close_old_connections()
            try:
                barrier.wait()
                StaffRoleAssignment.objects.assign_role(
                    user=self.target,
                    role=role_code,
                    assigned_by=self.actor,
                )
            except Exception as error:
                errors.append(error)
            finally:
                close_old_connections()

        threads = [
            threading.Thread(target=assign, args=(StaffRole.CRM_MANAGER,)),
            threading.Thread(target=assign, args=(StaffRole.CRM_VIEWER,)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(
            StaffRoleAssignment.objects.active()
            .filter(user=self.target, role_family=StaffRole.CRM_ROLE_FAMILY)
            .count(),
            1,
        )
        self.assertEqual(len(errors), 1)
