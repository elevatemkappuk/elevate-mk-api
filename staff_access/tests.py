from django.contrib.admin.sites import AdminSite
from django.contrib.auth.models import AnonymousUser
from django.contrib.messages.storage.fallback import FallbackStorage
from django.db import IntegrityError
from django.http import HttpRequest
from django.test import RequestFactory
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import User
from staff_access.admin import StaffRoleAdmin, StaffRoleAssignmentAdmin
from staff_access.models import StaffRole, StaffRoleAssignment


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
        )
        StaffRoleAssignment.objects.assign_role(user=user, role=role)

        self.assertFalse(StaffRoleAssignment.objects.active().exists())


class StaffRoleAssignmentModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="member@example.com",
            password="testpass123",
            person_first_name="Member",
            person_last_name="User",
        )
        self.actor = User.objects.create_user(
            email="actor@example.com",
            password="testpass123",
            person_first_name="Actor",
            person_last_name="User",
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
        with self.assertRaises(IntegrityError):
            StaffRoleAssignment.objects.create(user=self.user, role=self.admin_role)

    def test_revocation_makes_assignment_inactive(self):
        assignment = StaffRoleAssignment.objects.assign_role(user=self.user, role=self.admin_role)
        assignment.revoke(revoked_by=self.actor)
        assignment.refresh_from_db()

        self.assertFalse(assignment.is_active)
        self.assertIsNotNone(assignment.revoked_at)
        self.assertEqual(assignment.revoked_by, self.actor)

    def test_reactivation_restores_same_assignment_row(self):
        assignment = StaffRoleAssignment.objects.assign_role(user=self.user, role=self.admin_role)
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
        assignment.revoke()
        self.assertFalse(StaffRoleAssignment.objects.active().exists())

    def test_assignments_to_inactive_roles_do_not_grant_access(self):
        self.viewer_role.is_active = False
        self.viewer_role.save(update_fields=["is_active"])
        StaffRoleAssignment.objects.assign_role(user=self.user, role=self.viewer_role)
        self.assertFalse(StaffRoleAssignment.objects.active().exists())


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

    def test_revoked_role_receives_403(self):
        assignment = StaffRoleAssignment.objects.assign_role(user=self.staff_user, role=self.admin_role)
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

    def test_staff_role_assignment_admin_revokes_selected_assignments(self):
        request = self.build_request()
        assignment = StaffRoleAssignment.objects.assign_role(user=self.target_user, role=self.crm_admin_role)

        self.assignment_admin.revoke_selected_assignments(request, StaffRoleAssignment.objects.filter(pk=assignment.pk))
        assignment.refresh_from_db()

        self.assertFalse(assignment.is_active)
        self.assertIsNotNone(assignment.revoked_at)
        self.assertEqual(assignment.revoked_by, self.admin_user)

    def test_staff_role_assignment_admin_reactivates_selected_assignments(self):
        request = self.build_request()
        assignment = StaffRoleAssignment.objects.assign_role(user=self.target_user, role=self.crm_admin_role)
        assignment.revoke(revoked_by=self.admin_user)

        self.assignment_admin.reactivate_selected_assignments(request, StaffRoleAssignment.objects.filter(pk=assignment.pk))
        assignment.refresh_from_db()

        self.assertTrue(assignment.is_active)
        self.assertIsNone(assignment.revoked_at)
        self.assertIsNone(assignment.revoked_by)

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
