from django.test import TestCase

from audit.models import AuditEvent
from external_references.models import ExternalPersonReference
from external_references.services import attach_person_reference, revoke_person_reference
from people.models import Person


class ExternalPersonReferenceServiceTests(TestCase):
    def setUp(self):
        self.person = Person.objects.create(first_name="Ava", last_name="Example", primary_email="ava@example.com")

    def test_links_business_person_and_is_idempotent(self):
        first = attach_person_reference(
            person=self.person,
            provider="mailchimp",
            reference_type=ExternalPersonReference.ReferenceType.MARKETING_CONTACT,
            external_id="mc-123",
        )
        second = attach_person_reference(
            person=self.person,
            provider="MAILCHIMP",
            reference_type=ExternalPersonReference.ReferenceType.MARKETING_CONTACT,
            external_id="mc-123",
        )

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(ExternalPersonReference.objects.count(), 1)
        self.assertEqual(AuditEvent.objects.filter(action=AuditEvent.Action.EXTERNAL_PERSON_REFERENCE_LINKED).count(), 1)

    def test_non_business_person_is_rejected_without_audit(self):
        technical = Person.objects.create(record_type=Person.RecordType.TECHNICAL, first_name="Tech", last_name="User")
        with self.assertRaises(ValueError):
            attach_person_reference(
                person=technical,
                provider="MAILCHIMP",
                reference_type=ExternalPersonReference.ReferenceType.MARKETING_CONTACT,
                external_id="mc-456",
            )
        self.assertFalse(ExternalPersonReference.objects.exists())
        self.assertFalse(AuditEvent.objects.filter(entity_type="ExternalPersonReference").exists())

    def test_revoke_retains_identity_and_reactivation_is_audited(self):
        reference = attach_person_reference(
            person=self.person,
            provider="MAILCHIMP",
            reference_type=ExternalPersonReference.ReferenceType.MARKETING_CONTACT,
            external_id="mc-789",
        )
        revoke_person_reference(reference=reference)
        reference.refresh_from_db()
        self.assertEqual(reference.status, ExternalPersonReference.Status.REVOKED)
        self.assertIsNotNone(reference.revoked_at)

        attach_person_reference(
            person=self.person,
            provider="MAILCHIMP",
            reference_type=ExternalPersonReference.ReferenceType.MARKETING_CONTACT,
            external_id="mc-789",
        )
        reference.refresh_from_db()
        self.assertEqual(reference.status, ExternalPersonReference.Status.ACTIVE)
        self.assertEqual(AuditEvent.objects.filter(action=AuditEvent.Action.EXTERNAL_PERSON_REFERENCE_REACTIVATED).count(), 1)
