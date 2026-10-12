from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from audit.models import AuditEvent
from community.models import CommunityContentReport, CommunityModerationAction, CommunityPost, CommunityPostReply, CommunityProfile
from memberships.models import Membership
from people.models import Person
from staff_access.models import StaffRole, StaffRoleAssignment


class CommunityContentModerationApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.reporter = self.member("Reporter", "One", "reporter@example.com")
        self.author = self.member("Author", "Two", "author@example.com")
        self.post = CommunityPost.objects.create(
            author=self.author.person, purpose=CommunityPost.Purpose.ASK,
            headline="A useful question", body="A useful post body.", audience=CommunityPost.Audience.ELEVATE_COMMUNITY,
        )
        self.client.force_authenticate(self.reporter)

    def member(self, first_name, last_name, email):
        person = Person.objects.create(first_name=first_name, last_name=last_name, primary_email=email)
        Membership.objects.create(person=person, status=Membership.Status.ACTIVE, joined_at=timezone.localdate() - timedelta(days=1), membership_source=Membership.Source.COMMUNITY_PLATFORM)
        user = User.objects.create_user(email=email, password="Strong-password-123!", person=person)
        CommunityProfile.objects.create(person=person)
        return user

    def report_url(self):
        return f"/api/v1/community/posts/{self.post.public_id}/report/"

    def test_member_report_is_safe_and_idempotent(self):
        payload = {"reason": "SPAM_OR_EXCESSIVE_PROMOTION", "details": "Repeated promotion."}
        first = self.client.post(self.report_url(), payload, format="json")
        second = self.client.post(self.report_url(), {"reason": "OTHER", "details": "Different wording."}, format="json")
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.data, second.data)
        report = CommunityContentReport.objects.get()
        self.assertEqual(report.details, payload["details"])
        self.assertFalse(AuditEvent.objects.filter(action=AuditEvent.Action.COMMUNITY_CONTENT_REPORTED).count() > 1)
        self.assertNotIn("details", first.data)

    def test_member_cannot_report_own_content_or_ineligible_target(self):
        self.client.force_authenticate(self.author)
        own = self.client.post(f"/api/v1/community/posts/{self.post.public_id}/report/", {"reason": "OTHER"}, format="json")
        self.assertEqual(own.status_code, 404)
        self.post.status = CommunityPost.Status.MODERATOR_REMOVED
        self.post.removed_at = timezone.now()
        self.post.save(update_fields=["status", "removed_at", "updated_at"])
        self.client.force_authenticate(self.reporter)
        unavailable = self.client.post(self.report_url(), {"reason": "OTHER"}, format="json")
        self.assertEqual(unavailable.status_code, 404)

    def test_viewer_cannot_access_moderation_but_manager_can_remove_and_restore(self):
        viewer = self.member("Viewer", "Staff", "viewer@example.com")
        manager = self.member("Manager", "Staff", "manager@example.com")
        viewer_role, _ = StaffRole.objects.get_or_create(code=StaffRole.CRM_VIEWER, defaults={"name": "CRM Viewer"})
        manager_role, _ = StaffRole.objects.get_or_create(code=StaffRole.CRM_MANAGER, defaults={"name": "CRM Manager"})
        StaffRoleAssignment.objects.create(user=viewer, role=viewer_role)
        StaffRoleAssignment.objects.create(user=manager, role=manager_role)
        self.client.force_authenticate(self.reporter)
        response = self.client.post(self.report_url(), {"reason": "OTHER"}, format="json")
        report_id = response.data["report_id"]
        self.client.force_authenticate(viewer)
        self.assertEqual(self.client.get("/api/v1/community/moderation/reports/").status_code, 403)
        self.client.force_authenticate(manager)
        queue = self.client.get("/api/v1/community/moderation/reports/")
        self.assertEqual(queue.status_code, 200)
        self.assertEqual(queue.data["results"][0]["report_id"], report_id)
        removed = self.client.post(f"/api/v1/community/moderation/reports/{report_id}/remove/", {"resolution": "Removed."}, format="json")
        self.assertEqual(removed.status_code, 200)
        self.post.refresh_from_db()
        self.assertEqual(self.post.status, CommunityPost.Status.MODERATOR_REMOVED)
        self.assertEqual(CommunityContentReport.objects.get().status, CommunityContentReport.Status.RESOLVED)
        self.assertTrue(CommunityModerationAction.objects.filter(action=CommunityModerationAction.Action.CONTENT_REMOVED).exists())
        restored = self.client.post(f"/api/v1/community/moderation/reports/{report_id}/restore/", {}, format="json")
        self.assertEqual(restored.status_code, 200)
        self.post.refresh_from_db()
        self.assertEqual(self.post.status, CommunityPost.Status.ACTIVE)

    def test_reply_report_requires_parent_and_staff_projection_has_parent_context(self):
        reply = CommunityPostReply.objects.create(post=self.post, author=self.author.person, body="A useful reply.")
        response = self.client.post(
            f"/api/v1/community/posts/{self.post.public_id}/replies/{reply.public_id}/report/",
            {"reason": "INAPPROPRIATE_OR_ABUSIVE"}, format="json",
        )
        self.assertEqual(response.status_code, 201)
        manager = self.member("Manager", "Staff", "reply-manager@example.com")
        role, _ = StaffRole.objects.get_or_create(code=StaffRole.CRM_MANAGER, defaults={"name": "CRM Manager"})
        StaffRoleAssignment.objects.create(user=manager, role=role)
        self.client.force_authenticate(manager)
        detail = self.client.get(f"/api/v1/community/moderation/reports/{response.data['report_id']}/")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.data["target"]["type"], "REPLY")
        self.assertEqual(str(detail.data["target"]["parent_post"]["public_id"]), str(self.post.public_id))
