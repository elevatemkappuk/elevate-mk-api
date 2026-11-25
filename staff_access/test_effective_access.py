from unittest.mock import patch

from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from rest_framework.request import Request
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import User
from audit.models import AuditEvent
from people.models import Person
from staff_access.models import (
    CapabilityDefinition,
    CapabilityPolicyState,
    StaffCapabilityAssignment,
    StaffCapabilityRestriction,
    StaffRole,
    StaffRoleAssignment,
)
from staff_access.permissions import (
    CRM_EFFECTIVE_ACCESS_DISCLOSURE_ALLOWLIST,
    get_effective_capability_codes_for_user,
)


@override_settings(ROOT_URLCONF="config.urls")
class EffectiveAccessEndpointTests(TestCase):
    url = "/api/v1/staff/access/me/"

    def setUp(self):
        self.password = "testpass123"
        self.manager = self.create_staff_user("access-manager@example.com")
        self.admin = self.create_staff_user("access-admin@example.com")
        self.viewer = self.create_staff_user("access-viewer@example.com")
        self.non_staff = User.objects.create_user(
            email="access-member@example.com",
            password=self.password,
            person_first_name="Access",
            person_last_name="Member",
        )
        StaffRoleAssignment.objects.assign_role(user=self.manager, role=StaffRole.CRM_MANAGER)
        StaffRoleAssignment.objects.assign_role(user=self.admin, role=StaffRole.CRM_ADMIN)
        StaffRoleAssignment.objects.assign_role(user=self.viewer, role=StaffRole.CRM_VIEWER)

    def create_staff_user(self, email, *, superuser=False):
        return User.objects.create_user(
            email=email,
            password=self.password,
            person_first_name="Access",
            person_last_name="Staff",
            person_record_type=Person.RecordType.STAFF,
            is_staff=superuser,
            is_superuser=superuser,
        )

    def session_client(self, user):
        client = APIClient()
        response = client.post(
            "/api/v1/auth/login/",
            {"email": user.email, "password": self.password},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        return client

    def test_response_has_allowlisted_projection_and_policy_version(self):
        client = self.session_client(self.manager)
        response = client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.data), {"capability_codes", "policy_version"})
        self.assertEqual(response.data["capability_codes"], [])
        self.assertEqual(
            response.data["policy_version"],
            CapabilityPolicyState.objects.get(singleton_key=1, key="capability-governance").version,
        )
        self.assertEqual(
            CRM_EFFECTIVE_ACCESS_DISCLOSURE_ALLOWLIST,
            frozenset({CapabilityDefinition.STAFF_CAPABILITY_ADMIN}),
        )

    def test_all_active_operational_crm_roles_are_admitted(self):
        for user in (self.admin, self.manager, self.viewer):
            response = self.session_client(user).get(self.url)
            self.assertEqual(response.status_code, 200)

    def test_anonymous_and_authenticated_non_staff_are_rejected(self):
        self.assertEqual(APIClient().get(self.url).status_code, 401)
        self.assertEqual(self.session_client(self.non_staff).get(self.url).status_code, 403)

    def test_mobile_jwt_is_not_accepted_by_crm_only_endpoint(self):
        token = RefreshToken.for_user(self.manager).access_token
        response = APIClient().get(self.url, HTTP_AUTHORIZATION=f"Bearer {token}")

        self.assertEqual(response.status_code, 401)

    def test_response_is_not_cacheable_as_shared_or_storable_state(self):
        response = self.session_client(self.manager).get(self.url)

        self.assertEqual(response["Cache-Control"], "private, no-store")

    def test_endpoint_passes_allowlist_and_request_to_single_evaluator_call(self):
        client = self.session_client(self.manager)
        with patch("staff_access.views.get_effective_capability_codes_for_user", return_value=set()) as evaluator:
            response = client.get(self.url)

        self.assertEqual(response.status_code, 200)
        evaluator.assert_called_once()
        called_args, called_kwargs = evaluator.call_args
        self.assertEqual(called_args, (self.manager,))
        self.assertEqual(
            called_kwargs["requested_codes"],
            frozenset({CapabilityDefinition.STAFF_CAPABILITY_ADMIN}),
        )
        self.assertIsInstance(called_kwargs["request"], Request)
        self.assertIs(called_kwargs["request"]._request, response.wsgi_request)
        self.assertEqual(called_kwargs["request"].user.pk, self.manager.pk)

    def test_existing_evaluator_precedence_remains_live(self):
        contact = CapabilityDefinition.objects.get(code=CapabilityDefinition.PERSON_CONTACT_READ)
        export = CapabilityDefinition.objects.get(code=CapabilityDefinition.PERSON_EXPORT)
        self.assertEqual(
            get_effective_capability_codes_for_user(self.manager, requested_codes={contact.code, export.code}),
            {contact.code},
        )

        StaffCapabilityAssignment.objects.create(
            user=self.manager,
            capability=export,
            granted_by=self.admin,
        )
        StaffCapabilityRestriction.objects.create(
            user=self.manager,
            capability=contact,
            restricted_by=self.admin,
        )

        self.assertEqual(
            get_effective_capability_codes_for_user(self.manager, requested_codes={contact.code, export.code}),
            {export.code},
        )

    def test_ordinary_lookup_does_not_write_audit_event(self):
        client = self.session_client(self.manager)
        before = AuditEvent.objects.count()

        response = client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(AuditEvent.objects.count(), before)

    def test_superuser_audit_bypass_is_preserved_after_crm_admission(self):
        superuser = self.create_staff_user("access-superuser@example.com", superuser=True)
        StaffRoleAssignment.objects.assign_role(user=superuser, role=StaffRole.CRM_ADMIN)
        client = self.session_client(superuser)
        before = AuditEvent.objects.filter(action=AuditEvent.Action.PRIVACY_SUPERUSER_BYPASS).count()

        response = client.get(self.url)

        self.assertEqual(response.status_code, 200)
        bypass = AuditEvent.objects.filter(action=AuditEvent.Action.PRIVACY_SUPERUSER_BYPASS)
        self.assertEqual(bypass.count(), before + 1)
        self.assertEqual(
            bypass.latest("id").metadata["capability_codes"],
            [CapabilityDefinition.STAFF_CAPABILITY_ADMIN],
        )
