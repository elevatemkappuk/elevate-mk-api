import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("notifications", "0003_alter_transactionalemailjob_email_change_request_and_more"),
        ("staff_access", "0004_staffemailchangerequest"),
    ]
    operations = [
        migrations.AddField(
            model_name="transactionalemailjob",
            name="staff_invitation",
            field=models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="transactional_email_job", to="staff_access.staffinvitation"),
        ),
        migrations.AddField(
            model_name="transactionalemailjob",
            name="staff_email_change_request",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="transactional_email_jobs", to="staff_access.staffemailchangerequest"),
        ),
        migrations.AlterField(
            model_name="transactionalemailjob",
            name="job_type",
            field=models.CharField(choices=[("COMMUNITY_ACTIVATION", "Community activation"), ("COMMUNITY_EMAIL_CHANGE", "Community email change verification"), ("COMMUNITY_EMAIL_CHANGE_SECURITY", "Community email change security notification"), ("STAFF_INVITATION", "Staff invitation"), ("STAFF_EMAIL_CHANGE", "Staff email change verification")], default="COMMUNITY_ACTIVATION", max_length=50),
        ),
        migrations.RemoveConstraint(
            model_name="transactionalemailjob",
            name="transactional_email_job_single_resource",
        ),
        migrations.AddConstraint(
            model_name="transactionalemailjob",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(invitation__isnull=False, email_change_request__isnull=True, staff_invitation__isnull=True, staff_email_change_request__isnull=True)
                    | models.Q(invitation__isnull=True, email_change_request__isnull=False, staff_invitation__isnull=True, staff_email_change_request__isnull=True)
                    | models.Q(invitation__isnull=True, email_change_request__isnull=True, staff_invitation__isnull=False, staff_email_change_request__isnull=True)
                    | models.Q(invitation__isnull=True, email_change_request__isnull=True, staff_invitation__isnull=True, staff_email_change_request__isnull=False)
                ),
                name="transactional_email_job_single_resource",
            ),
        ),
    ]
