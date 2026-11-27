from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import User
from audit.models import AuditEvent
from people.models import Person
from staff_access.models import (
    CapabilityDefinition,
    CapabilityPolicyState,
    CapabilityProposal,
    RoleCapabilityDefault,
    StaffCapabilityAssignment,
    StaffCapabilityRestriction,
    StaffRole,
    StaffRoleAssignment,
)


@override_settings(ROOT_URLCONF="config.urls")
class GovernanceApiTests(TestCase):
    password = "testpass123"

    def setUp(self):
        self.ensure_reference_state()
        self.admin = self.create_staff("governance-api-admin@example.com")
        self.approver = self.create_staff("governance-api-approver@example.com")
        self.target = self.create_staff("governance-api-target@example.com")
        self.bootstrap = self.create_staff("governance-api-bootstrap@example.com", superuser=True)
        StaffRoleAssignment.objects.assign_role(user=self.admin, role=StaffRole.CRM_ADMIN, assigned_by=self.bootstrap)
        StaffRoleAssignment.objects.assign_role(user=self.approver, role=StaffRole.CRM_ADMIN, assigned_by=self.bootstrap)
        StaffRoleAssignment.objects.assign_role(user=self.target, role=StaffRole.CRM_MANAGER, assigned_by=self.bootstrap)
        self.admin_client = self.login(self.admin)
        self.approver_client = self.login(self.approver)

    def ensure_reference_state(self):
        capabilities = {}
        for code, name, description in CapabilityDefinition.CANONICAL_CAPABILITIES:
            capability, _created = CapabilityDefinition.objects.update_or_create(
                code=code,
                defaults={"name": name, "description": description, "is_active": True},
            )
            capabilities[code] = capability
        defaults = {
            StaffRole.CRM_ADMIN: {
                CapabilityDefinition.PERSON_CONTACT_READ,
                CapabilityDefinition.PERSON_DEMOGRAPHIC_READ,
                CapabilityDefinition.PERSON_EXPORT,
                CapabilityDefinition.STAFF_CAPABILITY_ADMIN,
            },
            StaffRole.CRM_MANAGER: {CapabilityDefinition.PERSON_CONTACT_READ},
        }
        for role_code, capability_codes in defaults.items():
            role = StaffRole.objects.get(code=role_code)
            for code in capability_codes:
                RoleCapabilityDefault.objects.update_or_create(
                    role=role,
                    capability=capabilities[code],
                    defaults={"is_enabled": True},
                )
        CapabilityPolicyState.objects.get_or_create(
            singleton_key=1,
            defaults={
                "key": "capability-governance",
                "version": 1,
                "baseline_snapshot": {},
                "reason": "Governance API test baseline",
            },
        )

    def create_staff(self, email, *, superuser=False):
        return User.objects.create_user(
            email=email,
            password=self.password,
            person_first_name="Governance",
            person_last_name="API",
            person_record_type=Person.RecordType.STAFF,
            is_staff=superuser,
            is_superuser=superuser,
        )

    def login(self, user):
        client = APIClient(enforce_csrf_checks=False)
        response = client.post("/api/v1/auth/login/", {"email": user.email, "password": self.password}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertNotIn("is_superuser", response.data)
        return client

    def test_target_projection_is_safe_and_backend_authoritative(self):
        response = self.admin_client.get(f"/api/v1/staff/governance/targets/{self.target.pk}/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["target_user_id"], self.target.pk)
        self.assertNotIn("email", response.data)
        self.assertIn(CapabilityDefinition.PERSON_CONTACT_READ, response.data["capabilities"])
        self.assertIn("permitted_actions", response.data["capabilities"][CapabilityDefinition.PERSON_CONTACT_READ])
        self.assertIn("effective_explanation", response.data["capabilities"][CapabilityDefinition.PERSON_CONTACT_READ])

    def test_staff_directory_is_searchable_without_contact_disclosure(self):
        response = self.admin_client.get("/api/v1/staff/governance/targets/?q=Governance")

        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(response.data["count"], 1)
        self.assertTrue(response.data["results"])
        row = next(item for item in response.data["results"] if item["user_id"] == self.target.pk)
        self.assertEqual(row["display_name"], "Governance API")
        self.assertIn(StaffRole.CRM_MANAGER, row["role_codes"])
        self.assertNotIn("email", row)
        self.assertNotIn("phone", row)
        self.assertNotIn("demographics", row)

    def test_ordinary_admin_can_create_and_approve_role_default_proposal(self):
        role = StaffRole.objects.get(code=StaffRole.CRM_VIEWER)
        response = self.admin_client.post(
            "/api/v1/staff/governance/proposals/",
            {
                "operation": "ROLE_DEFAULT_ENABLE",
                "capability": CapabilityDefinition.PERSON_CONTACT_READ,
                "role_id": role.pk,
                "reason": "Approved governance API contract test",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        proposal_id = response.data["public_id"]
        approval = self.approver_client.post(
            f"/api/v1/staff/governance/proposals/{proposal_id}/approve/",
            {},
            format="json",
        )
        self.assertEqual(approval.status_code, 200)
        self.assertEqual(approval.data["status"], "APPROVED")

    def test_direct_individual_change_uses_single_admin_service_path(self):
        capability = CapabilityDefinition.objects.get(code=CapabilityDefinition.STAFF_CAPABILITY_ADMIN)
        StaffCapabilityAssignment.objects.create(user=self.target, capability=capability, granted_by=self.admin)

        response = self.admin_client.post(
            "/api/v1/staff/governance/individual-changes/",
            {
                "operation": "INDIVIDUAL_SUSPEND",
                "capability": capability.code,
                "target_user_id": self.target.pk,
                "reason": "Temporarily suspend access",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["status"], "APPROVED")
        self.assertEqual(
            StaffCapabilityAssignment.objects.get(user=self.target, capability=capability).state,
            StaffCapabilityAssignment.State.SUSPENDED,
        )

    def test_ordinary_individual_grant_is_applied_without_pending_proposal(self):
        capability = CapabilityDefinition.objects.get(code=CapabilityDefinition.PERSON_DEMOGRAPHIC_READ)

        response = self.admin_client.post(
            "/api/v1/staff/governance/individual-changes/",
            {
                "operation": "INDIVIDUAL_GRANT",
                "capability": capability.code,
                "target_user_id": self.target.pk,
                "reason": "Grant ordinary demographic access",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["status"], "APPROVED")
        self.assertTrue(
            StaffCapabilityAssignment.objects.filter(
                user=self.target,
                capability=capability,
                state=StaffCapabilityAssignment.State.ACTIVE,
            ).exists()
        )
        self.assertFalse(
            CapabilityProposal.objects.filter(
                target_user=self.target,
                capability=capability,
                status=CapabilityProposal.Status.PENDING,
            ).exists()
        )

        direct_operation = self.admin_client.post(
            "/api/v1/staff/governance/proposals/",
            {
                "operation": "INDIVIDUAL_SUSPEND",
                "capability": CapabilityDefinition.STAFF_CAPABILITY_ADMIN,
                "target_user_id": self.target.pk,
                "reason": "Suspension must use the direct governance path",
            },
            format="json",
        )
        self.assertEqual(direct_operation.status_code, 400)
        self.assertTrue(
            AuditEvent.objects.filter(
                action=AuditEvent.Action.CAPABILITY_PERMISSION_MUTATED,
                metadata__event_type="GOVERNANCE_AUTHORIZATION_DENIED",
                metadata__denial_code="INVALID_EXECUTION_MODE",
            ).exists()
        )

    def test_proposal_endpoint_rejects_operations_that_use_direct_path(self):
        response = self.admin_client.post(
            "/api/v1/staff/governance/proposals/",
            {
                "operation": "INDIVIDUAL_GRANT",
                "capability": CapabilityDefinition.PERSON_DEMOGRAPHIC_READ,
                "target_user_id": self.target.pk,
                "reason": "Direct operations must not become orphaned proposals",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("proposal workflow", str(response.data).lower())
        self.assertFalse(
            CapabilityProposal.objects.filter(
                target_user=self.target,
                capability__code=CapabilityDefinition.PERSON_DEMOGRAPHIC_READ,
                status=CapabilityProposal.Status.PENDING,
            ).exists()
        )

    def test_duplicate_pending_proposal_returns_sanitized_conflict(self):
        capability = CapabilityDefinition.objects.get(code=CapabilityDefinition.STAFF_CAPABILITY_ADMIN)
        payload = {
            "operation": "INDIVIDUAL_GRANT",
            "capability": capability.code,
            "target_user_id": self.target.pk,
            "reason": "Pending proposal conflict contract",
        }

        first = self.admin_client.post("/api/v1/staff/governance/proposals/", payload, format="json")
        second = self.admin_client.post("/api/v1/staff/governance/proposals/", payload, format="json")

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 409)
        self.assertEqual(second.data["code"], "PENDING_PROPOSAL_EXISTS")
        self.assertIn("proposal_id", second.data)
        self.assertNotIn("IntegrityError", str(second.data))

    def test_target_projection_ignores_revoked_history_for_current_actions(self):
        capability = CapabilityDefinition.objects.get(code=CapabilityDefinition.PERSON_EXPORT)
        StaffCapabilityAssignment.objects.create(
            user=self.target,
            capability=capability,
            state=StaffCapabilityAssignment.State.REVOKED,
            granted_by=self.admin,
        )

        response = self.admin_client.get(f"/api/v1/staff/governance/targets/{self.target.pk}/")

        self.assertEqual(response.status_code, 200)
        state = response.data["capabilities"][capability.code]
        self.assertEqual(state["assignment_state"], StaffCapabilityAssignment.State.REVOKED)
        self.assertFalse(state["effective"])
        self.assertIn("INDIVIDUAL_GRANT", state["permitted_actions"])

    def test_target_projection_with_active_restriction_has_only_relevant_state(self):
        capability = CapabilityDefinition.objects.get(code=CapabilityDefinition.PERSON_EXPORT)
        StaffCapabilityRestriction.objects.create(user=self.target, capability=capability, restricted_by=self.admin)

        response = self.admin_client.get(f"/api/v1/staff/governance/targets/{self.target.pk}/")

        self.assertEqual(response.status_code, 200)
        state = response.data["capabilities"][capability.code]
        self.assertTrue(state["restriction_active"])
        self.assertFalse(state["effective"])
        self.assertIn("EXPLICIT_RESTRICTION", state["effective_explanation"]["sources"])
        self.assertNotIn("INDIVIDUAL_GRANT", state["permitted_actions"])

    def test_target_projection_does_not_advertise_unsupported_suspend(self):
        capability = CapabilityDefinition.objects.get(code=CapabilityDefinition.PERSON_CONTACT_READ)
        StaffCapabilityAssignment.objects.create(user=self.target, capability=capability, granted_by=self.admin)

        response = self.admin_client.get(f"/api/v1/staff/governance/targets/{self.target.pk}/")

        self.assertEqual(response.status_code, 200)
        actions = response.data["capabilities"][capability.code]["permitted_actions"]
        self.assertNotIn("INDIVIDUAL_SUSPEND", actions)
        self.assertIn("INDIVIDUAL_REVOKE", actions)

    def test_superuser_endpoint_requires_superuser_and_is_separate(self):
        ordinary = self.admin_client.post(
            "/api/v1/staff/governance/superuser-actions/",
            {
                "operation": "ROLE_DEFAULT_ENABLE",
                "capability": CapabilityDefinition.PERSON_CONTACT_READ,
                "role_id": StaffRole.objects.get(code=StaffRole.CRM_VIEWER).pk,
                "reason": "Must not use superuser path",
            },
            format="json",
        )
        self.assertEqual(ordinary.status_code, 403)

        superuser = self.create_staff("governance-api-superuser@example.com", superuser=True)
        StaffRoleAssignment.objects.assign_role(user=superuser, role=StaffRole.CRM_ADMIN, assigned_by=superuser)
        response = self.login(superuser).post(
            "/api/v1/staff/governance/superuser-actions/",
            {
                "operation": "ROLE_DEFAULT_ENABLE",
                "capability": CapabilityDefinition.PERSON_CONTACT_READ,
                "role_id": StaffRole.objects.get(code=StaffRole.CRM_VIEWER).pk,
                "reason": "Audited superuser operation",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "APPLIED")

    def test_crm_admitted_superuser_can_read_governance_projections(self):
        superuser = self.create_staff("governance-api-read-superuser@example.com", superuser=True)
        StaffRoleAssignment.objects.assign_role(user=superuser, role=StaffRole.CRM_ADMIN, assigned_by=superuser)
        client = self.login(superuser)

        directory = client.get("/api/v1/staff/governance/targets/?q=Governance")
        self.assertEqual(directory.status_code, 200)
        self.assertTrue(any(row["user_id"] == self.target.pk for row in directory.data["results"]))
        self.assertTrue(all("email" not in row for row in directory.data["results"]))

        defaults = client.get("/api/v1/staff/governance/role-defaults/")
        self.assertEqual(defaults.status_code, 200)
        self.assertEqual(len(defaults.data["role_defaults"]), 21)
        self.assertTrue(all(row["permitted_actions"] for row in defaults.data["role_defaults"]))
        self.assertTrue(all(row["execution_mode"] == "DIRECT_SUPERUSER" for row in defaults.data["role_defaults"]))

        for history_type in ("individual", "role-defaults", "decisions"):
            response = client.get(f"/api/v1/staff/governance/history/{history_type}/")
            self.assertEqual(response.status_code, 200)

    def test_superuser_direct_operations_require_reason_and_apply_without_proposal(self):
        superuser = self.create_staff("governance-api-direct-superuser@example.com", superuser=True)
        StaffRoleAssignment.objects.assign_role(user=superuser, role=StaffRole.CRM_ADMIN, assigned_by=superuser)
        client = self.login(superuser)
        role = StaffRole.objects.get(code=StaffRole.CRM_VIEWER)

        missing_reason = client.post(
            "/api/v1/staff/governance/superuser-actions/",
            {
                "operation": "ROLE_DEFAULT_ENABLE",
                "capability": CapabilityDefinition.PERSON_CONTACT_READ,
                "role_id": role.pk,
                "reason": "",
            },
            format="json",
        )
        self.assertEqual(missing_reason.status_code, 400)

        unsafe_reason = client.post(
            "/api/v1/staff/governance/superuser-actions/",
            {
                "operation": "ROLE_DEFAULT_ENABLE",
                "capability": CapabilityDefinition.PERSON_CONTACT_READ,
                "role_id": role.pk,
                "reason": "password=should-not-be-recorded",
            },
            format="json",
        )
        self.assertEqual(unsafe_reason.status_code, 400)

        role_change = client.post(
            "/api/v1/staff/governance/superuser-actions/",
            {
                "operation": "ROLE_DEFAULT_ENABLE",
                "capability": CapabilityDefinition.PERSON_CONTACT_READ,
                "role_id": role.pk,
                "reason": "Direct audited superuser role-default correction",
            },
            format="json",
        )
        self.assertEqual(role_change.status_code, 200, role_change.data)
        self.assertEqual(role_change.data["status"], "APPLIED")
        self.assertFalse(
            CapabilityProposal.objects.filter(
                role=role,
                capability__code=CapabilityDefinition.PERSON_CONTACT_READ,
                status=CapabilityProposal.Status.PENDING,
            ).exists()
        )

        individual_change = client.post(
            "/api/v1/staff/governance/superuser-actions/",
            {
                "operation": "INDIVIDUAL_GRANT",
                "capability": CapabilityDefinition.PERSON_EXPORT,
                "target_user_id": self.target.pk,
                "reason": "Direct audited superuser individual grant",
            },
            format="json",
        )
        self.assertEqual(individual_change.status_code, 200, individual_change.data)
        self.assertEqual(individual_change.data["status"], "APPLIED")
        self.assertTrue(
            StaffCapabilityAssignment.objects.filter(
                user=self.target,
                capability__code=CapabilityDefinition.PERSON_EXPORT,
                state=StaffCapabilityAssignment.State.ACTIVE,
            ).exists()
        )
        history = client.get("/api/v1/staff/governance/history/individual/")
        self.assertEqual(history.status_code, 200)
        direct_entries = [entry for entry in history.data["results"] if entry.get("superuser_operation")]
        self.assertTrue(direct_entries)
        self.assertTrue(any(entry.get("resulting_state") == StaffCapabilityAssignment.State.ACTIVE for entry in direct_entries))

    def test_superuser_without_crm_role_cannot_read_governance(self):
        superuser = self.create_staff("governance-api-no-role-superuser@example.com", superuser=True)
        response = self.login(superuser).get("/api/v1/staff/governance/targets/")
        self.assertEqual(response.status_code, 403)
        self.assertTrue(
            AuditEvent.objects.filter(
                action=AuditEvent.Action.CAPABILITY_PERMISSION_MUTATED,
                actor_user=superuser,
                metadata__event_type="GOVERNANCE_AUTHORIZATION_DENIED",
            ).exists()
        )

    def test_non_staff_capability_holder_cannot_read_governance(self):
        business_user = User.objects.create_user(
            email="governance-api-business-admin@example.com",
            password=self.password,
            person_first_name="Business",
            person_last_name="Administrator",
            person_record_type=Person.RecordType.TECHNICAL,
        )
        StaffRoleAssignment.objects.create(user=business_user, role=StaffRole.objects.get(code=StaffRole.CRM_ADMIN))
        response = self.login(business_user).get("/api/v1/staff/governance/targets/")
        self.assertEqual(response.status_code, 403)
        self.assertTrue(
            AuditEvent.objects.filter(
                action=AuditEvent.Action.CAPABILITY_PERMISSION_MUTATED,
                actor_user=business_user,
                metadata__event_type="GOVERNANCE_AUTHORIZATION_DENIED",
                metadata__denial_code="STAFF_PERSON_REQUIRED",
            ).exists()
        )

    def test_histories_expose_proposals_and_decisions_without_personal_contact_data(self):
        response = self.admin_client.get("/api/v1/staff/governance/history/individual/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["results"], [])

        response = self.admin_client.get("/api/v1/staff/governance/history/decisions/")
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

    def test_role_defaults_include_current_policy_version(self):
        response = self.admin_client.get("/api/v1/staff/governance/role-defaults/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data["policy_version"],
            CapabilityPolicyState.objects.get(singleton_key=1, key="capability-governance").version,
        )
        viewer_contact = next(
            row for row in response.data["role_defaults"]
            if row["role_code"] == StaffRole.CRM_VIEWER
            and row["capability"] == CapabilityDefinition.PERSON_CONTACT_READ
        )
        self.assertEqual(viewer_contact["permitted_actions"], ["ROLE_DEFAULT_ENABLE"])

        role = StaffRole.objects.get(code=StaffRole.CRM_VIEWER)
        self.admin_client.post(
            "/api/v1/staff/governance/proposals/",
            {
                "operation": "ROLE_DEFAULT_ENABLE",
                "capability": CapabilityDefinition.PERSON_CONTACT_READ,
                "role_id": role.pk,
                "reason": "Pending role-default action projection",
            },
            format="json",
        )
        response = self.admin_client.get("/api/v1/staff/governance/role-defaults/")
        viewer_contact = next(
            row for row in response.data["role_defaults"]
            if row["role_code"] == StaffRole.CRM_VIEWER
            and row["capability"] == CapabilityDefinition.PERSON_CONTACT_READ
        )
        self.assertEqual(viewer_contact["permitted_actions"], [])
