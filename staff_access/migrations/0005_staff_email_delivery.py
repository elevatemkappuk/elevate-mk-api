from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("staff_access", "0004_staffemailchangerequest")]
    operations = [
        migrations.AlterField(
            model_name="staffemailchangerequest",
            name="token_hash",
            field=models.CharField(blank=True, max_length=64, null=True, unique=True),
        ),
    ]
