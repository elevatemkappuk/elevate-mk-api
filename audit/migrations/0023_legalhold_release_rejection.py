from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("audit", "0022_auditeventsubject_legalhold_legalholdreleaserequest_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="legalholdreleaserequest",
            name="status",
            field=models.CharField(
                choices=[
                    ("PENDING", "Pending"),
                    ("APPROVED", "Approved"),
                    ("REJECTED", "Rejected"),
                ],
                default="PENDING",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="legalholdreleaserequest",
            name="rejected_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="legalholdreleaserequest",
            name="rejected_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="legal_hold_release_rejections",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="legalholdreleaserequest",
            name="rejection_reason",
            field=models.TextField(blank=True),
        ),
    ]
