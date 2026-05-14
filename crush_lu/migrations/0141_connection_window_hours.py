"""
Rename MeetupEvent.spark_request_deadline_hours -> connection_window_hours,
change its default from 168 (7 days) to 24 (1 day), and reset every existing
event to the new 24-hour window.

Background: the "Send Crush Spark" feature is being soft-removed from the
post-event flow. Direct connection requests are now the only post-event
matching action, and the window during which they're allowed shrinks from
7 days to 24 hours. The model field that historically powered both flows
is renamed to reflect its new single purpose.
"""
from django.db import migrations, models


def reset_to_24h(apps, schema_editor):
    MeetupEvent = apps.get_model("crush_lu", "MeetupEvent")
    MeetupEvent.objects.update(connection_window_hours=24)


def restore_to_168h(apps, schema_editor):
    MeetupEvent = apps.get_model("crush_lu", "MeetupEvent")
    MeetupEvent.objects.update(connection_window_hours=168)


class Migration(migrations.Migration):

    dependencies = [
        ("crush_lu", "0139_profilesubmission_revision_round"),
    ]

    operations = [
        migrations.RenameField(
            model_name="meetupevent",
            old_name="spark_request_deadline_hours",
            new_name="connection_window_hours",
        ),
        migrations.AlterField(
            model_name="meetupevent",
            name="connection_window_hours",
            field=models.PositiveIntegerField(
                default=24,
                help_text=(
                    "Hours after event start until post-event connection "
                    "requests close (default: 24 = 1 day)."
                ),
            ),
        ),
        migrations.RunPython(reset_to_24h, restore_to_168h),
    ]
