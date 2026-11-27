from django.test import TestCase
from django.core.exceptions import PermissionDenied

from accounts.models import User
from audit.models import AuditEvent
from people.models import Person
from staff_access.authorization import (
    STAFF_AUTHORIZATION_DENIED_ACTION,
    StaffOperation,
    authorize_staff_operation,
    can_access_staff_management,
    is_protected_staff_target,
)
from staff_access.models import StaffRole, StaffRoleAssignment


class StaffAuthorizationPolicyTests(TestCase):
    password = "testpass123"

    @classmethod
    def setUpTestData(cls):
        cls.bootstrap = User.objects.create_superuser(
            email="phase2a-bootstrap@example.com",
            password=cls.password,
            person_first_name="Phase",
            person_last_name="Bootstrap",
        )
        cls.admin = User.objects.create_user(
            email="phase2a-admin@example.com",
            password=cls.password,
            person_first_name="Phase",
            person_last_name="Admin",
            person_record_type=Person.RecordType.STAFF,
        )
        StaffRoleAssignment.objects.assign_role(
            user=cls.admin,
            role=StaffRole.CRM_ADMIN,
            assigned_by=cls.bootstrap,
        )
        cls.manager = User.objects.create_user(
            email="phase2a-manager@example.com",
            password=cls.password,
            person_first_name="Phase",
            person_last_name="Manager",
            person_record_type=Person.RecordType.STAFF,
        )
        StaffRoleAssignment.objects.assign_role(
            user=cls.manager,
            role=StaffRole.CRM_MANAGER,
            assigned_by=cls.bootstrap,
        )
        cls.target = User.objects.create_user(
            email="phase2a-target@example.com",
            password=cls.password,
            person_first_name="Phase",
            person_last_name="Target",
            person_record_type=Person.RecordType.STAFF,
        )

    def test_only_active_crm_admin_or_eligible_superuser_has_staff_authority(self):
        self.assertTrue(can_access_staff_management(self.admin))
        self.assertFalse(can_access_staff_management(self.manager))
        self.assertFalse(can_access_staff_management(self.target))
        self.assertTrue(can_access_staff_management(self.bootstrap))

    def test_superuser_exception_does_not_create_operational_role(self):
        self.assertFalse(
            StaffRoleAssignment.objects.filter(
                user=self.bootstrap,
                is_active=True,
            ).exists()
        )

    def test_superuser_without_linked_person_is_not_eligible(self):
        self.bootstrap.person = None
        self.assertFalse(can_access_staff_management(self.bootstrap))

    def test_is_staff_only_and_disabled_superuser_are_not_eligible(self):
        technical = User.objects.create_user(
            email="phase2a-is-staff-only@example.com",
            password=self.password,
            person_first_name="Phase",
            person_last_name="Technical",
            person_record_type=Person.RecordType.TECHNICAL,
            is_staff=True,
        )
        self.assertFalse(can_access_staff_management(technical))
        disabled = User.objects.create_superuser(
            email="phase2a-disabled-superuser@example.com",
            password=self.password,
            person_first_name="Phase",
            person_last_name="Disabled",
        )
        disabled.is_active = False
        self.assertFalse(can_access_staff_management(disabled))

    def test_disabled_or_revoked_authority_is_denied(self):
        self.admin.is_active = False
        self.assertFalse(can_access_staff_management(self.admin))
        self.admin.is_active = True
        backup = User.objects.create_user(
            email="phase2a-backup-admin@example.com",
            password=self.password,
            person_first_name="Phase",
            person_last_name="Backup",
            person_record_type=Person.RecordType.STAFF,
        )
        StaffRoleAssignment.objects.assign_role(
            user=backup,
            role=StaffRole.CRM_ADMIN,
            assigned_by=self.bootstrap,
        )
        assignment = StaffRoleAssignment.objects.get(user=self.admin)
        assignment.revoke(revoked_by=self.bootstrap)
        self.assertFalse(can_access_staff_management(self.admin))

    def test_technical_target_and_person_type_are_separate(self):
        self.assertFalse(is_protected_staff_target(self.target))
        self.target.person.record_type = Person.RecordType.TECHNICAL
        self.assertFalse(is_protected_staff_target(self.target))
        self.target.is_staff = True
        self.assertTrue(is_protected_staff_target(self.target))

    def test_crm_admin_cannot_mutate_self_or_protected_target(self):
        with self.assertRaises(PermissionDenied):
            authorize_staff_operation(
                actor=self.admin,
                target=self.admin,
                operation=StaffOperation.ROLE_CHANGE,
            )
        self.target.is_staff = True
        with self.assertRaises(PermissionDenied):
            authorize_staff_operation(
                actor=self.admin,
                target=self.target,
                operation=StaffOperation.ROLE_CHANGE,
            )
        self.assertEqual(
            AuditEvent.objects.filter(
                action=STAFF_AUTHORIZATION_DENIED_ACTION,
                metadata__event_type="STAFF_AUTHORIZATION_DENIED",
            ).count(),
            2,
        )

    def test_superuser_is_still_denied_self_management(self):
        with self.assertRaises(PermissionDenied):
            authorize_staff_operation(
                actor=self.bootstrap,
                target=self.bootstrap,
                operation=StaffOperation.ACCOUNT_DISABLE,
            )
