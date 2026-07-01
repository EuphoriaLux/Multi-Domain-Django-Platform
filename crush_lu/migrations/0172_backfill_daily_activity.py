"""Backfill DailyUserActivity from existing UserActivity rows (issue #523).

The weekly KPI snapshot now derives WAU / PWA-active solely from
DailyUserActivity. That table is empty for all activity that happened before the
middleware started writing it, so without a backfill the first completed week
after deploy (and any earlier backfilled ``--week-start``) would report zero or
partial engagement even though UserActivity shows active users.

UserActivity only keeps the *latest* ``last_seen`` / ``last_pwa_visit`` (a single
mutable timestamp each), so the best we can seed is one row per user at their
last-seen day plus one at their last PWA-visit day. That matches exactly what the
old last_seen-based query could ever report for the most recent week, so no
information is lost relative to the previous behaviour, and daily rows accumulate
accurately from here forward.
"""

from django.db import migrations


def backfill_daily_activity(apps, schema_editor):
    from django.utils import timezone

    UserActivity = apps.get_model("crush_lu", "UserActivity")
    DailyUserActivity = apps.get_model("crush_lu", "DailyUserActivity")

    rows = []
    qs = UserActivity.objects.all().only("user_id", "last_seen", "last_pwa_visit")
    for ua in qs.iterator(chunk_size=2000):
        # date -> was_pwa; a PWA-visit day overrides a plain last_seen day.
        by_date = {}
        if ua.last_seen:
            by_date[timezone.localtime(ua.last_seen).date()] = False
        if ua.last_pwa_visit:
            by_date[timezone.localtime(ua.last_pwa_visit).date()] = True
        for activity_date, was_pwa in by_date.items():
            rows.append(
                DailyUserActivity(
                    user_id=ua.user_id,
                    activity_date=activity_date,
                    was_pwa=was_pwa,
                )
            )

    if rows:
        # ignore_conflicts guards the (user, activity_date) unique constraint in
        # case the middleware already wrote today's row before this migration ran.
        DailyUserActivity.objects.bulk_create(
            rows, ignore_conflicts=True, batch_size=1000
        )


class Migration(migrations.Migration):

    dependencies = [
        ("crush_lu", "0171_crushprofile_not_on_whatsapp_dailyuseractivity"),
    ]

    operations = [
        migrations.RunPython(backfill_daily_activity, migrations.RunPython.noop),
    ]
