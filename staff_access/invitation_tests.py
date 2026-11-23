from django.core.exceptions import ValidationError
from django.test import TestCase

from accounts.models import User
from people.models import Person
from staff_access.models import StaffInvitation, StaffRole, StaffRoleAssignment
from staff_access.services import create_staff_invitation, redeem_staff_invitation


class StaffInvitationTests(TestCase):
    def setUp(self):
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
