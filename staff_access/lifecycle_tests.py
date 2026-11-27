from unittest.mock import patch

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase

from accounts.models import User
from accounts.revocation import issue_mobile_token_pair
from audit.models import AuditEvent
from config.test_utils import test_actor
from people.models import Person
from staff_access.models import (
    CapabilityDefinition,
    StaffCapabilityAssignment,
    StaffCapabilityRestriction,
    StaffRole,
    StaffRoleAssignment,
)
from staff_access.exceptions import FinalCrmAdminProtectionError
from staff_access.services import (
    deactivate_staff_access,
    disable_user_account,
    enable_user_account,
)


class StaffLifecycleFoundationTests(TestCase):
    def setUp(self):
        self.actor = test_actor()
        self.target = User.objects.create_user(
            email="lifecycle-target@example.com",
            password="testpass123",
            person_first_name="Lifecycle",
            person_last_name="Target",
            person_record_type=Person.RecordType.STAFF,
        )
        StaffRoleAssignment.objects.assign_role(
            user=self.target,
            role=StaffRole.CRM_MANAGER,
            assigned_by=self.actor,
        )
        self.capability = CapabilityDefinition.objects.get(code=CapabilityDefinition.PERSON_CONTACT_READ)
        StaffCapabilityAssignment.objects.create(
            user=self.target,
            capability=self.capability,
            state=StaffCapabilityAssignment.State.ACTIVE,
            granted_by=self.actor,
        )
        StaffCapabilityRestriction.objects.create(
            user=self.target,
            capability=CapabilityDefinition.objects.get(code=CapabilityDefinition.PERSON_EXPORT),
            restricted_by=self.actor,
        )

    def test_staff_deactivation_preserves_account_identity_and_suspends_grants(self):
        before_crm_version = self.target.crm_session_version
        deactivated = deactivate_staff_access(
            target_user=self.target,
            actor_user=self.actor,
            reason="Remove operational CRM access during role review.",
        )
        deactivated.refresh_from_db()
        self.assertTrue(deactivated.is_active)
        self.assertEqual(deactivated.account_lifecycle_state, User.AccountLifecycleState.ACTIVE)
        self.assertEqual(deactivated.crm_session_version, before_crm_version + 1)
        self.assertFalse(StaffRoleAssignment.objects.active().filter(user=deactivated).exists())
        grant = StaffCapabilityAssignment.objects.get(user=deactivated, capability=self.capability)
        self.assertEqual(grant.state, StaffCapabilityAssignment.State.SUSPENDED)
        self.assertTrue(StaffCapabilityRestriction.objects.filter(user=deactivated, is_active=True).exists())

    def test_staff_management_cannot_restore_suspended_grants(self):
        deactivate_staff_access(
            target_user=self.target,
            actor_user=self.actor,
            reason="Protect privacy grants while staff access is removed.",
        )
        self.assertEqual(
            StaffCapabilityAssignment.objects.get(user=self.target, capability=self.capability).state,
            StaffCapabilityAssignment.State.SUSPENDED,
        )

    def test_global_disablement_retains_roles_but_invalidates_credentials(self):
        mobile_refresh = issue_mobile_token_pair(user=self.target)
        before_auth_version = self.target.auth_version
        disable_user_account(
            target_user=self.target,
            actor_user=self.actor,
            reason="Disable the global account pending security review.",
        )
        self.target.refresh_from_db()
        self.assertFalse(self.target.is_active)
        self.assertEqual(self.target.account_lifecycle_state, User.AccountLifecycleState.DISABLED)
        self.assertGreater(self.target.auth_version, before_auth_version)
        self.assertTrue(StaffRoleAssignment.objects.filter(user=self.target, is_active=True).exists())
        self.assertTrue(StaffRoleAssignment.objects.active().filter(user=self.target).exists())
        self.assertTrue(
            self.target.mobile_auth_sessions.filter(revoked_at__isnull=False).exists()
        )
        self.assertNotIn(str(mobile_refresh), str(self.target.__dict__))

    def test_enablement_does_not_restore_roles_or_grants(self):
        disable_user_account(
            target_user=self.target,
            actor_user=self.actor,
            reason="Temporarily disable the account.",
        )
        StaffRoleAssignment.objects.filter(user=self.target).update(is_active=False)
        enable_user_account(
            target_user=self.target,
            actor_user=self.actor,
            reason="Re-enable global authentication after review.",
        )
        self.target.refresh_from_db()
        self.assertTrue(self.target.is_active)
        self.assertEqual(self.target.account_lifecycle_state, User.AccountLifecycleState.ACTIVE)
        self.assertFalse(StaffRoleAssignment.objects.active().filter(user=self.target).exists())
        self.assertEqual(
            StaffCapabilityAssignment.objects.get(user=self.target, capability=self.capability).state,
            StaffCapabilityAssignment.State.ACTIVE,
        )

    def test_final_administrator_cannot_be_deactivated_or_disabled(self):
        manager_assignment = StaffRoleAssignment.objects.get(user=self.target, role__code=StaffRole.CRM_MANAGER)
        manager_assignment._revoke_unchecked(revoked_by=self.actor)
        StaffRoleAssignment.objects.create(
            user=self.target,
            role=StaffRole.objects.get(code=StaffRole.CRM_ADMIN),
            role_family=StaffRole.CRM_ROLE_FAMILY,
            assigned_by=self.actor,
        )
        with self.assertRaises(FinalCrmAdminProtectionError):
            deactivate_staff_access(
                target_user=self.target,
                actor_user=self.actor,
                reason="Attempt final Administrator removal.",
            )
        with self.assertRaises(FinalCrmAdminProtectionError):
            disable_user_account(
                target_user=self.target,
                actor_user=self.actor,
                reason="Attempt final Administrator disablement.",
            )

    def test_lifecycle_audit_failure_rolls_back_deactivation(self):
        with patch("staff_access.services.record_audit_event", side_effect=RuntimeError("audit unavailable")):
            with self.assertRaises(RuntimeError):
                deactivate_staff_access(
                    target_user=self.target,
                    actor_user=self.actor,
                    reason="Audit rollback test.",
                )
        self.assertTrue(StaffRoleAssignment.objects.active().filter(user=self.target).exists())
        self.assertEqual(
            StaffCapabilityAssignment.objects.get(user=self.target, capability=self.capability).state,
            StaffCapabilityAssignment.State.ACTIVE,
        )
