import uuid

from django.db import migrations, models


def populate_directory_ids(apps, schema_editor):
    CommunityProfile = apps.get_model("community", "CommunityProfile")
    for profile in CommunityProfile.objects.filter(directory_id__isnull=True).iterator():
        profile.directory_id = uuid.uuid4()
        profile.save(update_fields=["directory_id"])


class Migration(migrations.Migration):
    dependencies = [
        ("community", "0005_communityprofile_asset_namespace_id"),
    ]

    operations = [
        migrations.AddField(
            model_name="communityprofile",
            name="directory_id",
            field=models.UUIDField(editable=False, null=True, unique=True),
        ),
        migrations.AddField(
            model_name="communityprofile",
            name="directory_visible",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="communityprofile",
            name="email_visible",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="communityprofile",
            name="mobile_visible",
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(populate_directory_ids, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="communityprofile",
            name="directory_id",
            field=models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
        ),
    ]
