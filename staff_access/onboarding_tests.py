from types import SimpleNamespace

from django.test import TestCase

from accounts.models import User
from people.models import Person
from staff_access.models import StaffAdministratorProposal, StaffInvitation, StaffRole, StaffRoleAssignment
from notifications.models import TransactionalEmailJob
from staff_access.onboarding import search_onboarding_people, submit_onboarding_request, validate_onboarding_request
from staff_access.services import inspect_staff_invitation, _invitation_token_hash, redeem_staff_invitation


class StaffOnboardingValidationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.actor = User.objects.create_superuser(
            email="onboarding-admin@example.com",
            password="Strong-password-123!",
            person=Person.objects.create(first_name="Onboarding", last_name="Admin", record_type=Person.RecordType.STAFF),
        )
        cls.person = Person.objects.create(
            first_name="Existing", last_name="Person", primary_email="existing@example.com", record_type=Person.RecordType.STAFF,
        )
        cls.user = User.objects.create_user(
            email="existing-login@example.com", password="Strong-password-123!", person=cls.person,
        )
        cls.request = SimpleNamespace(user=cls.actor, headers={}, META={})

    def test_lookup_is_name_based_and_returns_safe_candidate_projection(self):
        response = search_onboarding_people(query="Existing", request=self.request)
        self.assertEqual(response["results"][0]["person_id"], self.person.pk)
        self.assertEqual(response["results"][0]["onboarding_eligibility"], "ELIGIBLE_FOR_VALIDATION")

    def test_existing_person_reuses_active_account_without_persisting_changes(self):
        response = validate_onboarding_request(
            attrs={"person_id": self.person.pk, "role_code": StaffRole.CRM_VIEWER, "reason": "Operational staffing"},
            request=self.request,
        )
        self.assertTrue(response["eligible"])
        self.assertEqual(response["user"]["account_resolution"], "EXISTING_ACTIVE_ACCOUNT")
        self.assertFalse(response["submission_available"])

    def test_active_staff_is_not_onboarded_again(self):
        StaffRoleAssignment.objects.assign_role(
            user=self.user, role=StaffRole.CRM_VIEWER, assigned_by=self.actor,
        )
        response = validate_onboarding_request(
            attrs={"person_id": self.person.pk, "role_code": StaffRole.CRM_VIEWER, "reason": "Duplicate attempt"},
            request=self.request,
        )
        self.assertFalse(response["eligible"])
        self.assertEqual(response["conflict"]["code"], "ACTIVE_STAFF")

    def test_new_person_validation_does_not_create_records(self):
        before_people = Person.objects.count()
        before_users = User.objects.count()
        response = validate_onboarding_request(
            attrs={
                "first_name": "New", "last_name": "Staff", "primary_email": "new-person@example.com",
                "mobile": "", "login_email": "new-login@example.com", "role_code": StaffRole.CRM_MANAGER,
                "reason": "New operational role",
            },
            request=self.request,
        )
        self.assertTrue(response["eligible"])
        self.assertFalse(response["submission_available"])
        self.assertEqual(Person.objects.count(), before_people)
        self.assertEqual(User.objects.count(), before_users)

    def test_submission_creates_pending_identity_invitation_and_email_job(self):
        invitation = submit_onboarding_request(
            attrs={
                "first_name": "New", "last_name": "Staff", "primary_email": "new-person@example.com",
                "mobile": "", "login_email": "new-login@example.com", "role_code": StaffRole.CRM_MANAGER,
                "reason": "Approved operational staffing",
            },
            actor_user=self.actor,
            request=self.request,
        )
        self.assertEqual(invitation.target_user.account_lifecycle_state, User.AccountLifecycleState.PENDING_ACTIVATION)
        self.assertFalse(invitation.target_user.has_usable_password())
        self.assertEqual(TransactionalEmailJob.objects.get(staff_invitation=invitation).status, TransactionalEmailJob.Status.PENDING)
        self.assertEqual(StaffInvitation.objects.filter(target_user=invitation.target_user, accepted_at__isnull=True, revoked_at__isnull=True, superseded_at__isnull=True).count(), 1)

    def test_administrator_submission_creates_pending_governance_proposal_without_invitation(self):
        proposal = submit_onboarding_request(
            attrs={"first_name": "Admin", "last_name": "Candidate", "primary_email": "admin-candidate@example.com", "login_email": "admin-candidate@example.com", "role_code": StaffRole.CRM_ADMIN, "reason": "Admin staffing"},
            actor_user=self.actor,
            request=self.request,
        )
        self.assertIsInstance(proposal, StaffAdministratorProposal)
        self.assertEqual(proposal.status, StaffAdministratorProposal.Status.PENDING)
        self.assertFalse(StaffInvitation.objects.filter(target_user=proposal.target_user).exists())
        self.assertFalse(TransactionalEmailJob.objects.filter(staff_invitation__target_user=proposal.target_user).exists())

    def test_invitation_inspection_does_not_consume_digest(self):
        invitation = submit_onboarding_request(
            attrs={"person_id": self.person.pk, "login_email": self.user.email, "role_code": StaffRole.CRM_VIEWER, "reason": "Prepare staff access"},
            actor_user=self.actor, request=self.request,
        )
        raw_token = "inspection-token-value-123456789"
        invitation.token_hash = _invitation_token_hash(raw_token)
        invitation.save(update_fields=["token_hash"])
        result = inspect_staff_invitation(public_id=invitation.public_id, raw_token=raw_token)
        self.assertTrue(result["usable"])
        invitation.refresh_from_db()
        self.assertEqual(invitation.token_hash, _invitation_token_hash(raw_token))

    def test_active_existing_user_must_authenticate_as_exact_target(self):
        invitation = submit_onboarding_request(
            attrs={"person_id": self.person.pk, "login_email": self.user.email, "role_code": StaffRole.CRM_VIEWER, "reason": "Prepare staff access"},
            actor_user=self.actor, request=self.request,
        )
        raw_token = "active-account-token-value-123456"
        invitation.token_hash = _invitation_token_hash(raw_token)
        invitation.save(update_fields=["token_hash"])
        with self.assertRaises(Exception):
            redeem_staff_invitation(public_id=invitation.public_id, raw_token=raw_token, authenticated_user=self.actor)
