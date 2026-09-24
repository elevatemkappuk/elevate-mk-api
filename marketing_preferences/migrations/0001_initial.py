from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("people", "0004_normalize_person_demographic_values"),
        ("accounts", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="MarketingPreference",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("channel", models.CharField(choices=[("EMAIL", "Email")], max_length=20)),
                ("state", models.CharField(choices=[("UNKNOWN", "Unknown"), ("OPTED_IN", "Opted in"), ("OPTED_OUT", "Opted out")], max_length=20)),
                ("source", models.CharField(choices=[("MEMBERSHIP_FORM", "Membership form"), ("WEBSITE_SIGNUP", "Website signup"), ("STAFF_RECORDED", "Staff recorded"), ("HISTORICAL_IMPORT", "Historical import"), ("MAILCHIMP", "Mailchimp"), ("OTHER", "Other")], max_length=30)),
                ("recorded_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("actor_user", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="recorded_marketing_preferences", to=settings.AUTH_USER_MODEL)),
                ("person", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="marketing_preferences", to="people.person")),
            ],
            options={"ordering": ["person_id", "channel", "id"]},
        ),
        migrations.CreateModel(
            name="MarketingPreferenceHistory",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("channel", models.CharField(choices=[("EMAIL", "Email")], max_length=20)),
                ("state", models.CharField(choices=[("UNKNOWN", "Unknown"), ("OPTED_IN", "Opted in"), ("OPTED_OUT", "Opted out")], max_length=20)),
                ("source", models.CharField(choices=[("MEMBERSHIP_FORM", "Membership form"), ("WEBSITE_SIGNUP", "Website signup"), ("STAFF_RECORDED", "Staff recorded"), ("HISTORICAL_IMPORT", "Historical import"), ("MAILCHIMP", "Mailchimp"), ("OTHER", "Other")], max_length=30)),
                ("recorded_at", models.DateTimeField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("actor_user", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="marketing_preference_history", to=settings.AUTH_USER_MODEL)),
                ("preference", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="history", to="marketing_preferences.marketingpreference")),
            ],
            options={"ordering": ["recorded_at", "id"]},
        ),
        migrations.AddConstraint(
            model_name="marketingpreference",
            constraint=models.UniqueConstraint(fields=("person", "channel"), name="marketing_preference_person_channel_unique"),
        ),
        migrations.AddIndex(
            model_name="marketingpreference",
            index=models.Index(fields=["channel", "state"], name="marketing_pref_chan_state_idx"),
        ),
        migrations.AddIndex(
            model_name="marketingpreferencehistory",
            index=models.Index(fields=["preference", "recorded_at"], name="marketing_pref_hist_time_idx"),
        ),
    ]
