import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('marketing_preferences', '0001_initial'),
        ('people', '0004_normalize_person_demographic_values'),
    ]

    operations = [
        migrations.AlterField(
            model_name='marketingpreference',
            name='source',
            field=models.CharField(choices=[('MEMBERSHIP_FORM', 'Membership form'), ('WEBSITE_SIGNUP', 'Website signup'), ('STAFF_RECORDED', 'Staff recorded'), ('HISTORICAL_IMPORT', 'Historical import'), ('MAILCHIMP', 'Mailchimp'), ('BREVO', 'Brevo'), ('OTHER', 'Other')], max_length=30),
        ),
        migrations.AlterField(
            model_name='marketingpreferencehistory',
            name='source',
            field=models.CharField(choices=[('MEMBERSHIP_FORM', 'Membership form'), ('WEBSITE_SIGNUP', 'Website signup'), ('STAFF_RECORDED', 'Staff recorded'), ('HISTORICAL_IMPORT', 'Historical import'), ('MAILCHIMP', 'Mailchimp'), ('BREVO', 'Brevo'), ('OTHER', 'Other')], max_length=30),
        ),
        migrations.CreateModel(
            name='MarketingWebhookReceipt',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('provider', models.CharField(max_length=100)),
                ('event_id', models.CharField(max_length=255)),
                ('event_type', models.CharField(max_length=80)),
                ('event_recorded_at', models.DateTimeField(blank=True, null=True)),
                ('list_ids', models.JSONField(blank=True, default=list)),
                ('campaign_id', models.CharField(blank=True, max_length=255, null=True)),
                ('outcome', models.CharField(max_length=80)),
                ('received_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('person', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='marketing_webhook_receipts', to='people.person')),
            ],
            options={
                'ordering': ['-received_at', '-id'],
                'indexes': [models.Index(fields=['provider', 'event_type', 'received_at'], name='marketing_webhook_event_idx')],
                'constraints': [models.UniqueConstraint(fields=('provider', 'event_id'), name='marketing_webhook_provider_event_unique')],
            },
        ),
    ]
