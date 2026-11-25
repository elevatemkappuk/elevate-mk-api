import threading
from unittest import skipUnless
from django.contrib.admin.sites import AdminSite
from django.contrib.auth.models import AnonymousUser
from django.contrib.messages.storage.fallback import FallbackStorage
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection, transaction
from django.db import IntegrityError
from django.http import HttpRequest
from django.test import RequestFactory, TransactionTestCase
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from unittest.mock import patch

from accounts.models import User
from audit.models import AuditEvent
from people.models import Person
from staff_access.admin import StaffRoleAdmin, StaffRoleAssignmentAdmin
from staff_access.exceptions import FinalCrmAdminProtectionError
from staff_access.models import (
    CapabilityDefinition,
    CapabilityPolicyState,
    RoleCapabilityDefault,
    StaffCapabilityAssignment,
    StaffCapabilityRestriction,
    StaffRole,
    StaffRoleAssignment,
)
from staff_access.permissions import get_effective_capability_codes_for_user, user_has_capability


class StaffRoleModelTests(TestCase):
    def test_canonical_roles_are_seeded(self):
        codes = list(StaffRole.objects.order_by("code").values_list("code", flat=True))
        self.assertEqual(codes, [StaffRole.CRM_ADMIN, StaffRole.CRM_MANAGER, StaffRole.CRM_VIEWER])

    def test_role_codes_are_unique(self):
        with self.assertRaises(IntegrityError):
            StaffRole.objects.create(code=StaffRole.CRM_ADMIN, name="Duplicate")

    def test_role_activation_state_is_respected_by_active_query(self):
        role = StaffRole.objects.get(code=StaffRole.CRM_VIEWER)
        role.is_active = False
        role.save(update_fields=["is_active"])
        user = User.objects.create_user(
            email="viewer@example.com",
            password="testpass123",
            person_first_name="Viewer",
            person_last_name="User",
            person_record_type=Person.RecordType.STAFF,
        )
        StaffRoleAssignment.objects.assign_role(user=user, role=role)

        self.assertFalse(StaffRoleAssignment.objects.active().exists())


class CapabilityFoundationTests(TestCase):
    def create_user(self, email, *, superuser=False):
        if superuser:
            return User.objects.create_superuser(
                email=email,
                password="testpass123",
                person_first_name="Django",
                person_last_name="Superuser",
            )
        return User.objects.create_user(
            email=email,
            password="testpass123",
            person_first_name=email.split("@")[0],
            person_last_name="User",
            person_record_type=Person.RecordType.STAFF,
        )

    def setUp(self):
        self.viewer = self.create_user("cap-viewer@example.com")
        self.manager = self.create_user("cap-manager@example.com")
        self.admin = self.create_user("cap-admin@example.com")
        self.actor = self.create_user("cap-actor@example.com")
        StaffRoleAssignment.objects.assign_role(user=self.viewer, role=StaffRole.CRM_VIEWER)
        StaffRoleAssignment.objects.assign_role(user=self.manager, role=StaffRole.CRM_MANAGER)
        StaffRoleAssignment.objects.assign_role(user=self.admin, role=StaffRole.CRM_ADMIN)

    def test_canonical_capabilities_and_role_defaults_match_approved_matrix(self):
        self.assertEqual(
            set(CapabilityDefinition.objects.values_list("code", flat=True)),
            set(CapabilityDefinition.canonical_codes()),
        )
        self.assertEqual(
            set(
                RoleCapabilityDefault.objects.filter(role__code=StaffRole.CRM_ADMIN).values_list(
                    "capability__code", flat=True
                )
            ),
            {
                CapabilityDefinition.PERSON_CONTACT_READ,
                CapabilityDefinition.PERSON_DEMOGRAPHIC_READ,
                CapabilityDefinition.PERSON_EXPORT,
                CapabilityDefinition.STAFF_CAPABILITY_ADMIN,
            },
        )
        self.assertEqual(
            set(
                RoleCapabilityDefault.objects.filter(role__code=StaffRole.CRM_MANAGER).values_list(
                    "capability__code", flat=True
                )
            ),
            {CapabilityDefinition.PERSON_CONTACT_READ},
        )
        self.assertFalse(RoleCapabilityDefault.objects.filter(role__code=StaffRole.CRM_VIEWER).exists())
        self.assertFalse(
            RoleCapabilityDefault.objects.filter(
                capability__code__in=[
                    CapabilityDefinition.PERSON_RESTRICTED_ACCESS,
                    CapabilityDefinition.PRIVACY_AUDIT_READ,
                ]
            ).exists()
        )

    def test_role_defaults_are_evaluated_by_backend(self):
        self.assertEqual(get_effective_capability_codes_for_user(self.viewer), set())
        self.assertEqual(
            get_effective_capability_codes_for_user(self.manager),
            {CapabilityDefinition.PERSON_CONTACT_READ},
        )
        self.assertEqual(
            get_effective_capability_codes_for_user(self.admin),
            {
                CapabilityDefinition.PERSON_CONTACT_READ,
                CapabilityDefinition.PERSON_DEMOGRAPHIC_READ,
                CapabilityDefinition.PERSON_EXPORT,
                CapabilityDefinition.STAFF_CAPABILITY_ADMIN,
            },
        )

    def test_active_and_suspended_grants_and_historical_rows_are_distinguished(self):
        contact = CapabilityDefinition.objects.get(code=CapabilityDefinition.PERSON_CONTACT_READ)
        export = CapabilityDefinition.objects.get(code=CapabilityDefinition.PERSON_EXPORT)
        active = StaffCapabilityAssignment.objects.create(
            user=self.viewer, capability=contact, granted_by=self.actor
        )
        suspended = StaffCapabilityAssignment.objects.create(
            user=self.viewer,
            capability=export,
            state=StaffCapabilityAssignment.State.SUSPENDED,
            granted_by=self.actor,
        )
        revoked = StaffCapabilityAssignment.objects.create(
            user=self.viewer,
            capability=export,
            state=StaffCapabilityAssignment.State.REVOKED,
            granted_by=self.actor,
        )

        self.assertTrue(user_has_capability(self.viewer, CapabilityDefinition.PERSON_CONTACT_READ))
        self.assertFalse(user_has_capability(self.viewer, CapabilityDefinition.PERSON_EXPORT))
        self.assertEqual(StaffCapabilityAssignment.objects.current().count(), 2)
        self.assertEqual(StaffCapabilityAssignment.objects.active().get(pk=active.pk), active)
        self.assertEqual(suspended.state, StaffCapabilityAssignment.State.SUSPENDED)
        self.assertEqual(revoked.state, StaffCapabilityAssignment.State.REVOKED)

    def test_individual_grant_does_not_bypass_staff_role_eligibility(self):
        non_staff = self.create_user("cap-no-role@example.com")
        capability = CapabilityDefinition.objects.get(code=CapabilityDefinition.PERSON_CONTACT_READ)
        StaffCapabilityAssignment.objects.create(user=non_staff, capability=capability, granted_by=self.actor)

        self.assertFalse(user_has_capability(non_staff, CapabilityDefinition.PERSON_CONTACT_READ))

    def test_active_restriction_overrides_active_individual_grant(self):
        capability_code = CapabilityDefinition.PERSON_CONTACT_READ
        capability = CapabilityDefinition.objects.get(code=capability_code)
        StaffCapabilityAssignment.objects.create(
            user=self.viewer,
            capability=capability,
            state=StaffCapabilityAssignment.State.ACTIVE,
            granted_by=self.actor,
        )

        self.assertTrue(user_has_capability(self.viewer, capability_code))

        StaffCapabilityRestriction.objects.create(
            user=self.viewer,
            capability=capability,
            restricted_by=self.actor,
        )

        self.assertFalse(user_has_capability(self.viewer, capability_code))

    def test_active_restriction_overrides_active_role_default(self):
        capability_code = CapabilityDefinition.PERSON_CONTACT_READ
        capability = CapabilityDefinition.objects.get(code=capability_code)

        self.assertTrue(user_has_capability(self.manager, capability_code))
        self.assertTrue(
            RoleCapabilityDefault.objects.filter(
                role__code=StaffRole.CRM_MANAGER,
                capability=capability,
                is_enabled=True,
            ).exists()
        )

        StaffCapabilityRestriction.objects.create(
            user=self.manager,
            capability=capability,
            restricted_by=self.actor,
        )

        self.assertFalse(user_has_capability(self.manager, capability_code))

    def test_current_assignment_pair_is_database_unique(self):
        capability = CapabilityDefinition.objects.get(code=CapabilityDefinition.PERSON_RESTRICTED_ACCESS)
        StaffCapabilityAssignment.objects.create(
            user=self.viewer,
            capability=capability,
            state=StaffCapabilityAssignment.State.ACTIVE,
            granted_by=self.actor,
        )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                StaffCapabilityAssignment.objects.create(
                    user=self.viewer,
                    capability=capability,
                    state=StaffCapabilityAssignment.State.SUSPENDED,
                    granted_by=self.actor,
                )

        self.assertEqual(
            StaffCapabilityAssignment.objects.filter(user=self.viewer, capability=capability).count(),
            1,
        )

    def test_active_restriction_pair_is_database_unique(self):
        capability = CapabilityDefinition.objects.get(code=CapabilityDefinition.PERSON_RESTRICTED_ACCESS)
        StaffCapabilityRestriction.objects.create(
            user=self.viewer,
            capability=capability,
            restricted_by=self.actor,
        )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                StaffCapabilityRestriction.objects.create(
                    user=self.viewer,
                    capability=capability,
                    restricted_by=self.actor,
                )

        self.assertEqual(
            StaffCapabilityRestriction.objects.filter(user=self.viewer, capability=capability).count(),
            1,
        )

    def test_active_restriction_overrides_role_default_without_reactivating_grant(self):
        contact = CapabilityDefinition.objects.get(code=CapabilityDefinition.PERSON_CONTACT_READ)
        assignment = StaffCapabilityAssignment.objects.create(
            user=self.viewer,
            capability=contact,
            state=StaffCapabilityAssignment.State.SUSPENDED,
            suspended_at=timezone.now(),
            suspended_by=self.actor,
            granted_by=self.actor,
        )
        restriction = StaffCapabilityRestriction.objects.create(
            user=self.viewer, capability=contact, restricted_by=self.actor
        )

        self.assertFalse(user_has_capability(self.viewer, CapabilityDefinition.PERSON_CONTACT_READ))
        restriction.is_active = False
        restriction.removed_at = timezone.now()
        restriction.removed_by = self.actor
        restriction.save(update_fields=["is_active", "removed_at", "removed_by", "updated_at"])
        self.assertFalse(user_has_capability(self.viewer, CapabilityDefinition.PERSON_CONTACT_READ))
        assignment.refresh_from_db()
        self.assertEqual(assignment.state, StaffCapabilityAssignment.State.SUSPENDED)

    def test_empty_requested_capabilities_returns_empty_set(self):
        self.assertEqual(
            get_effective_capability_codes_for_user(self.admin, requested_codes=set()),
            set(),
        )

    def test_grant_suspend_regrant_preserves_current_assignment_and_revoke_preserves_history(self):
        restricted = CapabilityDefinition.objects.get(code=CapabilityDefinition.PERSON_RESTRICTED_ACCESS)

        assignment = StaffCapabilityAssignment.objects.create(
            user=self.viewer, capability=restricted, granted_by=self.actor
        )
        assignment.state = StaffCapabilityAssignment.State.SUSPENDED
        assignment.suspended_at = timezone.now()
        assignment.suspended_by = self.actor
        assignment.save(update_fields=["state", "suspended_at", "suspended_by", "updated_at"])
        self.assertEqual(assignment.state, StaffCapabilityAssignment.State.SUSPENDED)

        assignment.state = StaffCapabilityAssignment.State.ACTIVE
        assignment.suspended_at = None
        assignment.suspended_by = None
        assignment.save(update_fields=["state", "suspended_at", "suspended_by", "updated_at"])
        self.assertEqual(assignment.state, StaffCapabilityAssignment.State.ACTIVE)

        assignment.state = StaffCapabilityAssignment.State.REVOKED
        assignment.revoked_at = timezone.now()
        assignment.revoked_by = self.actor
        assignment.save(update_fields=["state", "revoked_at", "revoked_by", "updated_at"])
        revoked = assignment
        replacement = StaffCapabilityAssignment.objects.create(
            user=self.viewer, capability=restricted, granted_by=self.actor
        )
        self.assertEqual(revoked.pk, assignment.pk)
        self.assertNotEqual(replacement.pk, assignment.pk)
        self.assertEqual(
            StaffCapabilityAssignment.objects.filter(user=self.viewer, capability=restricted).count(),
            2,
        )

    def test_superuser_privacy_bypass_is_audited(self):
        superuser = self.create_user("privacy-superuser@example.com", superuser=True)
        before = AuditEvent.objects.filter(action=AuditEvent.Action.PRIVACY_SUPERUSER_BYPASS).count()

        self.assertTrue(user_has_capability(superuser, CapabilityDefinition.PERSON_EXPORT))

        event = AuditEvent.objects.get(action=AuditEvent.Action.PRIVACY_SUPERUSER_BYPASS)
        self.assertEqual(AuditEvent.objects.filter(action=AuditEvent.Action.PRIVACY_SUPERUSER_BYPASS).count(), before + 1)
        self.assertEqual(event.actor_user_id, superuser.id)
        self.assertEqual(event.metadata["capability_codes"], [CapabilityDefinition.PERSON_EXPORT])

    def test_superuser_bypass_fails_if_transactional_audit_fails(self):
        superuser = self.create_user("audit-failing-superuser@example.com", superuser=True)
        with patch("staff_access.permissions.record_audit_event", side_effect=RuntimeError("audit unavailable")):
            with self.assertRaises(RuntimeError):
                user_has_capability(superuser, CapabilityDefinition.PERSON_EXPORT)


class StaffRoleAssignmentModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="member@example.com",
            password="testpass123",
            person_first_name="Member",
            person_last_name="User",
            person_record_type=Person.RecordType.STAFF,
        )
        self.actor = User.objects.create_user(
            email="actor@example.com",
            password="testpass123",
            person_first_name="Actor",
            person_last_name="User",
            person_record_type=Person.RecordType.STAFF,
        )
        self.admin_role = StaffRole.objects.get(code=StaffRole.CRM_ADMIN)
        self.manager_role = StaffRole.objects.get(code=StaffRole.CRM_MANAGER)
        self.viewer_role = StaffRole.objects.get(code=StaffRole.CRM_VIEWER)

    def test_assignment_belongs_to_user(self):
        assignment = StaffRoleAssignment.objects.assign_role(user=self.user, role=self.admin_role, assigned_by=self.actor)
        self.assertEqual(assignment.user, self.user)

    def test_one_user_can_hold_multiple_different_roles(self):
        StaffRoleAssignment.objects.assign_role(user=self.user, role=self.admin_role)
        StaffRoleAssignment.objects.assign_role(user=self.user, role=self.manager_role)
        self.assertEqual(self.user.staff_role_assignments.count(), 2)

    def test_duplicate_user_role_assignment_is_prevented(self):
        StaffRoleAssignment.objects.assign_role(user=self.user, role=self.admin_role)
        with self.assertRaises((IntegrityError, ValidationError)):
            StaffRoleAssignment.objects.create(user=self.user, role=self.admin_role)

    def test_revocation_makes_assignment_inactive(self):
        assignment = StaffRoleAssignment.objects.assign_role(user=self.user, role=self.admin_role)
        StaffRoleAssignment.objects.assign_role(user=self.actor, role=self.admin_role)
        assignment.revoke(revoked_by=self.actor)
        assignment.refresh_from_db()

        self.assertFalse(assignment.is_active)
        self.assertIsNotNone(assignment.revoked_at)
        self.assertEqual(assignment.revoked_by, self.actor)

    def test_reactivation_restores_same_assignment_row(self):
        assignment = StaffRoleAssignment.objects.assign_role(user=self.user, role=self.admin_role)
        StaffRoleAssignment.objects.assign_role(user=self.actor, role=self.admin_role)
        original_id = assignment.id
        assignment.revoke(revoked_by=self.actor)
        reactivated = StaffRoleAssignment.objects.assign_role(user=self.user, role=self.admin_role)
        reactivated.refresh_from_db()

        self.assertEqual(reactivated.id, original_id)
        self.assertTrue(reactivated.is_active)
        self.assertIsNone(reactivated.revoked_at)
        self.assertIsNone(reactivated.revoked_by)

    def test_inactive_assignments_do_not_grant_access(self):
        assignment = StaffRoleAssignment.objects.assign_role(user=self.user, role=self.admin_role)
        StaffRoleAssignment.objects.assign_role(user=self.actor, role=self.admin_role)
        assignment.revoke()
        self.assertFalse(StaffRoleAssignment.objects.active().filter(pk=assignment.pk).exists())

    def test_assignments_to_inactive_roles_do_not_grant_access(self):
        self.viewer_role.is_active = False
        self.viewer_role.save(update_fields=["is_active"])
        StaffRoleAssignment.objects.assign_role(user=self.user, role=self.viewer_role)
        self.assertFalse(StaffRoleAssignment.objects.active().exists())

    def test_active_staff_role_assignment_still_works_for_technical_person_user(self):
        technical_user = User.objects.create_user(
            email="technical@example.com",
            password="testpass123",
            person_first_name="Technical",
            person_last_name="User",
            person_record_type=Person.RecordType.TECHNICAL,
        )

        assignment = StaffRoleAssignment.objects.assign_role(user=technical_user, role=self.admin_role)

        self.assertEqual(assignment.user.person.record_type, Person.RecordType.TECHNICAL)
        self.assertTrue(StaffRoleAssignment.objects.active().filter(pk=assignment.pk).exists())


class FinalCrmAdminProtectionTests(TestCase):
    def setUp(self):
        self.admin_role = StaffRole.objects.get(code=StaffRole.CRM_ADMIN)
        self.manager_role = StaffRole.objects.get(code=StaffRole.CRM_MANAGER)
        self.viewer_role = StaffRole.objects.get(code=StaffRole.CRM_VIEWER)
        self.actor = self.create_user("actor@example.com")

    def create_user(self, email):
        return User.objects.create_user(
            email=email,
            password="testpass123",
            person_first_name=email.split("@")[0],
            person_last_name="User",
            person_record_type=Person.RecordType.STAFF,
        )

    def assign(self, user, role):
        return StaffRoleAssignment.objects.assign_role(user=user, role=role, assigned_by=self.actor)

    def test_final_crm_admin_assignment_cannot_be_revoked(self):
        assignment = self.assign(self.create_user("admin@example.com"), self.admin_role)

        with self.assertRaises(FinalCrmAdminProtectionError):
            assignment.revoke(revoked_by=self.actor)

        assignment.refresh_from_db()
        self.assertTrue(assignment.is_active)

    def test_one_of_two_crm_admin_assignments_can_be_revoked(self):
        assignment = self.assign(self.create_user("admin-one@example.com"), self.admin_role)
        self.assign(self.create_user("admin-two@example.com"), self.admin_role)

        assignment.revoke(revoked_by=self.actor)

        assignment.refresh_from_db()
        self.assertFalse(assignment.is_active)

    def test_self_revocation_is_allowed_when_another_admin_remains(self):
        assignment = self.assign(self.actor, self.admin_role)
        self.assign(self.create_user("other-admin@example.com"), self.admin_role)

        assignment.revoke(revoked_by=self.actor)

        assignment.refresh_from_db()
        self.assertFalse(assignment.is_active)

    def test_self_revocation_is_rejected_for_final_admin(self):
        assignment = self.assign(self.actor, self.admin_role)

        with self.assertRaises(FinalCrmAdminProtectionError):
            assignment.revoke(revoked_by=self.actor)

        assignment.refresh_from_db()
        self.assertTrue(assignment.is_active)

    def test_manager_and_viewer_revocation_are_unaffected(self):
        manager_assignment = self.assign(self.create_user("manager@example.com"), self.manager_role)
        viewer_assignment = self.assign(self.create_user("viewer@example.com"), self.viewer_role)

        manager_assignment.revoke(revoked_by=self.actor)
        viewer_assignment.revoke(revoked_by=self.actor)

        manager_assignment.refresh_from_db()
        viewer_assignment.refresh_from_db()
        self.assertFalse(manager_assignment.is_active)
        self.assertFalse(viewer_assignment.is_active)

    def test_already_inactive_crm_admin_assignment_does_not_trigger_the_invariant(self):
        assignment = self.assign(self.create_user("admin-one@example.com"), self.admin_role)
        self.assign(self.create_user("admin-two@example.com"), self.admin_role)
        assignment.revoke(revoked_by=self.actor)

        assignment.revoke(revoked_by=self.actor)

        assignment.refresh_from_db()
        self.assertFalse(assignment.is_active)

    def test_crm_admin_reactivation_is_allowed(self):
        assignment = self.assign(self.create_user("admin-one@example.com"), self.admin_role)
        self.assign(self.create_user("admin-two@example.com"), self.admin_role)
        assignment.revoke(revoked_by=self.actor)

        assignment.reactivate()

        assignment.refresh_from_db()
        self.assertTrue(assignment.is_active)

    def test_canonical_crm_admin_role_cannot_be_deactivated(self):
        self.admin_role.is_active = False

        with self.assertRaises(FinalCrmAdminProtectionError):
            self.admin_role.save(update_fields=["is_active"])

        self.admin_role.refresh_from_db()
        self.assertTrue(self.admin_role.is_active)

    def test_django_superuser_does_not_count_as_operational_crm_admin(self):
        assignment = self.assign(self.create_user("operational-admin@example.com"), self.admin_role)
        User.objects.create_superuser(
            email="django-superuser@example.com",
            password="testpass123",
            person_first_name="Django",
            person_last_name="Superuser",
        )

        with self.assertRaises(FinalCrmAdminProtectionError):
            assignment.revoke(revoked_by=self.actor)

        assignment.refresh_from_db()
        self.assertTrue(assignment.is_active)


@override_settings(ROOT_URLCONF="staff_access.test_urls")
class StaffPermissionTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.non_staff_user = User.objects.create_user(
            email="member@example.com",
            password="testpass123",
            person_first_name="Member",
            person_last_name="User",
        )
        self.staff_user = User.objects.create_user(
            email="staff@example.com",
            password="testpass123",
            person_first_name="Staff",
            person_last_name="User",
            person_record_type=Person.RecordType.STAFF,
        )
        self.admin_role = StaffRole.objects.get(code=StaffRole.CRM_ADMIN)
        self.manager_role = StaffRole.objects.get(code=StaffRole.CRM_MANAGER)
        self.viewer_role = StaffRole.objects.get(code=StaffRole.CRM_VIEWER)

    def test_anonymous_user_receives_401(self):
        response = self.client.get("/test-permissions/any-staff/")
        self.assertEqual(response.status_code, 401)

    def test_authenticated_non_staff_user_receives_403(self):
        self.client.force_authenticate(user=self.non_staff_user)
        response = self.client.get("/test-permissions/any-staff/")
        self.assertEqual(response.status_code, 403)

    def test_user_with_permitted_active_staff_role_is_allowed(self):
        StaffRoleAssignment.objects.assign_role(user=self.staff_user, role=self.manager_role)
        self.client.force_authenticate(user=self.staff_user)
        response = self.client.get("/test-permissions/manager-or-admin/")
        self.assertEqual(response.status_code, 200)

    def test_technical_person_user_with_active_staff_role_is_allowed(self):
        technical_user = User.objects.create_user(
            email="technical-staff@example.com",
            password="testpass123",
            person_first_name="Technical",
            person_last_name="Staff",
            person_record_type=Person.RecordType.TECHNICAL,
        )
        StaffRoleAssignment.objects.assign_role(user=technical_user, role=self.admin_role)
        self.client.force_authenticate(user=technical_user)
        response = self.client.get("/test-permissions/admin-only/")
        self.assertEqual(response.status_code, 200)

    def test_revoked_role_receives_403(self):
        assignment = StaffRoleAssignment.objects.assign_role(user=self.staff_user, role=self.admin_role)
        backup_staff_user = User.objects.create_user(
            email="backup-staff@example.com",
            password="testpass123",
            person_first_name="Backup",
            person_last_name="Staff",
            person_record_type=Person.RecordType.STAFF,
        )
        StaffRoleAssignment.objects.assign_role(user=backup_staff_user, role=self.admin_role)
        assignment.revoke()
        self.client.force_authenticate(user=self.staff_user)
        response = self.client.get("/test-permissions/admin-only/")
        self.assertEqual(response.status_code, 403)

    def test_inactive_role_receives_403(self):
        self.viewer_role.is_active = False
        self.viewer_role.save(update_fields=["is_active"])
        StaffRoleAssignment.objects.assign_role(user=self.staff_user, role=self.viewer_role)
        self.client.force_authenticate(user=self.staff_user)
        response = self.client.get("/test-permissions/any-staff/")
        self.assertEqual(response.status_code, 403)


class StaffAdminTests(TestCase):
    def setUp(self):
        self.site = AdminSite()
        self.factory = RequestFactory()
        self.admin_user = User.objects.create_superuser(
            email="admin@example.com",
            password="testpass123",
            person_first_name="Admin",
            person_last_name="User",
        )
        self.target_user = User.objects.create_user(
            email="member@example.com",
            password="testpass123",
            person_first_name="Member",
            person_last_name="User",
            person_record_type=Person.RecordType.STAFF,
        )
        self.role_admin = StaffRoleAdmin(StaffRole, self.site)
        self.assignment_admin = StaffRoleAssignmentAdmin(StaffRoleAssignment, self.site)
        self.crm_admin_role = StaffRole.objects.get(code=StaffRole.CRM_ADMIN)
        self.crm_viewer_role = StaffRole.objects.get(code=StaffRole.CRM_VIEWER)

    def build_request(self):
        request = self.factory.post("/admin/")
        request.user = self.admin_user
        request.session = self.client.session
        setattr(request, "_messages", FallbackStorage(request))
        return request

    def test_staff_role_admin_prevents_deletion(self):
        request = self.build_request()

        self.assertFalse(self.role_admin.has_delete_permission(request, self.crm_admin_role))
        self.assertNotIn("delete_selected", self.role_admin.get_actions(request))

    def test_staff_role_admin_makes_canonical_code_and_name_read_only(self):
        request = self.build_request()

        readonly_fields = self.role_admin.get_readonly_fields(request, self.crm_admin_role)

        self.assertIn("code", readonly_fields)
        self.assertIn("name", readonly_fields)

    def test_staff_role_assignment_admin_prevents_deletion(self):
        request = self.build_request()

        self.assertFalse(self.assignment_admin.has_delete_permission(request))
        self.assertNotIn("delete_selected", self.assignment_admin.get_actions(request))

    def test_staff_role_assignment_admin_records_assigned_by_on_create(self):
        request = self.build_request()
        assignment = StaffRoleAssignment(user=self.target_user, role=self.crm_admin_role)

        self.assignment_admin.save_model(request, assignment, form=None, change=False)

        assignment.refresh_from_db()
        self.assertEqual(assignment.assigned_by, self.admin_user)
        self.assertTrue(assignment.is_active)
        self.assertIsNotNone(assignment.assigned_at)
        event = AuditEvent.objects.get(action=AuditEvent.Action.STAFF_ROLE_ASSIGNED)
        self.assertEqual(event.actor_user, self.admin_user)
        self.assertEqual(event.entity_type, "StaffRoleAssignment")
        self.assertEqual(event.entity_id, str(assignment.id))
        self.assertEqual(
            event.metadata,
            {
                "target_user_id": str(self.target_user.id),
                "staff_role_id": str(self.crm_admin_role.id),
                "staff_role_code": self.crm_admin_role.code,
                "policy_version": CapabilityPolicyState.objects.get(singleton_key=1).version,
            },
        )
        self.assertEqual(event.changes, {"is_active": {"from": None, "to": True}})

    def test_staff_role_assignment_admin_revokes_selected_assignments(self):
        request = self.build_request()
        assignment = StaffRoleAssignment.objects.assign_role(user=self.target_user, role=self.crm_admin_role)
        other_admin = User.objects.create_user(
            email="other-admin@example.com",
            password="testpass123",
            person_first_name="Other",
            person_last_name="Admin",
            person_record_type=Person.RecordType.STAFF,
        )
        StaffRoleAssignment.objects.assign_role(user=other_admin, role=self.crm_admin_role)

        self.assignment_admin.revoke_selected_assignments(request, StaffRoleAssignment.objects.filter(pk=assignment.pk))
        assignment.refresh_from_db()

        self.assertFalse(assignment.is_active)
        self.assertIsNotNone(assignment.revoked_at)
        self.assertEqual(assignment.revoked_by, self.admin_user)
        event = AuditEvent.objects.get(action=AuditEvent.Action.STAFF_ROLE_REVOKED)
        self.assertEqual(event.actor_user, self.admin_user)
        self.assertEqual(event.entity_type, "StaffRoleAssignment")
        self.assertEqual(event.entity_id, str(assignment.id))
        self.assertEqual(
            event.metadata,
            {
                "target_user_id": str(self.target_user.id),
                "staff_role_id": str(self.crm_admin_role.id),
                "staff_role_code": self.crm_admin_role.code,
                "policy_version": CapabilityPolicyState.objects.get(singleton_key=1).version,
            },
        )
        self.assertEqual(event.changes, {"is_active": {"from": True, "to": False}})

    def test_staff_role_assignment_admin_reactivates_selected_assignments(self):
        request = self.build_request()
        assignment = StaffRoleAssignment.objects.assign_role(user=self.target_user, role=self.crm_admin_role)
        other_admin = User.objects.create_user(
            email="other-admin@example.com",
            password="testpass123",
            person_first_name="Other",
            person_last_name="Admin",
            person_record_type=Person.RecordType.STAFF,
        )
        StaffRoleAssignment.objects.assign_role(user=other_admin, role=self.crm_admin_role)
        assignment.revoke(revoked_by=self.admin_user)

        self.assignment_admin.reactivate_selected_assignments(request, StaffRoleAssignment.objects.filter(pk=assignment.pk))
        assignment.refresh_from_db()

        self.assertTrue(assignment.is_active)
        self.assertIsNone(assignment.revoked_at)
        self.assertIsNone(assignment.revoked_by)
        event = AuditEvent.objects.get(action=AuditEvent.Action.STAFF_ROLE_REACTIVATED)
        self.assertEqual(event.actor_user, self.admin_user)
        self.assertEqual(event.entity_type, "StaffRoleAssignment")
        self.assertEqual(event.entity_id, str(assignment.id))
        self.assertEqual(
            event.metadata,
            {
                "target_user_id": str(self.target_user.id),
                "staff_role_id": str(self.crm_admin_role.id),
                "staff_role_code": self.crm_admin_role.code,
                "policy_version": CapabilityPolicyState.objects.get(singleton_key=1).version,
            },
        )
        self.assertEqual(event.changes, {"is_active": {"from": False, "to": True}})

    def test_staff_role_assignment_admin_reuses_existing_row_for_reactivation(self):
        request = self.build_request()
        assignment = StaffRoleAssignment.objects.assign_role(user=self.target_user, role=self.crm_viewer_role)
        original_id = assignment.id
        assignment.revoke(revoked_by=self.admin_user)

        duplicate_attempt = StaffRoleAssignment(user=self.target_user, role=self.crm_viewer_role)
        self.assignment_admin.save_model(request, duplicate_attempt, form=None, change=False)

        assignment.refresh_from_db()
        self.assertEqual(assignment.id, original_id)
        self.assertTrue(assignment.is_active)
        self.assertEqual(StaffRoleAssignment.objects.filter(user=self.target_user, role=self.crm_viewer_role).count(), 1)
        self.assertEqual(
            AuditEvent.objects.filter(action=AuditEvent.Action.STAFF_ROLE_ASSIGNED).count(),
            0,
        )
        event = AuditEvent.objects.get(action=AuditEvent.Action.STAFF_ROLE_REACTIVATED)
        self.assertEqual(event.entity_id, str(original_id))
        self.assertEqual(
            event.metadata,
            {
                "target_user_id": str(self.target_user.id),
                "staff_role_id": str(self.crm_viewer_role.id),
                "staff_role_code": self.crm_viewer_role.code,
                "policy_version": CapabilityPolicyState.objects.get(singleton_key=1).version,
            },
        )

    def test_staff_role_assignment_admin_duplicate_active_assignment_writes_no_audit_event(self):
        request = self.build_request()
        existing = StaffRoleAssignment.objects.assign_role(
            user=self.target_user,
            role=self.crm_admin_role,
            assigned_by=self.admin_user,
        )
        before_events = AuditEvent.objects.count()

        duplicate_attempt = StaffRoleAssignment(user=self.target_user, role=self.crm_admin_role)
        self.assignment_admin.save_model(request, duplicate_attempt, form=None, change=False)

        self.assertEqual(StaffRoleAssignment.objects.filter(user=self.target_user, role=self.crm_admin_role).count(), 1)
        self.assertEqual(duplicate_attempt.pk, existing.pk)
        self.assertEqual(AuditEvent.objects.count(), before_events)

    def test_staff_role_assignment_admin_grant_rolls_back_when_audit_write_fails(self):
        request = self.build_request()
        assignment = StaffRoleAssignment(user=self.target_user, role=self.crm_admin_role)
        before_events = AuditEvent.objects.count()

        with patch("staff_access.admin.record_audit_event", side_effect=RuntimeError("audit down")):
            with self.assertRaises(RuntimeError):
                self.assignment_admin.save_model(request, assignment, form=None, change=False)

        self.assertFalse(
            StaffRoleAssignment.objects.filter(user=self.target_user, role=self.crm_admin_role).exists()
        )
        self.assertEqual(AuditEvent.objects.count(), before_events)

    def test_staff_role_assignment_admin_reactivation_rolls_back_when_audit_write_fails(self):
        request = self.build_request()
        assignment = StaffRoleAssignment.objects.assign_role(user=self.target_user, role=self.crm_admin_role)
        other_admin = User.objects.create_user(
            email="other-admin@example.com",
            password="testpass123",
            person_first_name="Other",
            person_last_name="Admin",
            person_record_type=Person.RecordType.STAFF,
        )
        StaffRoleAssignment.objects.assign_role(user=other_admin, role=self.crm_admin_role)
        assignment.revoke(revoked_by=self.admin_user)
        revoked_at = assignment.revoked_at
        revoked_by = assignment.revoked_by

        with patch("staff_access.admin.record_audit_event", side_effect=RuntimeError("audit down")):
            with self.assertRaises(RuntimeError):
                self.assignment_admin.reactivate_selected_assignments(
                    request,
                    StaffRoleAssignment.objects.filter(pk=assignment.pk),
                )

        assignment.refresh_from_db()
        self.assertFalse(assignment.is_active)
        self.assertEqual(assignment.revoked_at, revoked_at)
        self.assertEqual(assignment.revoked_by, revoked_by)
        self.assertEqual(AuditEvent.objects.filter(action=AuditEvent.Action.STAFF_ROLE_REACTIVATED).count(), 0)

    def test_staff_role_assignment_admin_revoke_rolls_back_when_audit_write_fails(self):
        request = self.build_request()
        assignment = StaffRoleAssignment.objects.assign_role(user=self.target_user, role=self.crm_admin_role)
        other_admin = User.objects.create_user(
            email="other-admin@example.com",
            password="testpass123",
            person_first_name="Other",
            person_last_name="Admin",
            person_record_type=Person.RecordType.STAFF,
        )
        StaffRoleAssignment.objects.assign_role(user=other_admin, role=self.crm_admin_role)

        with patch("staff_access.admin.record_audit_event", side_effect=RuntimeError("audit down")):
            with self.assertRaises(RuntimeError):
                self.assignment_admin.revoke_selected_assignments(
                    request,
                    StaffRoleAssignment.objects.filter(pk=assignment.pk),
                )

        assignment.refresh_from_db()
        self.assertTrue(assignment.is_active)
        self.assertIsNone(assignment.revoked_at)
        self.assertIsNone(assignment.revoked_by)
        self.assertEqual(AuditEvent.objects.filter(action=AuditEvent.Action.STAFF_ROLE_REVOKED).count(), 0)

    def test_staff_role_admin_rejects_crm_admin_deactivation_in_its_form(self):
        request = self.build_request()
        form_class = self.role_admin.get_form(request, self.crm_admin_role, change=True)
        form = form_class({"is_active": False}, instance=self.crm_admin_role)

        self.assertFalse(form.is_valid())
        self.assertIn("is_active", form.errors)

    def test_staff_role_admin_deactivation_invalidates_existing_crm_sessions(self):
        StaffRoleAssignment.objects.assign_role(user=self.target_user, role=self.crm_viewer_role)
        client = APIClient()
        response = client.post(
            "/api/v1/auth/login/",
            {"email": self.target_user.email, "password": "testpass123"},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(client.get("/api/v1/people/").status_code, 200)

        request = self.build_request()
        role = StaffRole.objects.get(pk=self.crm_viewer_role.pk)
        role.is_active = False
        self.role_admin.save_model(request, role, form=None, change=True)

        self.assertEqual(client.get("/api/v1/people/").status_code, 401)
        event = AuditEvent.objects.get(action=AuditEvent.Action.STAFF_ROLE_REVOKED)
        self.assertEqual(event.actor_user, self.admin_user)
        self.assertEqual(event.entity_type, "StaffRole")

        role.is_active = True
        self.role_admin.save_model(request, role, form=None, change=True)
        self.assertEqual(client.get("/api/v1/people/").status_code, 401)

    def test_bulk_revoke_of_all_crm_admins_is_rejected_without_audit_events(self):
        request = self.build_request()
        first = StaffRoleAssignment.objects.assign_role(user=self.target_user, role=self.crm_admin_role)
        second_user = User.objects.create_user(
            email="second-admin@example.com",
            password="testpass123",
            person_first_name="Second",
            person_last_name="Admin",
            person_record_type=Person.RecordType.STAFF,
        )
        second = StaffRoleAssignment.objects.assign_role(user=second_user, role=self.crm_admin_role)

        self.assignment_admin.revoke_selected_assignments(
            request,
            StaffRoleAssignment.objects.filter(pk__in=[first.pk, second.pk]),
        )

        first.refresh_from_db()
        second.refresh_from_db()
        self.assertTrue(first.is_active)
        self.assertTrue(second.is_active)
        self.assertFalse(AuditEvent.objects.filter(action=AuditEvent.Action.STAFF_ROLE_REVOKED).exists())

    def test_mixed_bulk_revoke_succeeds_when_another_crm_admin_remains(self):
        request = self.build_request()
        crm_admin = StaffRoleAssignment.objects.assign_role(user=self.target_user, role=self.crm_admin_role)
        remaining_admin = User.objects.create_user(
            email="remaining-admin@example.com",
            password="testpass123",
            person_first_name="Remaining",
            person_last_name="Admin",
            person_record_type=Person.RecordType.STAFF,
        )
        StaffRoleAssignment.objects.assign_role(user=remaining_admin, role=self.crm_admin_role)
        manager = StaffRoleAssignment.objects.assign_role(
            user=User.objects.create_user(
                email="manager@example.com",
                password="testpass123",
                person_first_name="Manager",
                person_last_name="User",
                person_record_type=Person.RecordType.STAFF,
            ),
            role=StaffRole.objects.get(code=StaffRole.CRM_MANAGER),
        )
        viewer = StaffRoleAssignment.objects.assign_role(
            user=User.objects.create_user(
                email="viewer@example.com",
                password="testpass123",
                person_first_name="Viewer",
                person_last_name="User",
                person_record_type=Person.RecordType.STAFF,
            ),
            role=self.crm_viewer_role,
        )

        self.assignment_admin.revoke_selected_assignments(
            request,
            StaffRoleAssignment.objects.filter(pk__in=[crm_admin.pk, manager.pk, viewer.pk]),
        )

        for assignment in (crm_admin, manager, viewer):
            assignment.refresh_from_db()
            self.assertFalse(assignment.is_active)
        self.assertEqual(AuditEvent.objects.filter(action=AuditEvent.Action.STAFF_ROLE_REVOKED).count(), 3)


@skipUnless(connection.vendor == "postgresql", "Requires PostgreSQL row-level locking semantics.")
class FinalCrmAdminConcurrencyTests(TransactionTestCase):
    # This class only requires canonical role rows, which are recreated in
    # setUp. Avoid serialized rollback because the serialized permission
    # payload contains duplicate auth_permission natural keys in this project.
    serialized_rollback = False

    def setUp(self):
        self.role, _created = StaffRole.objects.update_or_create(
            code=StaffRole.CRM_ADMIN,
            defaults={"name": "CRM Admin", "is_active": True},
        )
        self.first = self.create_assignment("first-admin@example.com")
        self.second = self.create_assignment("second-admin@example.com")

    def create_assignment(self, email):
        user = User.objects.create_user(
            email=email,
            password="testpass123",
            person_first_name=email.split("@")[0],
            person_last_name="User",
            person_record_type=Person.RecordType.STAFF,
        )
        return StaffRoleAssignment.objects.assign_role(user=user, role=self.role)

    def test_concurrent_admin_revocations_leave_an_operational_admin(self):
        barrier = threading.Barrier(2)
        errors = []

        def revoke(assignment):
            close_old_connections()
            try:
                barrier.wait()
                assignment.revoke()
            except FinalCrmAdminProtectionError:
                errors.append("final_crm_admin")
            except Exception as error:
                errors.append(error)
            finally:
                close_old_connections()

        first_thread = threading.Thread(target=revoke, args=(self.first,))
        second_thread = threading.Thread(target=revoke, args=(self.second,))
        first_thread.start()
        second_thread.start()
        first_thread.join()
        second_thread.join()

        self.assertEqual(
            StaffRoleAssignment.objects.active().filter(role=self.role).count(),
            1,
        )
        self.assertEqual(errors, ["final_crm_admin"])
