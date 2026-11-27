from datetime import timedelta
from threading import Barrier, Thread
from unittest import skipUnless
from unittest.mock import patch

from django.contrib import admin
from django.core.exceptions import ValidationError
from django.db import IntegrityError, close_old_connections, connection, transaction
from django.test import TransactionTestCase
from django.utils import timezone

from accounts.models import User
from audit.models import AuditEvent
from people.models import Person
from staff_access.governance import (
    apply_capability_proposal,
    apply_individual_capability_change,
    apply_urgent_staff_capability_admin_action,
    apply_superuser_capability_change,
    approve_capability_proposal,
    create_capability_proposal,
    reconcile_staff_capability_assignments,
)
from staff_access.services import deactivate_staff_role, offboard_staff_user, revoke_staff_role_assignments
from staff_access.permissions import get_effective_capability_codes_for_user
from staff_access.models import (
    CapabilityDefinition,
    CapabilityPolicyState,
    CapabilityProposal,
    CapabilityProposalDecision,
    RoleCapabilityDefault,
    StaffCapabilityAssignment,
    StaffCapabilityRestriction,
    StaffRole,
    StaffRoleAssignment,
)


class CapabilityGovernanceTests(TransactionTestCase):
    reset_sequences = True

    def create_user(self, email):
        return User.objects.create_user(
            email=email,
            password="testpass123",
            person_first_name=email.split("@")[0],
            person_last_name="User",
            person_record_type=Person.RecordType.STAFF,
        )

    def setUp(self):
        self._ensure_governance_reference_state()
        self.bootstrap = User.objects.create_superuser(
            email="governance-bootstrap@example.com",
            password="testpass123",
            person_first_name="Governance",
            person_last_name="Bootstrap",
        )
        self.actor = self.create_user("governance-actor@example.com")
        self.approver = self.create_user("governance-approver@example.com")
        self.target = self.create_user("governance-target@example.com")
        StaffRoleAssignment.objects.assign_role(user=self.actor, role=StaffRole.CRM_ADMIN, assigned_by=self.bootstrap)
        StaffRoleAssignment.objects.assign_role(user=self.approver, role=StaffRole.CRM_ADMIN, assigned_by=self.bootstrap)
        StaffRoleAssignment.objects.assign_role(user=self.target, role=StaffRole.CRM_MANAGER, assigned_by=self.bootstrap)
        self.contact = CapabilityDefinition.objects.get(code=CapabilityDefinition.PERSON_CONTACT_READ)
        self.demographic = CapabilityDefinition.objects.get(code=CapabilityDefinition.PERSON_DEMOGRAPHIC_READ)
        self.admin_capability = CapabilityDefinition.objects.get(code=CapabilityDefinition.STAFF_CAPABILITY_ADMIN)
        self.policy = CapabilityPolicyState.objects.get(singleton_key=1)

    def _ensure_governance_reference_state(self):
        for code, name in StaffRole.CANONICAL_ROLES:
            StaffRole.objects.update_or_create(
                code=code,
                defaults={"name": name, "is_active": True},
            )
        capabilities = {}
        for code, name, description in CapabilityDefinition.CANONICAL_CAPABILITIES:
            capability, _created = CapabilityDefinition.objects.update_or_create(
                code=code,
                defaults={"name": name, "description": description, "is_active": True},
            )
            capabilities[code] = capability
        role_defaults = {
            StaffRole.CRM_ADMIN: {
                CapabilityDefinition.PERSON_CONTACT_READ,
                CapabilityDefinition.PERSON_DEMOGRAPHIC_READ,
                CapabilityDefinition.PERSON_EXPORT,
                CapabilityDefinition.STAFF_CAPABILITY_ADMIN,
            },
            StaffRole.CRM_MANAGER: {CapabilityDefinition.PERSON_CONTACT_READ},
        }
        for role_code, capability_codes in role_defaults.items():
            role = StaffRole.objects.get(code=role_code)
            for capability_code in capability_codes:
                RoleCapabilityDefault.objects.update_or_create(
                    role=role,
                    capability=capabilities[capability_code],
                    defaults={"is_enabled": True},
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
        if connection.vendor == "postgresql":
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

    def test_governance_models_have_superuser_only_django_admin_boundary(self):
        self.assertIn(RoleCapabilityDefault, admin.site._registry)
        self.assertIn(StaffCapabilityAssignment, admin.site._registry)
        self.assertIn(StaffCapabilityRestriction, admin.site._registry)
        self.assertNotIn(CapabilityProposal, admin.site._registry)
        self.assertNotIn(CapabilityProposalDecision, admin.site._registry)

    def test_ordinary_individual_change_is_one_transaction_with_before_and_after_audit(self):
        proposal = apply_individual_capability_change(
            actor_user=self.actor,
            target_user=self.target,
            capability=self.demographic,
            operation=CapabilityProposal.Operation.INDIVIDUAL_GRANT,
            reason="Approved demographic work assignment",
        )

        self.assertEqual(proposal.status, CapabilityProposal.Status.APPROVED)
        self.assertTrue(
            StaffCapabilityAssignment.objects.filter(
                user=self.target,
                capability=self.demographic,
                state=StaffCapabilityAssignment.State.ACTIVE,
            ).exists()
        )
        events = AuditEvent.objects.filter(entity_type="StaffCapabilityAssignment")
        self.assertEqual(events.count(), 2)
        self.assertEqual({event.metadata["mutation_phase"] for event in events}, {"BEFORE", "AFTER"})
        self.assertEqual(events.first().metadata["executor_user_id"], str(self.actor.pk))
        self.assertEqual(events.first().metadata["proposer_user_id"], str(self.actor.pk))

    def test_role_default_and_admin_grant_require_distinct_second_approver(self):
        role_default = create_capability_proposal(
            actor_user=self.actor,
            role=StaffRole.objects.get(code=StaffRole.CRM_VIEWER),
            capability=self.demographic,
            operation=CapabilityProposal.Operation.ROLE_DEFAULT_ENABLE,
            reason="Approved viewer policy change",
        )
        with self.assertRaises(PermissionError):
            apply_capability_proposal(proposal=role_default, actor_user=self.actor)
        approved_default = approve_capability_proposal(proposal=role_default, actor_user=self.approver)
        self.assertEqual(approved_default.status, CapabilityProposal.Status.APPROVED)

        admin_grant = create_capability_proposal(
            actor_user=self.actor,
            target_user=self.target,
            capability=self.admin_capability,
            operation=CapabilityProposal.Operation.INDIVIDUAL_GRANT,
            reason="Approved privileged administration assignment",
        )
        with self.assertRaises(PermissionError):
            approve_capability_proposal(proposal=admin_grant, actor_user=self.actor)
        approved_grant = approve_capability_proposal(proposal=admin_grant, actor_user=self.approver)
        self.assertEqual(approved_grant.status, CapabilityProposal.Status.APPROVED)

    def test_urgent_admin_suspend_restrict_and_revoke_are_single_admin_actions(self):
        StaffCapabilityAssignment.objects.create(
            user=self.target,
            capability=self.admin_capability,
            state=StaffCapabilityAssignment.State.ACTIVE,
            granted_by=self.actor,
        )
        suspended = apply_urgent_staff_capability_admin_action(
            actor_user=self.actor,
            target_user=self.target,
            operation=CapabilityProposal.Operation.INDIVIDUAL_SUSPEND,
            reason="Urgent privileged suspension",
        )
        self.assertEqual(suspended.status, CapabilityProposal.Status.APPROVED)
        restore_proposal = create_capability_proposal(
            actor_user=self.actor,
            target_user=self.target,
            capability=self.admin_capability,
            operation=CapabilityProposal.Operation.INDIVIDUAL_RESTORE,
            reason="Restore after incident review",
        )
        restored = approve_capability_proposal(proposal=restore_proposal, actor_user=self.approver)
        self.assertEqual(restored.status, CapabilityProposal.Status.APPROVED)
        restricted = apply_urgent_staff_capability_admin_action(
            actor_user=self.actor,
            target_user=self.target,
            operation=CapabilityProposal.Operation.INDIVIDUAL_RESTRICT,
            reason="Urgent privileged restriction",
        )
        self.assertEqual(restricted.status, CapabilityProposal.Status.APPROVED)
        revoked = apply_urgent_staff_capability_admin_action(
            actor_user=self.actor,
            target_user=self.target,
            operation=CapabilityProposal.Operation.INDIVIDUAL_REVOKE,
            reason="Urgent privileged revocation",
        )
        self.assertEqual(revoked.status, CapabilityProposal.Status.APPROVED)

    def test_expiry_boundary_resolves_pending_proposal_without_mutation(self):
        proposal = create_capability_proposal(
            actor_user=self.actor,
            target_user=self.target,
            capability=self.demographic,
            operation=CapabilityProposal.Operation.INDIVIDUAL_GRANT,
            reason="Boundary expiry check",
        )
        with patch(
            "staff_access.governance.timezone.now",
            return_value=proposal.expires_at + timedelta(seconds=1),
        ):
            resolved = apply_capability_proposal(proposal=proposal, actor_user=self.actor)

        self.assertEqual(resolved.status, CapabilityProposal.Status.EXPIRED)
        self.assertFalse(StaffCapabilityAssignment.objects.filter(user=self.target, capability=self.demographic).exists())
        self.assertEqual(resolved.decision.outcome, CapabilityProposalDecision.Outcome.EXPIRED)
        self.assertEqual(
            resolved.decision.policy_version,
            CapabilityPolicyState.objects.get(singleton_key=1).version,
        )

    def test_create_replaces_same_key_proposal_at_exact_expiry_boundary(self):
        first = create_capability_proposal(
            actor_user=self.actor,
            target_user=self.target,
            capability=self.demographic,
            operation=CapabilityProposal.Operation.INDIVIDUAL_GRANT,
            reason="Initial expiring request",
        )

        with patch("staff_access.governance.timezone.now", return_value=first.expires_at):
            replacement = create_capability_proposal(
                actor_user=self.actor,
                target_user=self.target,
                capability=self.demographic,
                operation=CapabilityProposal.Operation.INDIVIDUAL_RESTRICT,
                reason="Replacement at exact expiry boundary",
            )

        first.refresh_from_db()
        self.assertEqual(first.status, CapabilityProposal.Status.EXPIRED)
        self.assertEqual(first.decision.outcome, CapabilityProposalDecision.Outcome.EXPIRED)
        self.assertEqual(replacement.status, CapabilityProposal.Status.PENDING)

    def test_pending_uniqueness_is_database_enforced_in_atomic_isolation(self):
        create_capability_proposal(
            actor_user=self.actor,
            target_user=self.target,
            capability=self.demographic,
            operation=CapabilityProposal.Operation.INDIVIDUAL_GRANT,
            reason="First pending request",
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                create_capability_proposal(
                    actor_user=self.actor,
                    target_user=self.target,
                    capability=self.demographic,
                    operation=CapabilityProposal.Operation.INDIVIDUAL_RESTRICT,
                    reason="Competing pending request",
                )

        viewer = StaffRole.objects.get(code=StaffRole.CRM_VIEWER)
        create_capability_proposal(
            actor_user=self.actor,
            role=viewer,
            capability=self.demographic,
            operation=CapabilityProposal.Operation.ROLE_DEFAULT_ENABLE,
            reason="First role policy request",
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                create_capability_proposal(
                    actor_user=self.actor,
                    role=viewer,
                    capability=self.demographic,
                    operation=CapabilityProposal.Operation.ROLE_DEFAULT_DISABLE,
                    reason="Competing role policy request",
                )

    def test_audit_failure_rolls_back_direct_ordinary_change(self):
        with patch("staff_access.governance.record_audit_event", side_effect=RuntimeError("audit unavailable")):
            with self.assertRaises(RuntimeError):
                apply_individual_capability_change(
                    actor_user=self.actor,
                    target_user=self.target,
                    capability=self.demographic,
                    operation=CapabilityProposal.Operation.INDIVIDUAL_GRANT,
                    reason="Audit rollback check",
                )
        self.assertFalse(CapabilityProposal.objects.exists())
        self.assertFalse(StaffCapabilityAssignment.objects.filter(user=self.target, capability=self.demographic).exists())

    def test_audit_failure_rolls_back_approval_and_leaves_proposal_pending(self):
        proposal = create_capability_proposal(
            actor_user=self.actor,
            target_user=self.target,
            capability=self.demographic,
            operation=CapabilityProposal.Operation.INDIVIDUAL_GRANT,
            reason="Approval audit rollback check",
        )
        with patch("staff_access.governance.record_audit_event", side_effect=RuntimeError("audit unavailable")):
            with self.assertRaises(RuntimeError):
                apply_capability_proposal(proposal=proposal, actor_user=self.actor)
        proposal.refresh_from_db()
        self.assertEqual(proposal.status, CapabilityProposal.Status.PENDING)
        self.assertFalse(hasattr(proposal, "decision"))
        self.assertFalse(StaffCapabilityAssignment.objects.filter(user=self.target, capability=self.demographic).exists())

    def test_two_person_approval_audit_failure_rolls_back_and_keeps_proposal_pending(self):
        proposal = create_capability_proposal(
            actor_user=self.actor,
            role=StaffRole.objects.get(code=StaffRole.CRM_VIEWER),
            capability=self.demographic,
            operation=CapabilityProposal.Operation.ROLE_DEFAULT_ENABLE,
            reason="Two-person audit rollback check",
        )
        with patch("staff_access.governance.record_audit_event", side_effect=RuntimeError("audit unavailable")):
            with self.assertRaises(RuntimeError):
                approve_capability_proposal(proposal=proposal, actor_user=self.approver)

        proposal.refresh_from_db()
        self.assertEqual(proposal.status, CapabilityProposal.Status.PENDING)
        self.assertFalse(CapabilityProposalDecision.objects.filter(proposal=proposal).exists())
        self.assertFalse(
            RoleCapabilityDefault.objects.filter(
                role__code=StaffRole.CRM_VIEWER,
                capability=self.demographic,
                is_enabled=True,
            ).exists()
        )

    def test_terminal_proposal_cannot_be_replayed_or_receive_second_decision(self):
        proposal = apply_individual_capability_change(
            actor_user=self.actor,
            target_user=self.target,
            capability=self.demographic,
            operation=CapabilityProposal.Operation.INDIVIDUAL_GRANT,
            reason="Terminal replay check",
        )
        with self.assertRaises(ValidationError):
            apply_capability_proposal(proposal=proposal, actor_user=self.actor)

        self.assertEqual(CapabilityProposalDecision.objects.filter(proposal=proposal).count(), 1)

    def test_superuser_direct_path_bypasses_two_person_and_staff_eligibility(self):
        superuser = User.objects.create_superuser(
            email="governance-superuser@example.com",
            password="testpass123",
            person_first_name="Recovery",
            person_last_name="User",
        )
        apply_superuser_capability_change(
            actor_user=superuser,
            target_user=superuser,
            capability=self.admin_capability,
            operation=CapabilityProposal.Operation.INDIVIDUAL_GRANT,
            reason="Superuser self-administration",
        )
        apply_superuser_capability_change(
            actor_user=superuser,
            role=StaffRole.objects.get(code=StaffRole.CRM_VIEWER),
            capability=self.demographic,
            operation=CapabilityProposal.Operation.ROLE_DEFAULT_ENABLE,
            reason="Superuser direct role-default administration",
        )
        self.assertTrue(
            StaffCapabilityAssignment.objects.filter(
                user=superuser,
                capability=self.admin_capability,
                state=StaffCapabilityAssignment.State.ACTIVE,
            ).exists()
        )
        self.assertTrue(
            RoleCapabilityDefault.objects.filter(
                role__code=StaffRole.CRM_VIEWER,
                capability=self.demographic,
                is_enabled=True,
            ).exists()
        )
        role_events = AuditEvent.objects.filter(
            actor_user=superuser,
            action=AuditEvent.Action.CAPABILITY_PERMISSION_MUTATED,
            entity_type="RoleCapabilityDefault",
        )
        self.assertEqual(role_events.count(), 2)
        self.assertEqual(
            set(role_events.values_list("metadata__mutation_phase", flat=True)),
            {"BEFORE", "AFTER"},
        )
        self.assertTrue(all(event.metadata["superuser_authority"] for event in role_events))

    def test_superuser_bypasses_explicit_restriction_but_audit_is_recorded(self):
        superuser = User.objects.create_superuser(
            email="governance-superuser-restriction@example.com",
            password="testpass123",
            person_first_name="Restricted",
            person_last_name="Superuser",
        )
        apply_superuser_capability_change(
            actor_user=superuser,
            target_user=superuser,
            capability=self.demographic,
            operation=CapabilityProposal.Operation.INDIVIDUAL_RESTRICT,
            reason="Superuser restriction administration",
        )
        self.assertIn(
            self.demographic.code,
            get_effective_capability_codes_for_user(superuser),
        )
        self.assertTrue(
            AuditEvent.objects.filter(
                actor_user=superuser,
                action=AuditEvent.Action.CAPABILITY_PERMISSION_MUTATED,
            ).exists()
        )
        restriction_events = AuditEvent.objects.filter(
            actor_user=superuser,
            action=AuditEvent.Action.CAPABILITY_PERMISSION_MUTATED,
            entity_type="StaffCapabilityRestriction",
        )
        self.assertEqual(restriction_events.count(), 2)
        self.assertTrue(all(event.changes.get("before_state") for event in restriction_events))
        self.assertTrue(all(event.metadata["superuser_authority"] for event in restriction_events))

    def test_inactive_superuser_and_ordinary_admin_cannot_use_direct_path(self):
        superuser = User.objects.create_superuser(
            email="governance-inactive-superuser@example.com",
            password="testpass123",
            person_first_name="Inactive",
            person_last_name="Superuser",
        )
        superuser.is_active = False
        superuser.save(update_fields=["is_active"])
        with self.assertRaises(PermissionError):
            apply_superuser_capability_change(
                actor_user=superuser,
                target_user=self.target,
                capability=self.demographic,
                operation=CapabilityProposal.Operation.INDIVIDUAL_GRANT,
                reason="Inactive superuser attempt",
            )
        with self.assertRaises(PermissionError):
            apply_superuser_capability_change(
                actor_user=self.actor,
                target_user=self.target,
                capability=self.demographic,
                operation=CapabilityProposal.Operation.INDIVIDUAL_GRANT,
                reason="Ordinary administrator impersonation attempt",
            )

    def test_superuser_audit_failure_rolls_back_direct_mutation(self):
        superuser = User.objects.create_superuser(
            email="governance-superuser-rollback@example.com",
            password="testpass123",
            person_first_name="Rollback",
            person_last_name="Superuser",
        )
        with patch("staff_access.governance.record_audit_event", side_effect=RuntimeError("audit unavailable")):
            with self.assertRaises(RuntimeError):
                apply_superuser_capability_change(
                    actor_user=superuser,
                    target_user=self.target,
                    capability=self.demographic,
                    operation=CapabilityProposal.Operation.INDIVIDUAL_GRANT,
                    reason="Superuser audit rollback",
                )
        self.assertFalse(
            StaffCapabilityAssignment.objects.filter(
                user=self.target,
                capability=self.demographic,
            ).exists()
        )

    def test_superuser_cannot_rewrite_or_approve_existing_ordinary_proposal(self):
        superuser = User.objects.create_superuser(
            email="governance-superuser-proposal@example.com",
            password="testpass123",
            person_first_name="Proposal",
            person_last_name="Superuser",
        )

        proposal = create_capability_proposal(
            actor_user=self.actor,
            target_user=self.target,
            capability=self.demographic,
            operation=CapabilityProposal.Operation.INDIVIDUAL_GRANT,
            reason="Superuser ordinary governance attempt",
        )
        with self.assertRaises(PermissionError):
            apply_capability_proposal(proposal=proposal, actor_user=superuser)
        proposal.refresh_from_db()
        self.assertEqual(proposal.status, CapabilityProposal.Status.PENDING)

    def test_next_governance_request_observes_incremented_policy_version(self):
        initial_version = CapabilityPolicyState.objects.get(singleton_key=1).version
        first = apply_individual_capability_change(
            actor_user=self.actor,
            target_user=self.target,
            capability=self.demographic,
            operation=CapabilityProposal.Operation.INDIVIDUAL_GRANT,
            reason="Policy version increment",
        )
        self.assertEqual(first.status, CapabilityProposal.Status.APPROVED)
        current_version = CapabilityPolicyState.objects.get(singleton_key=1).version
        self.assertEqual(current_version, initial_version + 1)

        second = create_capability_proposal(
            actor_user=self.actor,
            target_user=self.target,
            capability=self.contact,
            operation=CapabilityProposal.Operation.INDIVIDUAL_RESTRICT,
            reason="Next request sees current policy",
        )
        self.assertEqual(second.policy_version, current_version)

    @skipUnless(connection.vendor == "postgresql", "Requires PostgreSQL row-level locking semantics.")
    def test_concurrent_approval_of_one_proposal_has_one_winner(self):
        proposal = create_capability_proposal(
            actor_user=self.actor,
            role=StaffRole.objects.get(code=StaffRole.CRM_VIEWER),
            capability=self.demographic,
            operation=CapabilityProposal.Operation.ROLE_DEFAULT_ENABLE,
            reason="Concurrent approval check",
        )
        barrier = Barrier(2)
        outcomes = []

        def approve_once():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                approve_capability_proposal(proposal=proposal, actor_user=self.approver)
                outcomes.append("approved")
            except ValidationError:
                outcomes.append("terminal")
            finally:
                close_old_connections()

        threads = [Thread(target=approve_once) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)

        self.assertEqual(sorted(outcomes), ["approved", "terminal"])
        self.assertEqual(CapabilityProposalDecision.objects.filter(proposal=proposal).count(), 1)

    @skipUnless(connection.vendor == "postgresql", "Requires PostgreSQL row-level locking semantics.")
    def test_governance_approval_and_role_revocation_complete_without_deadlock(self):
        proposal = create_capability_proposal(
            actor_user=self.actor,
            target_user=self.target,
            capability=self.demographic,
            operation=CapabilityProposal.Operation.INDIVIDUAL_GRANT,
            reason="Cross-path role revocation lock-order check",
        )
        target_role_assignment = StaffRoleAssignment.objects.get(user=self.target)
        barrier = Barrier(2)
        outcomes = []

        def approve():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                result = apply_capability_proposal(proposal=proposal.pk, actor_user=self.actor)
                outcomes.append(("approval", result.status))
            except (ValidationError, PermissionError) as exc:
                outcomes.append(("approval", type(exc).__name__))
            finally:
                close_old_connections()

        def revoke_role():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                revoke_staff_role_assignments(
                    assignments=[target_role_assignment],
                    revoked_by=self.actor,
                )
                outcomes.append(("revocation", "completed"))
            except (ValidationError, PermissionError) as exc:
                outcomes.append(("revocation", type(exc).__name__))
            finally:
                close_old_connections()

        threads = [Thread(target=approve), Thread(target=revoke_role)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)

        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual({name for name, _outcome in outcomes}, {"approval", "revocation"})
        proposal.refresh_from_db()
        self.assertIn(
            proposal.status,
            {
                CapabilityProposal.Status.APPROVED,
                CapabilityProposal.Status.INVALIDATED,
            },
        )

    @skipUnless(connection.vendor == "postgresql", "Requires PostgreSQL row-level locking semantics.")
    def test_governance_approval_and_offboarding_complete_without_deadlock(self):
        proposal = create_capability_proposal(
            actor_user=self.actor,
            target_user=self.target,
            capability=self.demographic,
            operation=CapabilityProposal.Operation.INDIVIDUAL_GRANT,
            reason="Cross-path offboarding lock-order check",
        )
        barrier = Barrier(2)
        outcomes = []

        def approve():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                result = apply_capability_proposal(proposal=proposal.pk, actor_user=self.actor)
                outcomes.append(("approval", result.status))
            except (ValidationError, PermissionError) as exc:
                outcomes.append(("approval", type(exc).__name__))
            finally:
                close_old_connections()

        def offboard():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                offboard_staff_user(
                    target_user=self.target,
                    actor_user=self.actor,
                    reason="Remove operational staff access.",
                )
                outcomes.append(("offboarding", "completed"))
            except (ValidationError, PermissionError) as exc:
                outcomes.append(("offboarding", type(exc).__name__))
            finally:
                close_old_connections()

        threads = [Thread(target=approve), Thread(target=offboard)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)

        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual({name for name, _outcome in outcomes}, {"approval", "offboarding"})
        proposal.refresh_from_db()
        self.assertIn(
            proposal.status,
            {
                CapabilityProposal.Status.APPROVED,
                CapabilityProposal.Status.INVALIDATED,
            },
        )

    def test_stale_policy_and_restricted_target_invalidate_pending_proposal(self):
        proposal = create_capability_proposal(
            actor_user=self.actor,
            target_user=self.target,
            capability=self.demographic,
            operation=CapabilityProposal.Operation.INDIVIDUAL_GRANT,
            reason="Stale restriction check",
        )
        StaffCapabilityRestriction.objects.create(
            user=self.target,
            capability=self.demographic,
            restricted_by=self.actor,
        )

        resolved = apply_capability_proposal(proposal=proposal, actor_user=self.actor)

        self.assertEqual(resolved.status, CapabilityProposal.Status.INVALIDATED)
        self.assertFalse(StaffCapabilityAssignment.objects.filter(user=self.target, capability=self.demographic).exists())

    def test_deactivated_role_invalidates_pending_role_default_proposal(self):
        proposal = create_capability_proposal(
            actor_user=self.actor,
            role=StaffRole.objects.get(code=StaffRole.CRM_MANAGER),
            capability=self.demographic,
            operation=CapabilityProposal.Operation.ROLE_DEFAULT_ENABLE,
            reason="Role lifecycle stale-state check",
        )
        deactivate_staff_role(StaffRole.objects.get(code=StaffRole.CRM_MANAGER), actor_user=self.actor)

        resolved = approve_capability_proposal(proposal=proposal, actor_user=self.approver)

        self.assertEqual(resolved.status, CapabilityProposal.Status.INVALIDATED)
        self.assertEqual(resolved.resolution_reason, "The proposal role is no longer active.")

    def test_lost_proposer_authority_invalidates_when_another_admin_resolves(self):
        proposal = create_capability_proposal(
            actor_user=self.actor,
            target_user=self.target,
            capability=self.demographic,
            operation=CapabilityProposal.Operation.INDIVIDUAL_GRANT,
            reason="Authority revalidation check",
        )
        StaffRoleAssignment.objects.filter(user=self.actor, role__code=StaffRole.CRM_ADMIN).update(
            is_active=False,
            revoked_at=timezone.now(),
        )

        resolved = apply_capability_proposal(proposal=proposal, actor_user=self.approver)

        self.assertEqual(resolved.status, CapabilityProposal.Status.INVALIDATED)
        self.assertEqual(resolved.decision.outcome, CapabilityProposalDecision.Outcome.INVALIDATED)

    def test_ineligible_target_invalidates_pending_proposal(self):
        proposal = create_capability_proposal(
            actor_user=self.actor,
            target_user=self.target,
            capability=self.demographic,
            operation=CapabilityProposal.Operation.INDIVIDUAL_GRANT,
            reason="Target eligibility check",
        )
        StaffRoleAssignment.objects.filter(user=self.target).update(is_active=False, revoked_at=timezone.now())

        resolved = apply_capability_proposal(proposal=proposal, actor_user=self.approver)

        self.assertEqual(resolved.status, CapabilityProposal.Status.INVALIDATED)
        self.assertEqual(resolved.resolution_reason, "The proposal target is no longer eligible.")

    @skipUnless(connection.vendor == "postgresql", "Requires PostgreSQL row-level locking semantics.")
    def test_pending_uniqueness_serializes_concurrent_postgres_proposals(self):
        barrier = Barrier(2)
        outcomes = []

        def create_pending():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                with transaction.atomic():
                    create_capability_proposal(
                        actor_user=self.actor,
                        target_user=self.target,
                        capability=self.demographic,
                        operation=CapabilityProposal.Operation.INDIVIDUAL_GRANT,
                        reason="Concurrent pending request",
                    )
                outcomes.append("created")
            except IntegrityError:
                outcomes.append("integrity-error")
            finally:
                close_old_connections()

        threads = [Thread(target=create_pending) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)

        self.assertEqual(sorted(outcomes), ["created", "integrity-error"])

    def test_role_reconciliation_suspends_grants_and_preserves_restriction_history(self):
        assignment = StaffCapabilityAssignment.objects.create(
            user=self.target,
            capability=self.demographic,
            state=StaffCapabilityAssignment.State.ACTIVE,
            granted_by=self.actor,
        )
        restriction = StaffCapabilityRestriction.objects.create(
            user=self.target,
            capability=self.demographic,
            restricted_by=self.actor,
        )
        StaffRoleAssignment.objects.filter(user=self.target).update(is_active=False, revoked_at=timezone.now())

        with self.assertRaises(PermissionError):
            reconcile_staff_capability_assignments(user=self.target, actor_user=None)

        reconciled = reconcile_staff_capability_assignments(user=self.target, actor_user=self.actor)

        self.assertEqual([item.pk for item in reconciled], [assignment.pk])
        assignment.refresh_from_db()
        self.assertEqual(assignment.state, StaffCapabilityAssignment.State.SUSPENDED)
        self.assertTrue(StaffCapabilityRestriction.objects.filter(pk=restriction.pk, is_active=True).exists())

    def test_decision_history_is_immutable_and_unknown_operation_is_rejected(self):
        proposal = create_capability_proposal(
            actor_user=self.actor,
            target_user=self.target,
            capability=self.demographic,
            operation=CapabilityProposal.Operation.INDIVIDUAL_GRANT,
            reason="Decision history check",
        )
        proposal = apply_capability_proposal(proposal=proposal, actor_user=self.actor)
        decision = proposal.decision
        with self.assertRaises(ValidationError):
            decision.save()
        with self.assertRaises(ValidationError):
            decision.delete()
        with self.assertRaises(ValidationError):
            CapabilityProposalDecision.objects.filter(pk=decision.pk).update(reason="Queryset mutation")
        proposal.reason = "Submission mutation"
        with self.assertRaises(ValidationError):
            proposal.save(update_fields=["reason"])
        with self.assertRaises(ValidationError):
            CapabilityProposal.objects.filter(pk=proposal.pk).update(reason="Queryset mutation")

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                CapabilityProposal.objects.create(
                    operation="UNKNOWN_OPERATION",
                    status=CapabilityProposal.Status.PENDING,
                    target_user=self.target,
                    capability=self.demographic,
                    proposed_by=self.actor,
                    reason="Unknown operation",
                    policy_version=self.policy.version,
                    expires_at=timezone.now() + timedelta(days=30),
                    expected_target_state={},
                )
