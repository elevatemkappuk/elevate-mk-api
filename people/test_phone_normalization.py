import unittest

from people.services import PhoneNormalizationStatus, normalize_phone_for_provider


class ProviderPhoneNormalizationTests(unittest.TestCase):
    def assert_normalized(self, value, expected, *, region=None):
        result = normalize_phone_for_provider(value, region=region)
        self.assertEqual(result.status, PhoneNormalizationStatus.NORMALIZED)
        self.assertEqual(result.e164, expected)
        self.assertIsNone(result.reason)

    def test_empty_and_whitespace_are_distinct_from_invalid(self):
        for value in (None, "", "   "):
            result = normalize_phone_for_provider(value)
            self.assertEqual(result.status, PhoneNormalizationStatus.EMPTY)
            self.assertIsNone(result.e164)

    def test_explicit_international_numbers_normalize_to_e164(self):
        self.assert_normalized("+447911123456", "+447911123456")
        self.assert_normalized("+44 7911 123456", "+447911123456")
        self.assert_normalized("00447911123456", "+447911123456")
        self.assert_normalized("00 44 7911 123456", "+447911123456")
        self.assert_normalized("+14155552671", "+14155552671")
        self.assert_normalized("+33612345678", "+33612345678")

    def test_previously_used_controlled_test_number_follows_pinned_metadata(self):
        result = normalize_phone_for_provider("+447911123456")

        self.assertEqual(result.status, PhoneNormalizationStatus.NORMALIZED)
        self.assertEqual(result.e164, "+447911123456")

    def test_national_numbers_require_explicit_region(self):
        ambiguous = normalize_phone_for_provider("07911 123456")
        self.assertEqual(ambiguous.status, PhoneNormalizationStatus.AMBIGUOUS)
        self.assertEqual(ambiguous.reason, "NO_RELIABLE_REGION")
        self.assert_normalized("07911 123456", "+447911123456", region="GB")
        self.assert_normalized("4155552671", "+14155552671", region="US")

    def test_invalid_and_unsupported_values_are_not_sent(self):
        for value, reason in (
            ("+44 7911", "INVALID_NUMBER"),
            ("+4479111234567890", "IMPOSSIBLE_NUMBER"),
            ("abc", "ALPHABETIC_INPUT"),
            ("+447911123456 ext 2", "UNSUPPORTED_EXTENSION"),
            ("+442079460018", "UNSUPPORTED_NUMBER_TYPE"),
        ):
            result = normalize_phone_for_provider(value)
            self.assertEqual(result.status, PhoneNormalizationStatus.INVALID)
            self.assertEqual(result.reason, reason)
            self.assertIsNone(result.e164)

    def test_fixed_line_or_mobile_is_accepted(self):
        self.assert_normalized("+12025550143", "+12025550143")

    def test_normalization_is_idempotent(self):
        first = normalize_phone_for_provider("00447911123456")
        second = normalize_phone_for_provider(first.e164)
        self.assertEqual(second, first)
