from django.test import TestCase

from people.models import Person


class PersonModelTests(TestCase):
    def test_person_can_exist_without_user(self):
        person = Person.objects.create(first_name="Taylor", last_name="Jordan")

        self.assertEqual(person.first_name, "Taylor")
        self.assertFalse(hasattr(person, "user"))
