from django.db import migrations


CANONICAL_ROLES = (
    ("CRM_ADMIN", "CRM Administrator"),
    ("CRM_MANAGER", "CRM Manager"),
    ("CRM_VIEWER", "CRM Viewer"),
)


def seed_canonical_roles(apps, schema_editor):
    StaffRole = apps.get_model("staff_access", "StaffRole")

    for code, name in CANONICAL_ROLES:
        StaffRole.objects.update_or_create(
            code=code,
            defaults={"name": name, "is_active": True},
        )


def unseed_canonical_roles(apps, schema_editor):
    StaffRole = apps.get_model("staff_access", "StaffRole")
    StaffRole.objects.filter(code__in=[code for code, _name in CANONICAL_ROLES]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("staff_access", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_canonical_roles, unseed_canonical_roles),
    ]
