from django.test import SimpleTestCase

from people.services import is_plausible_crm_mobile


class CrmMobilePlausibilityTests(SimpleTestCase):
    def test_blank_and_reasonable_international_or_national_values_are_accepted(self):
        for value in (
            "",
            "+447911123456",
            "+44 7911 123456",
            "00447911123456",
            "07911123456",
            "07911 123 456",
            "+44 (0) 7911-123-456",
        ):
            with self.subTest(value=value):
                self.assertTrue(is_plausible_crm_mobile(value))

    def test_obvious_garbage_and_malformed_structure_are_rejected(self):
        for value in (
            "abc",
            "hello123",
            "123",
            "12 34",
            "44+7911123456",
            "++447911123456",
            "+44--7911123456",
            "(07911123456",
            "07911123456-",
        ):
            with self.subTest(value=value):
                self.assertFalse(is_plausible_crm_mobile(value))
