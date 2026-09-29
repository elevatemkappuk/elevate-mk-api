from django.test import SimpleTestCase

from community.normalization import normalize_community_name
from people.services import PhoneNormalizationStatus, normalize_phone_for_community


class CommunityPhoneNormalizationTests(SimpleTestCase):
    def assert_normalized(self, value, region, expected):
        result = normalize_phone_for_community(value, region=region)
        self.assertEqual(result.status, PhoneNormalizationStatus.NORMALIZED)
        self.assertEqual(result.e164, expected)

    def test_national_numbers_canonicalize_for_selected_regions(self):
        self.assert_normalized("07123 456 789", "GB", "+447123456789")
        self.assert_normalized("024 123 4567", "GH", "+233241234567")
        self.assert_normalized("4155552671", "US", "+14155552671")

    def test_explicit_and_zero_zero_international_numbers_require_compatible_region(self):
        self.assert_normalized("+447123456789", "GB", "+447123456789")
        self.assert_normalized("00233 24 123 4567", "GH", "+233241234567")
        mismatch = normalize_phone_for_community("+233241234567", region="GB")
        self.assertEqual(mismatch.reason, "REGION_MISMATCH")
        zero_zero_mismatch = normalize_phone_for_community("00233 24 123 4567", region="GB")
        self.assertEqual(zero_zero_mismatch.reason, "REGION_MISMATCH")

    def test_invalid_region_and_phone_values_are_rejected(self):
        self.assertEqual(
            normalize_phone_for_community("07123456789", region="ZZ").reason,
            "INVALID_REGION",
        )
        self.assertEqual(
            normalize_phone_for_community("07123", region="GB").reason,
            "INVALID_NUMBER",
        )
        self.assertEqual(
            normalize_phone_for_community("02079460018", region="GB").reason,
            "UNSUPPORTED_NUMBER_TYPE",
        )


class CommunityNameNormalizationTests(SimpleTestCase):
    def test_obvious_case_is_formatted_without_pascal_case(self):
        cases = {
            "john": "John",
            "SMITH": "Smith",
            "mary jane": "Mary Jane",
            "o'connor": "O'Connor",
            "smith-jones": "Smith-Jones",
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(normalize_community_name(value), expected)

    def test_meaningful_mixed_case_is_preserved(self):
        for value in ("McDonald", "MacDonald", "de Souza", "van der Berg"):
            with self.subTest(value=value):
                self.assertEqual(normalize_community_name(value), value)
