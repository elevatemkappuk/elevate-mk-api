from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0002_authentication_revocation"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="user",
            options={
                "permissions": [
                    (
                        "access_api_documentation",
                        "Can access protected API documentation",
                    ),
                ],
            },
        ),
    ]
