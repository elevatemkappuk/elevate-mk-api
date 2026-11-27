from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone
import uuid


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("staff_access", "0009_role_family_integrity"),
    ]

    operations = [
        migrations.CreateModel(
            name="StaffAdministratorProposal",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("public_id", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("operation", models.CharField(choices=[("PROMOTE_EXISTING", "Promote existing staff"), ("ONBOARD", "Onboard new staff"), ("REACTIVATE", "Reactivate former staff")], max_length=32)),
                ("reason", models.TextField()),
                ("policy_version", models.PositiveBigIntegerField()),
                ("expected_target_state", models.JSONField(default=dict)),
                ("status", models.CharField(choices=[("PENDING", "Pending"), ("APPROVED", "Approved"), ("EFFECTED", "Effected"), ("REJECTED", "Rejected"), ("CANCELLED", "Cancelled"), ("EXPIRED", "Expired"), ("INVALIDATED", "Invalidated")], default="PENDING", max_length=20)),
                ("expires_at", models.DateTimeField()),
                ("invalidation_reason", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("decided_at", models.DateTimeField(blank=True, null=True)),
                ("effected_at", models.DateTimeField(blank=True, null=True)),
                ("approved_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="staff_administrator_proposals_approved", to=settings.AUTH_USER_MODEL)),
                ("proposed_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="staff_administrator_proposals_created", to=settings.AUTH_USER_MODEL)),
                ("requested_role", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="staff_administrator_proposals", to="staff_access.staffrole")),
                ("resulting_assignment", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="administrator_proposals_effected", to="staff_access.staffroleassignment")),
                ("resulting_invitation", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="administrator_proposals_effected", to="staff_access.staffinvitation")),
                ("target_user", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="staff_administrator_proposals_targeted", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ["-created_at", "-id"],
                "indexes": [
                    models.Index(fields=["status", "expires_at"], name="staff_adm_prop_stat_exp"),
                    models.Index(fields=["target_user", "status"], name="staff_adm_prop_target"),
                ],
            },
        ),
        migrations.CreateModel(
            name="StaffAdministratorProposalDecision",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("outcome", models.CharField(choices=[("PENDING", "Pending"), ("APPROVED", "Approved"), ("EFFECTED", "Effected"), ("REJECTED", "Rejected"), ("CANCELLED", "Cancelled"), ("EXPIRED", "Expired"), ("INVALIDATED", "Invalidated")], max_length=20)),
                ("reason", models.TextField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("decided_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="staff_administrator_proposal_decisions", to=settings.AUTH_USER_MODEL)),
                ("proposal", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="decision", to="staff_access.staffadministratorproposal")),
            ],
            options={"ordering": ["-created_at", "-id"]},
        ),
        migrations.AddConstraint(
            model_name="staffadministratorproposal",
            constraint=models.CheckConstraint(condition=~models.Q(proposed_by=models.F("target_user")), name="staff_admin_proposal_no_self_target"),
        ),
        migrations.AddConstraint(
            model_name="staffadministratorproposal",
            constraint=models.CheckConstraint(condition=models.Q(expires_at__gt=models.F("created_at")), name="staff_admin_proposal_expiry_after_creation"),
        ),
        migrations.AddConstraint(
            model_name="staffadministratorproposal",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(status="PENDING", decided_at__isnull=True, effected_at__isnull=True)
                    | models.Q(status="APPROVED", decided_at__isnull=False, effected_at__isnull=True)
                    | models.Q(status="EFFECTED", decided_at__isnull=False, effected_at__isnull=False)
                    | models.Q(status__in=("REJECTED", "CANCELLED", "EXPIRED", "INVALIDATED"), decided_at__isnull=False, effected_at__isnull=True)
                ),
                name="staff_admin_proposal_lifecycle_state",
            ),
        ),
    ]
