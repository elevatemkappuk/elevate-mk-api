import uuid

from django.db import migrations, models


def populate_asset_namespace_ids(apps, schema_editor):
    CommunityProfile = apps.get_model("community", "CommunityProfile")
    for profile in CommunityProfile.objects.filter(asset_namespace_id__isnull=True).iterator():
        profile.asset_namespace_id = uuid.uuid4()
        profile.save(update_fields=["asset_namespace_id"])


class Migration(migrations.Migration):
    dependencies = [
        ("community", "0004_communityprofile_photo"),
    ]

    operations = [
        migrations.AddField(
            model_name="communityprofile",
            name="asset_namespace_id",
            field=models.UUIDField(editable=False, null=True, unique=True),
        ),
        migrations.RunPython(populate_asset_namespace_ids, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="communityprofile",
            name="asset_namespace_id",
            field=models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
        ),
    ]
