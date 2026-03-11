from django.db import migrations, models


def migrate_boolean_to_choices(apps, schema_editor):
    MeetupEvent = apps.get_model("crush_lu", "MeetupEvent")
    MeetupEvent.objects.filter(require_approved_profile=True).update(
        profile_requirement="approved"
    )
    MeetupEvent.objects.filter(require_approved_profile=False).update(
        profile_requirement="none"
    )


class Migration(migrations.Migration):

    dependencies = [
        ("crush_lu", "0095_eventpolloption_static_image_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="meetupevent",
            name="profile_requirement",
            field=models.CharField(
                choices=[
                    ("approved", "Approved profile required"),
                    ("profile_exists", "Profile must exist"),
                    ("none", "No profile required"),
                ],
                default="approved",
                help_text="Controls what level of profile is needed to register for this event",
                max_length=20,
            ),
        ),
        migrations.RunPython(
            migrate_boolean_to_choices,
            migrations.RunPython.noop,
        ),
        migrations.RemoveField(
            model_name="meetupevent",
            name="require_approved_profile",
        ),
    ]
