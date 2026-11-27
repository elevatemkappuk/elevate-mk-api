from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from audit.models import AuditEvent, AuditEventSubject, LegalHold, LegalHoldReleaseRequest
from people.models import Person
from staff_access.models import CapabilityDefinition, StaffCapabilityAssignment, StaffRole, StaffRoleAssignment


@override_settings(ROOT_URLCONF="config.urls")
class PrivacyAuditAndLegalHoldApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_user(
            email="privacy-admin-api@example.com",
            password="testpass123",
            person=Person.objects.create(
                first_name="Privacy",
                last_name="Admin",
                record_type=Person.RecordType.STAFF,
            ),
        )
        self.other_admin = User.objects.create_user(
            email="privacy-admin-two@example.com",
            password="testpass123",
            person=Person.objects.create(
                first_name="Privacy",
                last_name="Approver",
                record_type=Person.RecordType.STAFF,
            ),
        )
        self.superuser = User.objects.create_user(
            email="privacy-superuser-api@example.com",
            password="testpass123",
            is_staff=True,
            is_superuser=True,
            person=Person.objects.create(
                first_name="Privacy", last_name="Superuser", record_type=Person.RecordType.STAFF,
            ),
        )
        role = StaffRole.objects.get(code=StaffRole.CRM_ADMIN)
        StaffRoleAssignment.objects.assign_role(user=self.admin, role=role, assigned_by=self.superuser)
        StaffRoleAssignment.objects.assign_role(user=self.other_admin, role=role, assigned_by=self.superuser)
        StaffRoleAssignment.objects.assign_role(user=self.superuser, role=role, assigned_by=self.superuser)
        StaffCapabilityAssignment.objects.create(
            user=self.admin,
            capability=CapabilityDefinition.objects.get(code=CapabilityDefinition.PRIVACY_AUDIT_READ),
            granted_by=self.admin,
        )
        legal_hold_capability = CapabilityDefinition.objects.get(code=CapabilityDefinition.LEGAL_HOLD_ADMIN)
        StaffCapabilityAssignment.objects.create(user=self.admin, capability=legal_hold_capability, granted_by=self.admin)
        StaffCapabilityAssignment.objects.create(
            user=self.other_admin,
            capability=legal_hold_capability,
            granted_by=self.admin,
        )
        self.person = Person.objects.create(first_name="Subject", last_name="Person")
        self.audit_history_url = f"/api/v1/people/{self.person.pk}/privacy-audit-history/"
        self.holds_url = "/api/v1/privacy/legal-holds/"

    def authenticate(self, user):
        self.client.force_authenticate(user=user)

    def test_privacy_history_is_person_scoped_and_safe(self):
        self.authenticate(self.admin)
        event = AuditEvent.objects.create(
            actor_user=self.admin,
            action=AuditEvent.Action.PERSON_UPDATED,
            entity_type="Person",
            entity_id=str(self.person.pk),
            changes={
                "location": {"from": "Old", "to": "New"},
                "email": {"from": "secret@example.com", "to": "new@example.com"},
            },
            metadata={"person_id": str(self.person.pk), "token": "must-not-project"},
            audit_domain=AuditEvent.Domain.PRIVACY,
        )
        AuditEventSubject.objects.create(event=event, person=self.person)
        AuditEvent.objects.create(
            actor_user=self.admin,
            action=AuditEvent.Action.PERSON_UPDATED,
            entity_type="Person",
            entity_id=str(self.person.pk),
            audit_domain=AuditEvent.Domain.OPERATIONAL,
        )

        response = self.client.get(self.audit_history_url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["id"], str(event.id))
        self.assertEqual(response.data["results"][0]["permitted_actions"], ["place_event_hold"])
        self.assertTrue(response.data["results"][0]["legal_hold_event_reference"])
        self.assertNotIn("metadata", response.data["results"][0])
        self.assertNotIn("entity_id", response.data["results"][0])

    def test_privacy_history_denial_is_audited_and_mobile_jwt_boundary_is_not_changed(self):
        self.authenticate(self.other_admin)

        response = self.client.get(self.audit_history_url)

        self.assertEqual(response.status_code, 403)
        denial = AuditEvent.objects.filter(
            action=AuditEvent.Action.CAPABILITY_PERMISSION_MUTATED,
            metadata__event_type="PRIVACY_AUTHORIZATION_DENIED",
            metadata__operation="PRIVACY_AUDIT_READ",
        ).latest("id")
        self.assertEqual(denial.metadata["outcome"], "DENIED")
        self.assertNotEqual(denial.entity_id, str(self.person.pk))

    def test_legal_hold_workflow_uses_services_and_action_descriptors(self):
        self.authenticate(self.admin)

        create_response = self.client.post(
            self.holds_url,
            {"person_id": self.person.pk, "reason": "Regulatory investigation"},
            format="json",
        )

        self.assertEqual(create_response.status_code, 201)
        self.assertEqual(create_response.data["scope"], LegalHold.Scope.PERSON)
        self.assertEqual(create_response.data["permitted_actions"], ["request_release"])
        self.assertEqual(create_response.data["execution_mode"], "GOVERNED")
        self.assertEqual(create_response.data["person_display_name"], "Subject Person")
        self.assertEqual(create_response.data["placed_by"]["display_name"], "Privacy Admin")
        hold_id = create_response.data["public_id"]

        request_response = self.client.post(
            f"/api/v1/privacy/legal-holds/{hold_id}/release-requests/",
            {"reason": "Review complete"},
            format="json",
        )

        self.assertEqual(request_response.status_code, 201)
        release_request_id = request_response.data["public_id"]

        self.authenticate(self.other_admin)
        approve_response = self.client.post(
            f"/api/v1/privacy/legal-hold-release-requests/{release_request_id}/approve/",
            {"reason": "Independent review complete"},
            format="json",
        )

        self.assertEqual(approve_response.status_code, 200)
        self.assertEqual(approve_response.data["status"], LegalHold.Status.RELEASED)
        self.assertFalse(LegalHold.objects.filter(public_id=hold_id, status=LegalHold.Status.ACTIVE).exists())

    def test_legal_hold_person_targets_are_capability_protected_and_minimal(self):
        staff_person = Person.objects.create(
            first_name="Eligible", last_name="Staff", record_type=Person.RecordType.STAFF,
            primary_email="staff-target@example.com",
        )
        self.authenticate(self.admin)

        response = self.client.get(
            "/api/v1/privacy/legal-holds/person-targets/",
            {"q": "Eligible"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["id"], staff_person.pk)
        self.assertEqual(response.data["results"][0]["first_name"], "Eligible")
        self.assertNotIn("primary_email", response.data["results"][0])
        self.assertNotIn("mobile", response.data["results"][0])
        self.assertNotIn("age_range", response.data["results"][0])

    def test_pending_release_removes_duplicate_request_action_and_reject_allows_retry(self):
        self.authenticate(self.admin)
        create_response = self.client.post(
            self.holds_url,
            {"person_id": self.person.pk, "reason": "Pending release action check"},
            format="json",
        )
        hold_id = create_response.data["public_id"]
        first = self.client.post(
            f"/api/v1/privacy/legal-holds/{hold_id}/release-requests/",
            {"reason": "First release request"},
            format="json",
        )
        self.assertEqual(first.status_code, 201)
        duplicate = self.client.post(
            f"/api/v1/privacy/legal-holds/{hold_id}/release-requests/",
            {"reason": "Duplicate release request"},
            format="json",
        )
        self.assertEqual(duplicate.status_code, 400)
        projection = self.client.get(self.holds_url).data["results"][0]
        self.assertEqual(projection["permitted_actions"], [])
        self.authenticate(self.other_admin)
        rejected = self.client.post(
            f"/api/v1/privacy/legal-hold-release-requests/{first.data['public_id']}/reject/",
            {"reason": "Hold remains required"},
            format="json",
        )
        self.assertEqual(rejected.status_code, 200)
        self.authenticate(self.admin)
        retry = self.client.post(
            f"/api/v1/privacy/legal-holds/{hold_id}/release-requests/",
            {"reason": "Second release request"},
            format="json",
        )
        self.assertEqual(retry.status_code, 201)

    def test_legal_hold_access_denial_records_route_operation(self):
        StaffCapabilityAssignment.objects.filter(
            user=self.other_admin,
            capability__code=CapabilityDefinition.LEGAL_HOLD_ADMIN,
        ).delete()
        self.authenticate(self.other_admin)

        response = self.client.get(self.holds_url)

        self.assertEqual(response.status_code, 403)
        denial = AuditEvent.objects.filter(
            action=AuditEvent.Action.CAPABILITY_PERMISSION_MUTATED,
            metadata__event_type="PRIVACY_AUTHORIZATION_DENIED",
            metadata__denial_code="LEGAL_HOLD_ADMIN_REQUIRED",
        ).latest("id")
        self.assertEqual(denial.metadata["operation"], "LEGAL_HOLD_ACCESS")

    def test_event_reference_places_event_specific_hold(self):
        self.authenticate(self.admin)
        event = AuditEvent.objects.create(
            actor_user=self.admin,
            action=AuditEvent.Action.PERSON_UPDATED,
            entity_type="Person",
            entity_id=str(self.person.pk),
            audit_domain=AuditEvent.Domain.PRIVACY,
        )
        history = self.client.get(self.audit_history_url)
        reference = next(item["legal_hold_event_reference"] for item in history.data["results"] if item["id"] == str(event.id))
        response = self.client.post(self.holds_url, {"audit_event_reference": reference, "reason": "Event-specific retention review"}, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["scope"], LegalHold.Scope.EVENT)
        self.assertIsNone(response.data["person_id"])

    def test_legal_hold_projection_exposes_safe_release_requests_to_independent_reviewer(self):
        self.authenticate(self.admin)
        create_response = self.client.post(self.holds_url, {"person_id": self.person.pk, "reason": "Investigation"}, format="json")
        hold_id = create_response.data["public_id"]
        request_response = self.client.post(
            f"/api/v1/privacy/legal-holds/{hold_id}/release-requests/",
            {"reason": "Independent review requested"}, format="json",
        )
        self.authenticate(self.other_admin)
        response = self.client.get(self.holds_url)
        self.assertEqual(response.status_code, 200)
        release_request = response.data["results"][0]["release_requests"][0]
        self.assertEqual(release_request["public_id"], request_response.data["public_id"])
        self.assertEqual(release_request["status"], LegalHoldReleaseRequest.Status.PENDING)
        self.assertEqual(release_request["permitted_actions"], ["approve", "reject"])
        self.assertEqual(release_request["requested_by_id"], self.admin.pk)
        self.assertEqual(release_request["justification"], "Independent review requested")
        self.assertEqual(release_request["requested_by"]["display_name"], "Privacy Admin")

    def test_release_request_projection_exposes_only_requester_cancellation(self):
        self.authenticate(self.admin)
        create_response = self.client.post(self.holds_url, {"person_id": self.person.pk, "reason": "Investigation"}, format="json")
        hold_id = create_response.data["public_id"]
        self.client.post(
            f"/api/v1/privacy/legal-holds/{hold_id}/release-requests/",
            {"reason": "Independent review requested"}, format="json",
        )
        response = self.client.get(self.holds_url)
        self.assertEqual(response.status_code, 200)
        actions = response.data["results"][0]["release_requests"][0]["permitted_actions"]
        self.assertEqual(actions, ["cancel"])
        self.assertNotIn("approve", actions)
        self.assertNotIn("reject", actions)

    def test_technical_person_cannot_become_a_person_hold_target(self):
        self.authenticate(self.admin)
        technical = Person.objects.create(
            first_name="Technical",
            last_name="Subject",
            record_type=Person.RecordType.TECHNICAL,
        )

        response = self.client.post(
            self.holds_url,
            {"person_id": technical.pk, "reason": "Should not be accepted"},
            format="json",
        )

        self.assertEqual(response.status_code, 404)

    def test_rejection_preserves_hold_and_allows_subsequent_request(self):
        self.authenticate(self.admin)
        create_response = self.client.post(self.holds_url, {"person_id": self.person.pk, "reason": "Investigation"}, format="json")
        hold_id = create_response.data["public_id"]
        request_response = self.client.post(
            f"/api/v1/privacy/legal-holds/{hold_id}/release-requests/",
            {"reason": "Proposed release"}, format="json",
        )
        self.authenticate(self.other_admin)
        reject_response = self.client.post(
            f"/api/v1/privacy/legal-hold-release-requests/{request_response.data['public_id']}/reject/",
            {"reason": "Hold remains necessary"}, format="json",
        )
        self.assertEqual(reject_response.status_code, 200)
        self.assertEqual(reject_response.data["status"], LegalHoldReleaseRequest.Status.REJECTED)
        self.assertTrue(LegalHold.objects.filter(public_id=hold_id, status=LegalHold.Status.ACTIVE).exists())
        second = self.client.post(
            f"/api/v1/privacy/legal-holds/{hold_id}/release-requests/",
            {"reason": "Later review"}, format="json",
        )
        self.assertEqual(second.status_code, 201)

    def test_original_requester_can_cancel_pending_release_without_releasing_hold(self):
        self.authenticate(self.admin)
        create_response = self.client.post(self.holds_url, {"person_id": self.person.pk, "reason": "Investigation"}, format="json")
        hold_id = create_response.data["public_id"]
        request_response = self.client.post(
            f"/api/v1/privacy/legal-holds/{hold_id}/release-requests/",
            {"reason": "Proposed release"}, format="json",
        )
        cancel_response = self.client.post(
            f"/api/v1/privacy/legal-hold-release-requests/{request_response.data['public_id']}/cancel/",
            {"reason": "Matter remains active"}, format="json",
        )
        self.assertEqual(cancel_response.status_code, 200)
        self.assertEqual(cancel_response.data["status"], LegalHoldReleaseRequest.Status.CANCELLED)
        request = LegalHoldReleaseRequest.objects.get(public_id=request_response.data["public_id"])
        self.assertEqual(request.cancelled_by_id, self.admin.pk)
        self.assertEqual(request.cancellation_reason, "Matter remains active")
        self.assertTrue(LegalHold.objects.filter(public_id=hold_id, status=LegalHold.Status.ACTIVE).exists())
        event = AuditEvent.objects.filter(action=AuditEvent.Action.LEGAL_HOLD_RELEASE_CANCELLED).latest("id")
        self.assertEqual(event.actor_user_id, self.admin.pk)
        self.assertEqual(event.metadata["outcome"], "CANCELLED")
        retry = self.client.post(
            f"/api/v1/privacy/legal-holds/{hold_id}/release-requests/",
            {"reason": "Later review"}, format="json",
        )
        self.assertEqual(retry.status_code, 201)

    def test_only_original_requester_can_cancel_and_reason_is_required(self):
        self.authenticate(self.admin)
        create_response = self.client.post(self.holds_url, {"person_id": self.person.pk, "reason": "Investigation"}, format="json")
        hold_id = create_response.data["public_id"]
        request_response = self.client.post(
            f"/api/v1/privacy/legal-holds/{hold_id}/release-requests/",
            {"reason": "Proposed release"}, format="json",
        )
        self.authenticate(self.other_admin)
        denied = self.client.post(
            f"/api/v1/privacy/legal-hold-release-requests/{request_response.data['public_id']}/cancel/",
            {"reason": "Not the requester"}, format="json",
        )
        self.assertEqual(denied.status_code, 400)
        self.authenticate(self.admin)
        missing_reason = self.client.post(
            f"/api/v1/privacy/legal-hold-release-requests/{request_response.data['public_id']}/cancel/",
            {"reason": ""}, format="json",
        )
        self.assertEqual(missing_reason.status_code, 400)
        self.assertEqual(LegalHoldReleaseRequest.objects.get(public_id=request_response.data["public_id"]).status, LegalHoldReleaseRequest.Status.PENDING)

    def test_superuser_direct_release_is_explicit_and_audited(self):
        self.authenticate(self.admin)
        create_response = self.client.post(self.holds_url, {"person_id": self.person.pk, "reason": "Investigation"}, format="json")
        hold_id = create_response.data["public_id"]
        request_response = self.client.post(
            f"/api/v1/privacy/legal-holds/{hold_id}/release-requests/",
            {"reason": "Direct review"}, format="json",
        )
        self.authenticate(self.superuser)
        response = self.client.post(
            f"/api/v1/privacy/legal-holds/{hold_id}/direct-release/",
            {"reason": "Emergency direct review"}, format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], LegalHold.Status.RELEASED)
        self.assertTrue(AuditEvent.objects.filter(action=AuditEvent.Action.LEGAL_HOLD_RELEASED, actor_user=self.superuser).exists())
        request = LegalHoldReleaseRequest.objects.get(public_id=request_response.data["public_id"])
        self.assertEqual(request.status, LegalHoldReleaseRequest.Status.APPROVED)
        event = AuditEvent.objects.filter(action=AuditEvent.Action.LEGAL_HOLD_RELEASED, actor_user=self.superuser).latest("id")
        self.assertTrue(event.metadata["superuser_authority"])
