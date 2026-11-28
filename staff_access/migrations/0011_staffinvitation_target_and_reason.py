from django.db import migrations, models
import django.db.models.deletion
from django.conf import settings


class Migration(migrations.Migration):
    dependencies = [
        ("staff_access", "0010_staff_administrator_proposals"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="staffinvitation",
            name="target_user",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="staff_invitations_received",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="staffinvitation",
            name="operation",
            field=models.CharField(default="ONBOARD", max_length=32),
        ),
        migrations.AddField(
            model_name="staffinvitation",
            name="reason",
            field=models.TextField(blank=True),
        ),
    ]
