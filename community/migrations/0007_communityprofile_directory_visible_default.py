from django.db import migrations, models


def promote_existing_profiles(apps, schema_editor):
    CommunityProfile = apps.get_model("community", "CommunityProfile")
    CommunityProfile.objects.filter(directory_visible=False).update(directory_visible=True)


class Migration(migrations.Migration):
    dependencies = [
        ("community", "0006_communityprofile_directory_privacy"),
    ]

    operations = [
        migrations.AlterField(
            model_name="communityprofile",
            name="directory_visible",
            field=models.BooleanField(default=True),
        ),
        migrations.RunPython(promote_existing_profiles, migrations.RunPython.noop),
    ]
