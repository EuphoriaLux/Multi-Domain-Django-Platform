from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("crush_lu", "0098_remove_completed_status"),
    ]

    operations = [
        migrations.AlterField(
            model_name="meetupevent",
            name="profile_requirement",
            field=models.CharField(
                choices=[
                    ("approved", "Approved profile required"),
                    ("unverified", "Unverified profile only"),
                    ("profile_exists", "Profile must exist"),
                    ("none", "No profile required"),
                ],
                default="approved",
                help_text="Controls what level of profile is needed to register for this event",
                max_length=20,
            ),
        ),
    ]
