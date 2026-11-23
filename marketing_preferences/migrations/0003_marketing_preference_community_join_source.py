from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("marketing_preferences", "0002_alter_marketingpreference_source_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="marketingpreference",
            name="source",
            field=models.CharField(
                choices=[
                    ("MEMBERSHIP_FORM", "Membership form"),
                    ("WEBSITE_SIGNUP", "Website signup"),
                    ("STAFF_RECORDED", "Staff recorded"),
                    ("HISTORICAL_IMPORT", "Historical import"),
                    ("MAILCHIMP", "Mailchimp"),
                    ("BREVO", "Brevo"),
                    ("COMMUNITY_JOIN", "Community Join"),
                    ("OTHER", "Other"),
                ],
                max_length=30,
            ),
        ),
        migrations.AlterField(
            model_name="marketingpreferencehistory",
            name="source",
            field=models.CharField(
                choices=[
                    ("MEMBERSHIP_FORM", "Membership form"),
                    ("WEBSITE_SIGNUP", "Website signup"),
                    ("STAFF_RECORDED", "Staff recorded"),
                    ("HISTORICAL_IMPORT", "Historical import"),
                    ("MAILCHIMP", "Mailchimp"),
                    ("BREVO", "Brevo"),
                    ("COMMUNITY_JOIN", "Community Join"),
                    ("OTHER", "Other"),
                ],
                max_length=30,
            ),
        ),
    ]
