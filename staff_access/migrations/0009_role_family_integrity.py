from django.db import migrations, models
from django.db.models import Q


CRM_ROLE_CODES = ("CRM_ADMIN", "CRM_MANAGER", "CRM_VIEWER")


def populate_crm_role_family(apps, schema_editor):
    StaffRole = apps.get_model("staff_access", "StaffRole")
    StaffRoleAssignment = apps.get_model("staff_access", "StaffRoleAssignment")

    StaffRole.objects.filter(code__in=CRM_ROLE_CODES).update(family="CRM")
    StaffRoleAssignment.objects.filter(role__code__in=CRM_ROLE_CODES).update(role_family="CRM")


def clear_crm_role_family(apps, schema_editor):
    StaffRoleAssignment = apps.get_model("staff_access", "StaffRoleAssignment")
    StaffRole = apps.get_model("staff_access", "StaffRole")

    StaffRoleAssignment.objects.filter(role__code__in=CRM_ROLE_CODES).update(role_family=None)
    StaffRole.objects.filter(code__in=CRM_ROLE_CODES).update(family=None)


class Migration(migrations.Migration):
    dependencies = [
        ("staff_access", "0008_legal_hold_admin"),
    ]

    operations = [
        migrations.AddField(
            model_name="staffrole",
            name="family",
            field=models.CharField(blank=True, max_length=100, null=True),
        ),
        migrations.AddField(
            model_name="staffroleassignment",
            name="role_family",
            field=models.CharField(blank=True, max_length=100, null=True),
        ),
        migrations.RunPython(populate_crm_role_family, clear_crm_role_family),
        migrations.AddConstraint(
            model_name="staffroleassignment",
            constraint=models.UniqueConstraint(
                condition=Q(
                    is_active=True,
                    revoked_at__isnull=True,
                    role_family__isnull=False,
                ),
                fields=("user", "role_family"),
                name="staff_access_one_active_role_family",
            ),
        ),
    ]
