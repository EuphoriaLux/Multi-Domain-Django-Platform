"""Create the December 2026 past-event swipe campaign (decision 2026-11-20).

Times in Luxembourg time (CET, UTC+1): opens Tue 1 Dec 10:00, reminders
Fri 4 Dec 18:00 and Tue 8 Dec 19:00, last call Fri 11 Dec 10:00, closes
Sun 13 Dec 23:59. Switched on, so swiping opens by itself on 1 December;
messages go out once DJANGO_SWIPE_CAMPAIGN_URL is set on the Function App.
Editable (or switch off) in the admin under Connections > Swipe campaigns.
"""

from datetime import datetime, timezone

from django.db import migrations

NAME = "December 2026: swipe your past events"


def _utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


def create(apps, schema_editor):
    SwipeCampaign = apps.get_model("crush_lu", "SwipeCampaign")
    SwipeCampaign.objects.get_or_create(
        name=NAME,
        defaults={
            "starts_at": _utc(2026, 12, 1, 9, 0),
            "reminder_1_at": _utc(2026, 12, 4, 17, 0),
            "reminder_2_at": _utc(2026, 12, 8, 18, 0),
            "last_call_at": _utc(2026, 12, 11, 9, 0),
            "ends_at": _utc(2026, 12, 13, 22, 59),
            "is_active": True,
        },
    )


def remove(apps, schema_editor):
    apps.get_model("crush_lu", "SwipeCampaign").objects.filter(name=NAME).delete()


class Migration(migrations.Migration):
    dependencies = [("crush_lu", "0300_swipe_campaign")]

    operations = [migrations.RunPython(create, remove)]
