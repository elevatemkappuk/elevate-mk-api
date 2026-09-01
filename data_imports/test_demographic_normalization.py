from django.test import SimpleTestCase

from data_imports.services.normalization import normalize_membership_form_row
from people.models import Person


class MembershipFormDemographicNormalizationTests(SimpleTestCase):
    headers = {
        "Timestamp": "Timestamp",
        "First Name ": "First Name ",
        "Last Name ": "Last Name ",
        "Gender": "Gender",
        "Age ": "Age ",
        "Email (preferably your personal email)": "Email (preferably your personal email)",
        "Mobile Number": "Mobile Number",
        "Location": "Location",
        "What industry do you work in?": "What industry do you work in?",
        "What is your current role or job title?": "What is your current role or job title?",
        "Linkedin URL": "Linkedin URL",
    }

    def normalize(self, age_range, gender):
        return normalize_membership_form_row(
            {"Age ": age_range, "Gender": gender},
            self.headers,
        )

    def test_all_form_age_labels_and_variants_normalize_to_person_choices(self):
        cases = {
            "Under 25": Person.AgeRange.UNDER_25,
            "under 25": Person.AgeRange.UNDER_25,
            "25 - 29": Person.AgeRange.AGE_25_29,
            "25-29": Person.AgeRange.AGE_25_29,
            "25 \u00e2\u20ac\u201c 29": Person.AgeRange.AGE_25_29,
            "30 - 34": Person.AgeRange.AGE_30_34,
            "35-39": Person.AgeRange.AGE_35_39,
            "40 - 45": Person.AgeRange.AGE_40_45,
            "Over 45": Person.AgeRange.OVER_45,
            "over 45": Person.AgeRange.OVER_45,
        }

        for source_value, expected_value in cases.items():
            with self.subTest(source_value=source_value):
                normalized, errors = self.normalize(source_value, "Female")
                self.assertEqual(normalized["age_range"], expected_value)
                self.assertEqual(errors, [])

    def test_all_form_gender_labels_and_variants_normalize_to_person_choices(self):
        cases = {
            "Male": Person.Gender.MALE,
            "male": Person.Gender.MALE,
            "Female": Person.Gender.FEMALE,
            "female": Person.Gender.FEMALE,
            "Non-Binary": Person.Gender.NON_BINARY,
            "Non Binary": Person.Gender.NON_BINARY,
            "non-binary": Person.Gender.NON_BINARY,
            "non binary": Person.Gender.NON_BINARY,
            "Transgender": Person.Gender.TRANSGENDER,
            "transgender": Person.Gender.TRANSGENDER,
            "Other": Person.Gender.OTHER,
            "other": Person.Gender.OTHER,
        }

        for source_value, expected_value in cases.items():
            with self.subTest(source_value=source_value):
                normalized, errors = self.normalize("Under 25", source_value)
                self.assertEqual(normalized["gender"], expected_value)
                self.assertEqual(errors, [])
