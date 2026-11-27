from django.db import migrations, models


def classify_existing_inactive_accounts(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    User.objects.filter(is_active=True).update(account_lifecycle_state="ACTIVE")
    User.objects.filter(is_active=False).update(account_lifecycle_state="DISABLED")


class Migration(migrations.Migration):
    dependencies = [("accounts", "0003_api_documentation_permission")]

    operations = [
        migrations.AddField(
            model_name="user",
            name="account_lifecycle_state",
            field=models.CharField(
                choices=[
                    ("ACTIVE", "Active"),
                    ("PENDING_ACTIVATION", "Pending activation"),
                    ("DISABLED", "Disabled"),
                ],
                default="ACTIVE",
                max_length=24,
            ),
        ),
        migrations.RunPython(classify_existing_inactive_accounts, migrations.RunPython.noop),
    ]
