from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone
from django.conf import settings


CAPABILITIES = (
    ("PERSON_CONTACT_READ", "Person contact read", "Read permitted Person contact details."),
    ("PERSON_DEMOGRAPHIC_READ", "Person demographic read", "Read permitted Person demographic details."),
    ("PERSON_EXPORT", "Person export", "Export permitted Person data."),
    ("PERSON_RESTRICTED_ACCESS", "Person restricted access", "Additional gate for explicitly approved exceptional workflows."),
    ("PRIVACY_AUDIT_READ", "Privacy audit read", "Read privacy audit history where separately authorized."),
    ("STAFF_CAPABILITY_ADMIN", "Staff capability administration", "Administer staff capability permissions through approved governance workflows."),
)

ROLE_DEFAULTS = {
    "CRM_ADMIN": {"PERSON_CONTACT_READ", "PERSON_DEMOGRAPHIC_READ", "PERSON_EXPORT", "STAFF_CAPABILITY_ADMIN"},
    "CRM_MANAGER": {"PERSON_CONTACT_READ"},
    "CRM_VIEWER": set(),
}


def seed_capabilities_and_defaults(apps, schema_editor):
    CapabilityDefinition = apps.get_model("staff_access", "CapabilityDefinition")
    StaffRole = apps.get_model("staff_access", "StaffRole")
    RoleCapabilityDefault = apps.get_model("staff_access", "RoleCapabilityDefault")

    capabilities = {}
    for code, name, description in CAPABILITIES:
        capability, _created = CapabilityDefinition.objects.update_or_create(
            code=code,
            defaults={"name": name, "description": description, "is_active": True},
        )
        capabilities[code] = capability

    for role_code, capability_codes in ROLE_DEFAULTS.items():
        role, _created = StaffRole.objects.get_or_create(
            code=role_code,
            defaults={"name": role_code.replace("_", " ").title(), "is_active": True},
        )
        for capability_code in capability_codes:
            RoleCapabilityDefault.objects.update_or_create(
                role=role,
                capability=capabilities[capability_code],
                defaults={"is_enabled": True},
            )


class Migration(migrations.Migration):
    dependencies = [
        ("staff_access", "0005_staff_email_delivery"),
    ]

    operations = [
        migrations.CreateModel(
            name="CapabilityDefinition",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code", models.CharField(max_length=64, unique=True)),
                ("name", models.CharField(max_length=255)),
                ("description", models.TextField()),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ["code"]},
        ),
        migrations.CreateModel(
            name="RoleCapabilityDefault",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("is_enabled", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("capability", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="role_defaults", to="staff_access.capabilitydefinition")),
                ("role", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="capability_defaults", to="staff_access.staffrole")),
            ],
            options={
                "ordering": ["role__code", "capability__code"],
                "indexes": [models.Index(fields=["role", "is_enabled"], name="staff_role_cap_default_idx")],
                "constraints": [models.UniqueConstraint(fields=("role", "capability"), name="staff_role_capability_default_unique")],
            },
        ),
        migrations.CreateModel(
            name="StaffCapabilityAssignment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("state", models.CharField(choices=[("ACTIVE", "Active"), ("SUSPENDED", "Suspended"), ("REVOKED", "Revoked")], default="ACTIVE", max_length=20)),
                ("granted_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("suspended_at", models.DateTimeField(blank=True, null=True)),
                ("revoked_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("capability", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="assignments", to="staff_access.capabilitydefinition")),
                ("granted_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="staff_capability_assignments_granted", to=settings.AUTH_USER_MODEL)),
                ("revoked_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="staff_capability_assignments_revoked", to=settings.AUTH_USER_MODEL)),
                ("suspended_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="staff_capability_assignments_suspended", to=settings.AUTH_USER_MODEL)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="staff_capability_assignments", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ["user_id", "capability__code", "-created_at", "-id"],
                "indexes": [
                    models.Index(fields=["user", "capability", "state"], name="staff_cap_assignment_idx"),
                    models.Index(fields=["state", "capability"], name="staff_cap_state_cap_idx"),
                ],
                "constraints": [models.UniqueConstraint(condition=models.Q(("state__in", ["ACTIVE", "SUSPENDED"])), fields=("user", "capability"), name="staff_capability_one_current_assignment")],
            },
        ),
        migrations.CreateModel(
            name="StaffCapabilityRestriction",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("is_active", models.BooleanField(default=True)),
                ("restricted_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("removed_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("capability", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="restrictions", to="staff_access.capabilitydefinition")),
                ("removed_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="staff_capability_restrictions_removed", to=settings.AUTH_USER_MODEL)),
                ("restricted_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="staff_capability_restrictions_created", to=settings.AUTH_USER_MODEL)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="staff_capability_restrictions", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ["user_id", "capability__code", "-created_at", "-id"],
                "indexes": [models.Index(fields=["user", "capability", "is_active"], name="staff_cap_restriction_idx")],
                "constraints": [models.UniqueConstraint(condition=models.Q(("is_active", True), ("removed_at__isnull", True)), fields=("user", "capability"), name="staff_capability_one_active_restriction")],
            },
        ),
        migrations.RunPython(seed_capabilities_and_defaults, migrations.RunPython.noop),
    ]
