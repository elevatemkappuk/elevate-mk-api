from django.db import migrations, models
from django.core.validators import MaxLengthValidator
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("community", "0002_communityaccountinvitation"),
    ]

    operations = [
        migrations.CreateModel(
            name="CommunityProfile",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("bio", models.TextField(blank=True, max_length=400, validators=[MaxLengthValidator(400)])),
                ("person_preexisted_community", models.BooleanField(default=False, editable=False)),
                ("review_acknowledged_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "person",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="community_profile",
                        to="people.person",
                    ),
                ),
            ],
        ),
    ]
