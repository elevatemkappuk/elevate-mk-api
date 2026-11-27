from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone
import uuid


def seed_capability_policy_state(apps, schema_editor):
    CapabilityPolicyState = apps.get_model("staff_access", "CapabilityPolicyState")
    RoleCapabilityDefault = apps.get_model("staff_access", "RoleCapabilityDefault")
    StaffCapabilityAssignment = apps.get_model("staff_access", "StaffCapabilityAssignment")
    StaffCapabilityRestriction = apps.get_model("staff_access", "StaffCapabilityRestriction")
    AuditEvent = apps.get_model("audit", "AuditEvent")
    defaults = []
    for item in RoleCapabilityDefault.objects.select_related("role", "capability").order_by("role_id", "capability_id"):
        defaults.append(
            {
                "id": item.pk,
                "role_id": item.role_id,
                "role_code": item.role.code,
                "capability_id": item.capability_id,
                "capability_code": item.capability.code,
                "is_enabled": item.is_enabled,
                "updated_at": item.updated_at.isoformat(),
            }
        )
    assignments = []
    for item in StaffCapabilityAssignment.objects.select_related("capability").order_by("pk"):
        assignments.append(
            {
                "id": item.pk,
                "user_id": item.user_id,
                "capability_id": item.capability_id,
                "capability_code": item.capability.code,
                "state": item.state,
                "granted_at": item.granted_at.isoformat() if item.granted_at else None,
                "suspended_at": item.suspended_at.isoformat() if item.suspended_at else None,
                "revoked_at": item.revoked_at.isoformat() if item.revoked_at else None,
                "granted_by_id": item.granted_by_id,
                "suspended_by_id": item.suspended_by_id,
                "revoked_by_id": item.revoked_by_id,
            }
        )
    restrictions = []
    for item in StaffCapabilityRestriction.objects.select_related("capability").order_by("pk"):
        restrictions.append(
            {
                "id": item.pk,
                "user_id": item.user_id,
                "capability_id": item.capability_id,
                "capability_code": item.capability.code,
                "is_active": item.is_active,
                "restricted_at": item.restricted_at.isoformat() if item.restricted_at else None,
                "removed_at": item.removed_at.isoformat() if item.removed_at else None,
                "restricted_by_id": item.restricted_by_id,
                "removed_by_id": item.removed_by_id,
            }
        )
    baseline = {
        "role_capability_defaults": defaults,
        "individual_capability_assignments": assignments,
        "individual_capability_restrictions": restrictions,
    }
    captured_at = django.utils.timezone.now()
    state, _created = CapabilityPolicyState.objects.get_or_create(
        singleton_key=1,
        defaults={
            "key": "capability-governance",
            "version": 1,
            "reason": "Phase 1A.2 governance baseline",
            "baseline_captured_at": captured_at,
            "baseline_snapshot": baseline,
        },
    )
    AuditEvent.objects.create(
        actor_user=None,
        action="CAPABILITY_POLICY_BASELINED",
        entity_type="CapabilityPolicyState",
        entity_id=str(state.pk),
        changes=baseline,
        metadata={"source": "staff_access.0007_capability_governance", "policy_version": state.version},
        occurred_at=captured_at,
    )


class Migration(migrations.Migration):
    dependencies = [
        ("audit", "0021_alter_auditevent_action"),
        ("staff_access", "0006_capability_foundation"),
    ]

    operations = [
        migrations.CreateModel(
            name="CapabilityPolicyState",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("singleton_key", models.PositiveSmallIntegerField(default=1, editable=False, unique=True)),
                ("key", models.CharField(default="capability-governance", max_length=64, unique=True)),
                ("version", models.PositiveBigIntegerField(default=1)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("baseline_captured_at", models.DateTimeField(default=django.utils.timezone.now, editable=False)),
                ("baseline_snapshot", models.JSONField(default=dict, editable=False)),
                ("reason", models.TextField(blank=True)),
                ("updated_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="capability_policy_updates", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["key"]},
        ),
        migrations.AddConstraint(
            model_name="capabilitypolicystate",
            constraint=models.CheckConstraint(condition=models.Q(("singleton_key", 1)), name="staff_cap_policy_singleton_key"),
        ),
        migrations.AddConstraint(
            model_name="capabilitypolicystate",
            constraint=models.CheckConstraint(condition=models.Q(("key", "capability-governance")), name="staff_cap_policy_singleton_name"),
        ),
        migrations.CreateModel(
            name="CapabilityProposal",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("public_id", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("operation", models.CharField(choices=[("ROLE_DEFAULT_ENABLE", "Enable role default"), ("ROLE_DEFAULT_DISABLE", "Disable role default"), ("INDIVIDUAL_GRANT", "Grant individual capability"), ("INDIVIDUAL_RESTORE", "Restore individual capability"), ("INDIVIDUAL_SUSPEND", "Suspend individual capability"), ("INDIVIDUAL_REVOKE", "Revoke individual capability"), ("INDIVIDUAL_RESTRICT", "Restrict individual capability"), ("INDIVIDUAL_UNRESTRICT", "Remove individual restriction")], max_length=40)),
                ("status", models.CharField(choices=[("PENDING", "Pending"), ("APPROVED", "Approved"), ("REJECTED", "Rejected"), ("CANCELLED", "Cancelled"), ("EXPIRED", "Expired"), ("INVALIDATED", "Invalidated")], default="PENDING", max_length=20)),
                ("reason", models.TextField()),
                ("policy_version", models.PositiveBigIntegerField()),
                ("expected_target_state", models.JSONField(default=dict)),
                ("expires_at", models.DateTimeField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("resolved_at", models.DateTimeField(blank=True, null=True)),
                ("resolution_reason", models.TextField(blank=True)),
                ("controlled_recovery", models.BooleanField(default=False)),
                ("capability", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="proposals", to="staff_access.capabilitydefinition")),
                ("proposed_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="capability_proposals_created", to=settings.AUTH_USER_MODEL)),
                ("role", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="capability_proposals", to="staff_access.staffrole")),
                ("target_user", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="capability_proposals_targeted", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-created_at", "-id"]},
        ),
        migrations.AddConstraint(
            model_name="capabilityproposal",
            constraint=models.CheckConstraint(condition=(models.Q(("operation__in", ["ROLE_DEFAULT_ENABLE", "ROLE_DEFAULT_DISABLE"]), ("role__isnull", False), ("target_user__isnull", True)) | models.Q(("operation__in", ["INDIVIDUAL_GRANT", "INDIVIDUAL_RESTORE", "INDIVIDUAL_SUSPEND", "INDIVIDUAL_REVOKE", "INDIVIDUAL_RESTRICT", "INDIVIDUAL_UNRESTRICT"]), ("role__isnull", True), ("target_user__isnull", False))), name="staff_cap_proposal_target_shape"),
        ),
        migrations.AddConstraint(
            model_name="capabilityproposal",
            constraint=models.CheckConstraint(condition=(models.Q(("status", "PENDING"), ("resolved_at__isnull", True)) | models.Q(("status__in", ["APPROVED", "REJECTED", "CANCELLED", "EXPIRED", "INVALIDATED"]), ("resolved_at__isnull", False))), name="staff_cap_proposal_resolution_state"),
        ),
        migrations.AddConstraint(
            model_name="capabilityproposal",
            constraint=models.CheckConstraint(condition=models.Q(expires_at__gt=models.F("created_at")), name="staff_cap_proposal_expiry_after_creation"),
        ),
        migrations.AddConstraint(
            model_name="capabilityproposal",
            constraint=models.UniqueConstraint(condition=models.Q(("status", "PENDING"), ("operation__in", ["ROLE_DEFAULT_ENABLE", "ROLE_DEFAULT_DISABLE"])), fields=("role", "capability"), name="staff_cap_one_pending_role_proposal"),
        ),
        migrations.AddConstraint(
            model_name="capabilityproposal",
            constraint=models.UniqueConstraint(condition=models.Q(("status", "PENDING"), ("operation__in", ["INDIVIDUAL_GRANT", "INDIVIDUAL_RESTORE", "INDIVIDUAL_SUSPEND", "INDIVIDUAL_REVOKE", "INDIVIDUAL_RESTRICT", "INDIVIDUAL_UNRESTRICT"])), fields=("target_user", "capability"), name="staff_cap_one_pending_user_proposal"),
        ),
        migrations.AddIndex(model_name="capabilityproposal", index=models.Index(fields=["status", "expires_at"], name="staff_cap_prop_status_exp_idx")),
        migrations.AddIndex(model_name="capabilityproposal", index=models.Index(fields=["target_user", "capability", "status"], name="staff_cap_prop_user_idx")),
        migrations.AddIndex(model_name="capabilityproposal", index=models.Index(fields=["role", "capability", "status"], name="staff_cap_prop_role_idx")),
        migrations.CreateModel(
            name="CapabilityProposalDecision",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("outcome", models.CharField(choices=[("APPROVED", "Approved"), ("REJECTED", "Rejected"), ("CANCELLED", "Cancelled"), ("EXPIRED", "Expired"), ("INVALIDATED", "Invalidated")], max_length=20)),
                ("reason", models.TextField()),
                ("policy_version", models.PositiveBigIntegerField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("decided_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="capability_proposal_decisions", to=settings.AUTH_USER_MODEL)),
                ("proposal", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="decision", to="staff_access.capabilityproposal")),
            ],
            options={"ordering": ["-created_at", "-id"]},
        ),
        migrations.RunPython(seed_capability_policy_state, migrations.RunPython.noop),
    ]
