from django.db import migrations


def migrate_importbatch_lifecycle_statuses(apps, schema_editor):
    ImportBatch = apps.get_model("data_imports", "ImportBatch")
    ImportRecord = apps.get_model("data_imports", "ImportRecord")

    ImportBatch.objects.filter(status__in=["PENDING", "STAGED", "COMMITTING"]).update(
        status="PROCESSING"
    )
    ImportBatch.objects.filter(status="READY_TO_COMMIT").update(status="READY_FOR_IMPORT")
    ImportBatch.objects.filter(status="COMPLETED").update(status="IMPORTED")

    for batch in ImportBatch.objects.filter(status="ANALYZED").iterator():
        has_unresolved_review = ImportRecord.objects.filter(
            batch_id=batch.pk,
            status="REVIEW_REQUIRED",
        ).exists()
        ImportBatch.objects.filter(pk=batch.pk).update(
            status="READY_FOR_REVIEW" if has_unresolved_review else "READY_FOR_IMPORT"
        )


class Migration(migrations.Migration):

    dependencies = [
        ("data_imports", "0005_alter_importbatch_status"),
    ]

    operations = [
        migrations.RunPython(migrate_importbatch_lifecycle_statuses, migrations.RunPython.noop),
    ]
