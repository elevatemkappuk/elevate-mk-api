from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("audit", "0026_alter_auditevent_action")]

    operations = [
        migrations.AddField(
            model_name="legalholdreleaserequest",
            name="cancelled_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="legal_hold_release_cancellations",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="legalholdreleaserequest",
            name="cancelled_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="legalholdreleaserequest",
            name="cancellation_reason",
            field=models.TextField(blank=True),
        ),
    ]
