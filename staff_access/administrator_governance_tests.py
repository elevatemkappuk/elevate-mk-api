from datetime import timedelta
from unittest.mock import patch

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.utils import timezone

from accounts.models import User
from audit.models import AuditEvent
from config.test_utils import test_actor
from people.models import Person
from notifications.models import TransactionalEmailJob
from staff_access.administrator_governance import (
    approve_administrator_proposal,
    create_administrator_proposal,
    resolve_administrator_proposal,
    consume_administrator_proposal,
)
from staff_access.models import (
    StaffAdministratorProposal,
    StaffInvitation,
    StaffRole,
    StaffRoleAssignment,
)


class AdministratorProposalTests(TestCase):
    def user(self, email, *, active=True, superuser=False):
        user = (
            User.objects.create_superuser(email=email, password="testpass123", person_first_name="Test", person_last_name="User")
            if superuser
            else User.objects.create_user(
                email=email,
                password="testpass123",
                person_first_name="Test",
                person_last_name="User",
                person_record_type=Person.RecordType.STAFF,
            )
        )
        if not active:
            user.is_active = False
            user.save(update_fields=["is_active"])
        return user

    def setUp(self):
        self.bootstrap = test_actor()
        self.proposer = self.user("proposal-admin@example.com")
        self.approver = self.user("approval-admin@example.com")
        self.target = self.user("promotion-target@example.com")
        self.onboard_target = self.user("onboard-target@example.com", active=False)
        StaffRoleAssignment.objects.assign_role(
            user=self.proposer, role=StaffRole.CRM_ADMIN, assigned_by=self.bootstrap
        )
        StaffRoleAssignment.objects.assign_role(
            user=self.approver, role=StaffRole.CRM_ADMIN, assigned_by=self.bootstrap
        )
        StaffRoleAssignment.objects.assign_role(
            user=self.target, role=StaffRole.CRM_MANAGER, assigned_by=self.bootstrap
        )

    def test_existing_promotion_requires_distinct_approver_and_effects_atomically(self):
        proposal = create_administrator_proposal(
            actor_user=self.proposer,
            operation=StaffAdministratorProposal.Operation.PROMOTE_EXISTING,
            target_user=self.target,
            reason="Scheduled administrator succession.",
        )
        with self.assertRaises(PermissionDenied):
            approve_administrator_proposal(proposal=proposal, actor_user=self.proposer)

        approve_administrator_proposal(proposal=proposal, actor_user=self.approver)
        proposal.refresh_from_db()
        self.assertEqual(proposal.status, StaffAdministratorProposal.Status.EFFECTED)
        self.assertTrue(StaffRoleAssignment.objects.active().filter(user=self.target, role__code=StaffRole.CRM_ADMIN).exists())
        self.assertFalse(StaffRoleAssignment.objects.active().filter(user=self.target, role__code=StaffRole.CRM_MANAGER).exists())
        self.target.refresh_from_db()
        self.assertEqual(self.target.crm_session_version, 2)
        self.assertTrue(
            AuditEvent.objects.filter(
                entity_type="StaffAdministratorProposal",
                entity_id=str(proposal.public_id),
                metadata__event_type="STAFF_ADMIN_PROMOTION_EFFECTED",
            ).exists()
        )
        with self.assertRaises(ValidationError):
            approve_administrator_proposal(proposal=proposal, actor_user=self.approver)

    def test_onboarding_approval_issues_invitation_without_effecting_authority(self):
        proposal = create_administrator_proposal(
            actor_user=self.proposer,
            operation=StaffAdministratorProposal.Operation.ONBOARD,
            target_user=self.onboard_target,
            reason="Approved onboarding appointment.",
        )
        approve_administrator_proposal(proposal=proposal, actor_user=self.approver)
        proposal.refresh_from_db()
        self.assertEqual(proposal.status, StaffAdministratorProposal.Status.APPROVED)
        self.assertFalse(StaffRoleAssignment.objects.filter(user=self.onboard_target).exists())
        invitation = StaffInvitation.objects.get(target_user=self.onboard_target)
        self.assertEqual(invitation.operation, StaffAdministratorProposal.Operation.ONBOARD)
        self.assertEqual(invitation.pending_role_codes, [StaffRole.CRM_ADMIN])
        self.assertLessEqual(invitation.expires_at, proposal.expires_at)
        self.assertEqual(proposal.resulting_invitation_id, invitation.pk)
        self.assertEqual(TransactionalEmailJob.objects.get(staff_invitation=invitation).status, TransactionalEmailJob.Status.PENDING)

    def test_consumption_requires_exact_approved_operation(self):
        proposal = create_administrator_proposal(
            actor_user=self.proposer,
            operation=StaffAdministratorProposal.Operation.ONBOARD,
            target_user=self.onboard_target,
            reason="Operation binding test.",
        )
        approve_administrator_proposal(proposal=proposal, actor_user=self.approver)
        assignment = StaffRoleAssignment.objects.create(
            user=self.onboard_target,
            role=StaffRole.objects.get(code=StaffRole.CRM_ADMIN),
            role_family=StaffRole.CRM_ROLE_FAMILY,
            assigned_by=self.approver,
        )
        with self.assertRaises(ValidationError):
            consume_administrator_proposal(
                proposal=proposal,
                actor_user=self.approver,
                resulting_assignment=assignment,
                expected_operation=StaffAdministratorProposal.Operation.REACTIVATE,
            )

    def test_reactivation_approval_issues_reactivate_bound_invitation(self):
        proposal = create_administrator_proposal(
            actor_user=self.proposer,
            operation=StaffAdministratorProposal.Operation.REACTIVATE,
            target_user=self.onboard_target,
            reason="Restore Administrator appointment after review.",
        )
        approve_administrator_proposal(proposal=proposal, actor_user=self.approver)
        proposal.refresh_from_db()
        invitation = StaffInvitation.objects.get(target_user=self.onboard_target)
        self.assertEqual(proposal.status, StaffAdministratorProposal.Status.APPROVED)
        self.assertEqual(invitation.operation, StaffAdministratorProposal.Operation.REACTIVATE)
        self.assertEqual(invitation.pending_role_codes, [StaffRole.CRM_ADMIN])
        self.assertEqual(proposal.resulting_invitation_id, invitation.pk)
        self.assertLessEqual(invitation.expires_at, proposal.expires_at)

    def test_expired_proposal_is_not_approvable(self):
        proposal = create_administrator_proposal(
            actor_user=self.proposer,
            operation=StaffAdministratorProposal.Operation.PROMOTE_EXISTING,
            target_user=self.target,
            reason="Expired appointment.",
        )
        with patch(
            "staff_access.administrator_governance.timezone.now",
            return_value=proposal.expires_at + timedelta(seconds=1),
        ):
            approved = approve_administrator_proposal(proposal=proposal, actor_user=self.approver)
        self.assertEqual(approved.status, StaffAdministratorProposal.Status.INVALIDATED)
        self.assertFalse(StaffRoleAssignment.objects.active().filter(user=self.target, role__code=StaffRole.CRM_ADMIN).exists())

    def test_stale_target_state_invalidates_proposal(self):
        proposal = create_administrator_proposal(
            actor_user=self.proposer,
            operation=StaffAdministratorProposal.Operation.PROMOTE_EXISTING,
            target_user=self.target,
            reason="Stale appointment.",
        )
        assignment = StaffRoleAssignment.objects.get(user=self.target, role__code=StaffRole.CRM_MANAGER)
        assignment.revoke(revoked_by=self.bootstrap)
        StaffRoleAssignment.objects.assign_role(user=self.target, role=StaffRole.CRM_VIEWER, assigned_by=self.bootstrap)
        approved = approve_administrator_proposal(proposal=proposal, actor_user=self.approver)
        self.assertEqual(approved.status, StaffAdministratorProposal.Status.INVALIDATED)

    def test_proposer_can_cancel_but_cannot_reject(self):
        proposal = create_administrator_proposal(
            actor_user=self.proposer,
            operation=StaffAdministratorProposal.Operation.ONBOARD,
            target_user=self.onboard_target,
            reason="Cancel test.",
        )
        with self.assertRaises(PermissionDenied):
            resolve_administrator_proposal(
                proposal=proposal,
                actor_user=self.proposer,
                outcome=StaffAdministratorProposal.Status.REJECTED,
                reason="Separation of duty.",
            )
        cancelled = resolve_administrator_proposal(
            proposal=proposal,
            actor_user=self.proposer,
            outcome=StaffAdministratorProposal.Status.CANCELLED,
            reason="No longer required.",
        )
        self.assertEqual(cancelled.status, StaffAdministratorProposal.Status.CANCELLED)

    def test_audit_failure_rolls_back_proposal_creation(self):
        with patch("staff_access.administrator_governance.record_audit_event", side_effect=RuntimeError("audit unavailable")):
            with self.assertRaises(RuntimeError):
                create_administrator_proposal(
                    actor_user=self.proposer,
                    operation=StaffAdministratorProposal.Operation.PROMOTE_EXISTING,
                    target_user=self.target,
                    reason="Must be audited.",
                )
        self.assertFalse(StaffAdministratorProposal.objects.exists())
