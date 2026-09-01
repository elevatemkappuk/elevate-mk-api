# Generated manually for recognized legacy Person demographic labels.

import re

from django.db import migrations


def demographic_key(value):
    value = str(value).strip().casefold()
    value = value.replace("\u2013", "-").replace("\u2014", "-")
    value = value.replace("\u00e2\u20ac\u201c", "-")
    value = re.sub(r"\s*-\s*", "-", value)
    return re.sub(r"\s+", " ", value)


def normalize_recognized_person_demographics(apps, schema_editor):
    Person = apps.get_model("people", "Person")
    age_ranges = {
        "under 25": "UNDER_25",
        "under_25": "UNDER_25",
        "25-29": "25_29",
        "25_29": "25_29",
        "30-34": "30_34",
        "30_34": "30_34",
        "35-39": "35_39",
        "35_39": "35_39",
        "40-45": "40_45",
        "40_45": "40_45",
        "over 45": "OVER_45",
        "over_45": "OVER_45",
    }
    genders = {
        "male": "MALE",
        "female": "FEMALE",
        "non-binary": "NON_BINARY",
        "non binary": "NON_BINARY",
        "non_binary": "NON_BINARY",
        "transgender": "TRANSGENDER",
        "other": "OTHER",
    }

    for person in Person.objects.only("id", "age_range", "gender").iterator():
        updates = {}
        if person.age_range:
            age_range = age_ranges.get(demographic_key(person.age_range))
            if age_range and age_range != person.age_range:
                updates["age_range"] = age_range
        if person.gender:
            gender = genders.get(demographic_key(person.gender))
            if gender and gender != person.gender:
                updates["gender"] = gender
        if updates:
            Person.objects.filter(pk=person.pk).update(**updates)


class Migration(migrations.Migration):

    dependencies = [
        ("people", "0003_alter_person_age_range_alter_person_gender"),
    ]

    operations = [
        migrations.RunPython(normalize_recognized_person_demographics, migrations.RunPython.noop),
    ]
