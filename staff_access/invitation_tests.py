from django.core.exceptions import ValidationError
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import User
from people.models import Person
from staff_access.models import StaffEmailChangeRequest, StaffInvitation, StaffRole, StaffRoleAssignment
from staff_access.services import (
    confirm_staff_email_change,
    create_staff_invitation,
    redeem_staff_invitation,
    request_staff_email_change,
)


class StaffInvitationTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        for code, name in StaffRole.CANONICAL_ROLES:
            StaffRole.objects.update_or_create(code=code, defaults={"name": name, "is_active": True})
        self.actor = User.objects.create_user(
            email="admin@example.com",
            password="Strong-password-123!",
            person=Person.objects.create(
                first_name="CRM", last_name="Admin", record_type=Person.RecordType.STAFF,
            ),
        )
        StaffRoleAssignment.objects.assign_role(
            user=self.actor,
            role=StaffRole.objects.get(code=StaffRole.CRM_ADMIN),
        )

    def test_invitation_stores_only_token_digest_and_redeems_once(self):
        invitation, token = create_staff_invitation(
            actor_user=self.actor,
            email="new.staff@example.com",
            first_name="New",
            last_name="Staff",
            role_codes=[StaffRole.CRM_VIEWER],
        )
        self.assertNotEqual(invitation.token_hash, token)
        user = redeem_staff_invitation(
            public_id=invitation.public_id,
            raw_token=token,
            password="Strong-password-123!",
        )
        user.refresh_from_db()
        self.assertEqual(user.person.record_type, Person.RecordType.STAFF)
        self.assertTrue(
            StaffRoleAssignment.objects.active().filter(
                user=user, role__code=StaffRole.CRM_VIEWER,
            ).exists()
        )
        with self.assertRaises(ValidationError):
            redeem_staff_invitation(
                public_id=invitation.public_id,
                raw_token=token,
                password="Another-strong-password-123!",
            )

    def test_new_invitation_supersedes_current_invitation(self):
        first, _ = create_staff_invitation(
            actor_user=self.actor,
            email="same@example.com",
            first_name="Same",
            last_name="Person",
            role_codes=[StaffRole.CRM_VIEWER],
        )
        second, _ = create_staff_invitation(
            actor_user=self.actor,
            email="same@example.com",
            first_name="Same",
            last_name="Person",
            role_codes=[StaffRole.CRM_MANAGER],
        )
        first.refresh_from_db()
        self.assertIsNotNone(first.superseded_at)
        self.assertIsNone(second.superseded_at)

    def test_invitation_rejects_passwords_that_fail_configured_validators(self):
        invitation, token = create_staff_invitation(
            actor_user=self.actor,
            email="weak-password@example.com",
            first_name="Weak",
            last_name="Password",
            role_codes=[StaffRole.CRM_VIEWER],
        )
        with self.assertRaises(ValidationError):
            redeem_staff_invitation(
                public_id=invitation.public_id,
                raw_token=token,
                password="123",
            )
        self.assertFalse(User.objects.filter(email="weak-password@example.com").exists())

    def test_staff_invitation_api_can_create_and_redeem_account(self):
        self.client.force_authenticate(self.actor)
        response = self.client.post(
            "/api/v1/staff/invitations/",
            {
                "email": "api.staff@example.com",
                "first_name": "API",
                "last_name": "Staff",
                "role_codes": [StaffRole.CRM_VIEWER],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        invitation = StaffInvitation.objects.get(public_id=response.data["public_id"])
        token = response.data["activation_token"]
        self.client.force_authenticate(None)
        redeemed = self.client.post(
            f"/api/v1/staff/invitations/{invitation.public_id}/{token}/redeem/",
            {"password": "Strong-password-123!", "confirm_password": "Strong-password-123!"},
            format="json",
        )
        self.assertEqual(redeemed.status_code, 200, redeemed.data)
        self.assertTrue(User.objects.filter(email="api.staff@example.com", person__record_type=Person.RecordType.STAFF).exists())

    def test_verified_staff_email_change_updates_person_and_revokes_credentials(self):
        target = User.objects.create_user(
            email="old.staff@example.com",
            password="Strong-password-123!",
            person=Person.objects.create(
                first_name="Old", last_name="Staff", primary_email="old.staff@example.com",
                record_type=Person.RecordType.STAFF,
            ),
        )
        previous_version = target.auth_version
        change, token = request_staff_email_change(
            actor_user=self.actor,
            user_id=target.pk,
            requested_email="new.staff@example.com",
        )
        confirm_staff_email_change(request_id=change.pk, raw_token=token)
        target.refresh_from_db()
        self.assertEqual(target.email, "new.staff@example.com")
        self.assertEqual(target.person.primary_email, "new.staff@example.com")
        self.assertGreater(target.auth_version, previous_version)
        with self.assertRaises(ValidationError):
            confirm_staff_email_change(request_id=change.pk, raw_token=token)
