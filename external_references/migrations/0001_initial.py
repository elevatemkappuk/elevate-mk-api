from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("people", "0004_normalize_person_demographic_values"),
    ]

    operations = [
        migrations.CreateModel(
            name="ExternalPersonReference",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("provider", models.CharField(max_length=100)),
                ("reference_type", models.CharField(choices=[("MARKETING_CONTACT", "Marketing contact")], max_length=40)),
                ("external_id", models.CharField(max_length=255)),
                ("status", models.CharField(choices=[("ACTIVE", "Active"), ("REVOKED", "Revoked")], default="ACTIVE", max_length=20)),
                ("linked_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("revoked_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("person", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="external_person_references", to="people.person")),
            ],
            options={
                "ordering": ["provider", "reference_type", "external_id", "id"],
            },
        ),
        migrations.AddConstraint(
            model_name="externalpersonreference",
            constraint=models.UniqueConstraint(fields=("provider", "reference_type", "external_id"), name="external_person_ref_provider_type_id_unique"),
        ),
        migrations.AddConstraint(
            model_name="externalpersonreference",
            constraint=models.UniqueConstraint(fields=("person", "provider", "reference_type"), name="external_person_ref_person_provider_type_unique"),
        ),
        migrations.AddIndex(
            model_name="externalpersonreference",
            index=models.Index(fields=["person", "status"], name="ext_person_ref_p_status_idx"),
        ),
        migrations.AddIndex(
            model_name="externalpersonreference",
            index=models.Index(fields=["provider", "reference_type", "status"], name="ext_person_ref_prov_st_idx"),
        ),
    ]
