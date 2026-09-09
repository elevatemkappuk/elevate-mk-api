from datetime import date, datetime, timezone as dt_timezone
from unittest.mock import patch

from django.utils import timezone
from rest_framework.test import APITestCase

from accounts.models import User
from data_imports.models import ImportBatch
from memberships.models import Membership
from people.models import Person
from professional_profiles.models import Industry, ProfessionalProfile
from staff_access.models import StaffRole, StaffRoleAssignment
from .queries import dashboard_projection


class DashboardTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="dashboard@example.com", password="safe-password", person_first_name="Staff", person_last_name="User")
        self.roles = {code: StaffRole.objects.get_or_create(code=code, defaults={"name": name})[0] for code, name in StaffRole.CANONICAL_ROLES}

    def person(self, **kwargs):
        return Person.objects.create(first_name="Dashboard", last_name="Person", **kwargs)

    def member(self, person, joined=date(2026, 1, 12), status=Membership.Status.ACTIVE):
        return Membership.objects.create(person=person, joined_at=joined, status=status,
            ended_at=date(2026, 2, 1) if status == Membership.Status.FORMER else None, membership_source=Membership.Source.STAFF)

    def projection(self):
        with patch("dashboard.queries.timezone.localdate", return_value=date(2026, 3, 15)):
            return dashboard_projection()

    def test_authorization_and_read_only_contract(self):
        self.assertEqual(self.client.get("/api/v1/dashboard/").status_code, 401)
        self.client.force_authenticate(self.user)
        self.assertEqual(self.client.get("/api/v1/dashboard/").status_code, 403)
        self.user.is_staff = self.user.is_superuser = True
        self.user.save()
        self.assertEqual(self.client.get("/api/v1/dashboard/").status_code, 403)
        for role in self.roles.values():
            assignment = StaffRoleAssignment.objects.assign_role(user=self.user, role=role)
            response = self.client.get("/api/v1/dashboard/")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(set(response.data), {"overview", "growth", "community_profile", "attention"})
            self.assertEqual(self.client.post("/api/v1/dashboard/", {}).status_code, 405)
            StaffRoleAssignment.objects.filter(pk=assignment.pk).update(is_active=False)
            self.assertEqual(self.client.get("/api/v1/dashboard/").status_code, 403)

    def test_overview_and_attention_domain_boundaries(self):
        self.person()
        self.member(self.person())
        self.member(self.person(), status=Membership.Status.FORMER)
        self.member(self.person(archived_at=timezone.now()))
        self.member(self.person(record_type=Person.RecordType.TECHNICAL))
        self.person(record_type=Person.RecordType.TECHNICAL, archived_at=timezone.now())
        for status in ImportBatch.Status.values:
            ImportBatch.objects.create(source_type="MEMBERSHIP_FORM", source_filename="test.xlsx", source_fingerprint=status, status=status)
        result = self.projection()
        self.assertEqual(result["overview"], {"total_people": 3, "active_members": 1, "contacts": 1, "former_members": 1})
        self.assertEqual(result["attention"], {"imports_needing_review": 1, "archived_people": 1})

    def test_six_month_window_zeros_and_distinct_authoritative_dates(self):
        person = self.person()
        Person.objects.filter(pk=person.pk).update(created_at=datetime(2026, 3, 1, tzinfo=dt_timezone.utc))
        membership = self.member(person, joined=date(2025, 10, 2))
        Membership.objects.filter(pk=membership.pk).update(created_at=datetime(2026, 3, 1, tzinfo=dt_timezone.utc))
        self.member(self.person(archived_at=timezone.now()), joined=date(2026, 1, 12), status=Membership.Status.FORMER)
        self.member(self.person(record_type="TECHNICAL"), joined=date(2025, 10, 2))
        self.member(self.person(), joined=date(2025, 9, 30))
        self.member(self.person(), joined=date(2026, 4, 1))
        Person.objects.exclude(pk=person.pk).update(created_at=datetime(2020, 1, 1, tzinfo=dt_timezone.utc))
        result = self.projection()["growth"]
        months = ["2025-10", "2025-11", "2025-12", "2026-01", "2026-02", "2026-03"]
        for series in result.values():
            self.assertEqual([row["month"] for row in series], months)
        self.assertEqual([row["count"] for row in result["people_by_month"]], [0, 0, 0, 0, 0, 1])
        self.assertEqual([row["count"] for row in result["members_by_month"]], [1, 0, 0, 1, 0, 0])

    def test_people_growth_excludes_archived_and_technical_and_respects_timezone(self):
        for kwargs in ({}, {"archived_at": timezone.now()}, {"record_type": "TECHNICAL"}):
            person = self.person(**kwargs)
            Person.objects.filter(pk=person.pk).update(created_at=datetime(2026, 3, 1, 0, 30, tzinfo=dt_timezone.utc))
        with timezone.override("America/New_York"):
            result = self.projection()["growth"]["people_by_month"]
        self.assertEqual([row["count"] for row in result], [0, 0, 0, 0, 1, 0])

    def test_locations_top_five_stored_values_and_deterministic_ties(self):
        for label in ["", "  ", "\t\n", "Zulu", "Alpha", "Beta", "Delta", "Gamma", "Epsilon", "Zulu", " MK "]:
            self.person(location=label)
        self.person(location="Excluded", archived_at=timezone.now())
        self.person(location="Excluded", record_type="TECHNICAL")
        self.assertEqual(self.projection()["community_profile"]["top_locations"], [
            {"label": "Zulu", "count": 2}, {"label": " MK ", "count": 1}, {"label": "Alpha", "count": 1},
            {"label": "Beta", "count": 1}, {"label": "Delta", "count": 1},
        ])

    def test_industries_and_all_canonical_age_ranges(self):
        industries = [Industry.objects.create(name=name, slug=name.lower()) for name in ["Technology", "Arts", "Business", "Design", "Education", "Finance"]]
        for industry in industries:
            ProfessionalProfile.objects.create(person=self.person(age_range=Person.AgeRange.UNDER_25), industry=industry)
        ProfessionalProfile.objects.create(person=self.person(), industry=industries[0])
        ProfessionalProfile.objects.create(person=self.person())
        ProfessionalProfile.objects.create(person=self.person(record_type="TECHNICAL", age_range=Person.AgeRange.UNDER_25), industry=industries[0])
        ProfessionalProfile.objects.create(person=self.person(archived_at=timezone.now(), age_range=Person.AgeRange.UNDER_25), industry=industries[0])
        profile = self.projection()["community_profile"]
        self.assertEqual([row["label"] for row in profile["top_industries"]], ["Technology", "Arts", "Business", "Design", "Education"])
        self.assertEqual(profile["top_industries"][0], {"id": industries[0].pk, "label": "Technology", "count": 2})
        self.assertEqual(profile["age_ranges"], [{"value": value, "label": label, "count": 6 if value == Person.AgeRange.UNDER_25 else 0} for value, label in Person.AgeRange.choices])

    def test_empty_projection_is_zero_filled_with_bounded_queries(self):
        # The user account's TECHNICAL Person does not enter the projection.
        with self.assertNumQueries(8):
            result = self.projection()
        self.assertEqual(result["overview"]["total_people"], 0)
        self.assertTrue(all(row["count"] == 0 for series in result["growth"].values() for row in series))
        self.assertEqual(result["community_profile"]["top_locations"], [])
