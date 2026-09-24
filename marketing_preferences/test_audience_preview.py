from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from interests.models import Interest, PersonInterest
from marketing_preferences.models import MarketingPreference, MarketingPreferenceHistory, MarketingWebhookReceipt
from memberships.models import Membership
from people.models import Person
from professional_profiles.models import Industry, ProfessionalProfile
from skills.models import PersonSkill, Skill
from staff_access.models import StaffRole, StaffRoleAssignment
from tags.models import PersonTag, Tag
from accounts.models import User
from external_references.models import ExternalPersonReference, ExternalPersonSyncJob


class AudiencePreviewApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_user(
            email="audience-admin@example.com",
            password="password123",
            person_first_name="Audience",
            person_last_name="Admin",
            person_record_type=Person.RecordType.TECHNICAL,
        )
        self.manager = User.objects.create_user(
            email="audience-manager@example.com",
            password="password123",
            person_first_name="Audience",
            person_last_name="Manager",
            person_record_type=Person.RecordType.TECHNICAL,
        )
        self.viewer = User.objects.create_user(
            email="audience-viewer@example.com",
            password="password123",
            person_first_name="Audience",
            person_last_name="Viewer",
            person_record_type=Person.RecordType.TECHNICAL,
        )
        self.nonstaff = User.objects.create_user(
            email="audience-nonstaff@example.com",
            password="password123",
            person_first_name="Audience",
            person_last_name="Nonstaff",
            person_record_type=Person.RecordType.TECHNICAL,
        )
        for user, role in (
            (self.admin, StaffRole.CRM_ADMIN),
            (self.manager, StaffRole.CRM_MANAGER),
            (self.viewer, StaffRole.CRM_VIEWER),
        ):
            StaffRoleAssignment.objects.assign_role(user=user, role=StaffRole.objects.get(code=role))

        self.url = "/api/v1/marketing/audiences/preview/"
        self.eligible = Person.objects.create(
            first_name="Eligible", last_name="Person", primary_email="eligible@example.com", location="London",
        )
        self.unknown = Person.objects.create(
            first_name="Unknown", last_name="Person", primary_email="unknown@example.com", location="London",
        )
        self.opted_out = Person.objects.create(
            first_name="Opted", last_name="Out", primary_email="out@example.com", location="London",
        )
        self.no_email = Person.objects.create(first_name="No", last_name="Email", primary_email="")
        self.archived = Person.objects.create(
            first_name="Archived", last_name="Person", primary_email="archived@example.com",
            archived_at=timezone.now(),
        )
        Person.objects.create(
            first_name="Technical", last_name="Person", primary_email="technical@example.com",
            record_type=Person.RecordType.TECHNICAL,
        )
        self._preference(self.eligible, MarketingPreference.State.OPTED_IN)
        self._preference(self.opted_out, MarketingPreference.State.OPTED_OUT)

    def _preference(self, person, state):
        return MarketingPreference.objects.create(
            person=person,
            channel=MarketingPreference.Channel.EMAIL,
            state=state,
            source=MarketingPreference.Source.STAFF_RECORDED,
        )

    def authenticate(self, user):
        self.client.force_authenticate(user=user)

    def preview(self, payload=None):
        self.authenticate(self.admin)
        return self.client.post(self.url, payload or {}, format="json")

    def test_counts_use_active_business_population_and_no_email_has_precedence(self):
        response = self.preview()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["selected_count"], 4)
        self.assertEqual(response.data["eligible_count"], 1)
        self.assertEqual(response.data["excluded_count"], 3)
        self.assertEqual(response.data["exclusion_counts"], {
            "EXCLUDED_OPTED_OUT": 1,
            "EXCLUDED_CONSENT_UNKNOWN": 1,
            "EXCLUDED_NO_EMAIL": 1,
        })
        rows = {row["id"]: row for row in response.data["results"]["results"]}
        self.assertEqual(rows[self.eligible.id]["classification"], "ELIGIBLE")
        self.assertEqual(rows[self.opted_out.id]["exclusion_reasons"], ["EXCLUDED_OPTED_OUT"])
        self.assertEqual(rows[self.unknown.id]["exclusion_reasons"], ["EXCLUDED_CONSENT_UNKNOWN"])
        self.assertEqual(rows[self.no_email.id]["exclusion_reasons"], ["EXCLUDED_NO_EMAIL"])
        self.assertNotIn(self.archived.id, rows)

    def test_result_views_and_pages_keep_aggregate_counts_stable(self):
        all_response = self.preview({"page_size": 25, "result": "all"})
        eligible_response = self.preview({"page_size": 25, "result": "eligible"})
        excluded_response = self.preview({"page_size": 25, "result": "excluded"})
        page_response = self.preview({"page_size": 25, "page": 2, "result": "all"})

        for response in (eligible_response, excluded_response, page_response):
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data["selected_count"], all_response.data["selected_count"])
            self.assertEqual(response.data["eligible_count"], all_response.data["eligible_count"])
            self.assertEqual(response.data["excluded_count"], all_response.data["excluded_count"])
        self.assertEqual(eligible_response.data["results"]["count"], 1)
        self.assertEqual(excluded_response.data["results"]["count"], 3)

    def test_multiple_people_per_classification_are_counted_and_aligned(self):
        for index in range(3):
            person = Person.objects.create(
                first_name="Eligible", last_name=f"Additional {index}",
                primary_email=f"eligible-{index}@example.com",
            )
            self._preference(person, MarketingPreference.State.OPTED_IN)
        for index in range(2):
            person = Person.objects.create(
                first_name="Opted", last_name=f"Out {index}",
                primary_email=f"out-{index}@example.com",
            )
            self._preference(person, MarketingPreference.State.OPTED_OUT)
        for index in range(2):
            Person.objects.create(
                first_name="Unknown", last_name=f"Additional {index}",
                primary_email=f"unknown-{index}@example.com",
            )

        all_response = self.preview({"result": "all", "page_size": 25, "page": 1})
        eligible_response = self.preview({"result": "eligible", "page_size": 25})
        excluded_response = self.preview({"result": "excluded", "page_size": 25})

        self.assertEqual(all_response.status_code, 200)
        self.assertEqual(all_response.data["selected_count"], 11)
        self.assertEqual(all_response.data["eligible_count"], 4)
        self.assertEqual(all_response.data["excluded_count"], 7)
        self.assertEqual(all_response.data["selected_count"], all_response.data["eligible_count"] + all_response.data["excluded_count"])
        self.assertEqual(sum(all_response.data["exclusion_counts"].values()), all_response.data["excluded_count"])
        self.assertEqual(all_response.data["results"]["count"], all_response.data["selected_count"])
        self.assertEqual(eligible_response.data["results"]["count"], eligible_response.data["eligible_count"])
        self.assertEqual(excluded_response.data["results"]["count"], excluded_response.data["excluded_count"])

    def test_aggregate_counts_are_stable_across_pages_sizes_and_filters(self):
        for index in range(30):
            person = Person.objects.create(
                first_name="Paged", last_name=f"Person {index:02d}",
                primary_email=f"paged-{index}@example.com", location="Bristol",
            )
            self._preference(person, MarketingPreference.State.OPTED_IN)

        page_one = self.preview({"page": 1, "page_size": 25})
        page_two = self.preview({"page": 2, "page_size": 25})
        larger_page = self.preview({"page": 1, "page_size": 50})
        filtered = self.preview({"selection": {"location": ["bristol"]}, "page_size": 25})

        self.assertEqual(page_one.data["results"]["count"], page_one.data["selected_count"])
        self.assertEqual(page_two.data["results"]["count"], page_one.data["selected_count"])
        self.assertEqual(larger_page.data["results"]["count"], larger_page.data["selected_count"])
        for response in (page_two, larger_page):
            self.assertEqual(response.data["selected_count"], page_one.data["selected_count"])
            self.assertEqual(response.data["eligible_count"], page_one.data["eligible_count"])
            self.assertEqual(response.data["excluded_count"], page_one.data["excluded_count"])
        self.assertEqual(filtered.data["results"]["count"], filtered.data["selected_count"])
        self.assertEqual(filtered.data["eligible_count"], filtered.data["selected_count"])

    def test_existing_people_selection_filters_are_reused(self):
        industry = Industry.objects.create(name="Technology", slug="audience-technology")
        interest = Interest.objects.create(name="Mentoring", slug="audience-mentoring")
        skill = Skill.objects.create(name="Python", slug="audience-python")
        tag = Tag.objects.create(name="Priority", slug="audience-priority")
        Membership.objects.create(
            person=self.eligible,
            status=Membership.Status.ACTIVE,
            joined_at="2026-01-01",
            membership_source=Membership.Source.STAFF,
        )
        ProfessionalProfile.objects.create(
            person=self.eligible,
            industry=industry,
            career_stage=ProfessionalProfile.CareerStage.SENIOR,
            job_title="Engineer",
        )
        PersonInterest.objects.create(person=self.eligible, interest=interest)
        PersonSkill.objects.create(person=self.eligible, skill=skill)
        PersonTag.objects.create(person=self.eligible, tag=tag, assigned_by=self.admin)

        response = self.preview({
            "selection": {
                "q": "engineer",
                "relationship": ["ACTIVE_MEMBER"],
                "location": ["london"],
                "industry": [industry.id],
                "career_stage": ["SENIOR"],
                "interest": [interest.id],
                "skill": [skill.id],
                "tag": [tag.id],
            },
        })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["selected_count"], 1)
        self.assertEqual(response.data["results"]["results"][0]["id"], self.eligible.id)

    def test_authorization_matches_people_read_access(self):
        self.assertEqual(self.client.post(self.url, {}, format="json").status_code, 401)
        self.authenticate(self.nonstaff)
        self.assertEqual(self.client.post(self.url, {}, format="json").status_code, 403)
        for user in (self.admin, self.manager, self.viewer):
            self.authenticate(user)
            self.assertEqual(self.client.post(self.url, {}, format="json").status_code, 200)

    def test_validation_rejects_non_active_record_state_unsupported_filter_and_bad_result(self):
        self.authenticate(self.admin)
        for payload in (
            {"selection": {"record_state": "all"}},
            {"selection": {"consent": "OPTED_IN"}},
            {"result": "unknown"},
            {"page_size": 10},
        ):
            self.assertEqual(self.client.post(self.url, payload, format="json").status_code, 400)

    @patch("brevo_marketing.client.BrevoMarketingClient")
    @patch("mailchimp.client.MailchimpMarketingClient")
    def test_preview_has_no_provider_or_persistence_side_effects(self, mailchimp_client, brevo_client):
        self.authenticate(self.admin)
        before = {
            "jobs": ExternalPersonSyncJob.objects.count(),
            "references": ExternalPersonReference.objects.count(),
            "history": MarketingPreferenceHistory.objects.count(),
            "receipts": MarketingWebhookReceipt.objects.count(),
            "people": Person.objects.count(),
            "preferences": MarketingPreference.objects.count(),
        }

        response = self.client.post(self.url, {}, format="json")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(ExternalPersonSyncJob.objects.count(), before["jobs"])
        self.assertEqual(ExternalPersonReference.objects.count(), before["references"])
        self.assertEqual(MarketingWebhookReceipt.objects.count(), before["receipts"])
        self.assertEqual(MarketingPreferenceHistory.objects.count(), before["history"])
        self.assertEqual(Person.objects.count(), before["people"])
        self.assertEqual(MarketingPreference.objects.count(), before["preferences"])
        brevo_client.assert_not_called()
        mailchimp_client.assert_not_called()
