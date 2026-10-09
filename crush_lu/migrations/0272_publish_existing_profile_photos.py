"""Publish every photo members can already see, so the held-replacement rule
changes nothing for them at deploy time.

Before this change a primary photo was visible to other members unless a coach
had moderated it, while photos 2 and 3 needed an approval of the exact file.
Those are exactly the files published here; only uploads made from now on
wait for a coach before other members see them.
"""

from django.db import migrations
from django.utils import timezone

MODERATED = ("needs_revision", "flagged_fake")


def publish_existing(apps, schema_editor):
    CrushProfile = apps.get_model("crush_lu", "CrushProfile")
    ReviewState = apps.get_model("crush_lu", "ProfilePhotoReviewState")
    Published = apps.get_model("crush_lu", "PublishedProfilePhoto")

    states = {
        (row["profile_id"], row["photo_field"]): (row["photo_key"], row["status"])
        for row in ReviewState.objects.values(
            "profile_id", "photo_field", "photo_key", "status"
        ).iterator()
    }
    now = timezone.now()
    batch = []
    rows = (
        CrushProfile.objects.exclude(photo_review_status="flagged_fake")
        .values(
            "pk",
            "photo_1",
            "photo_2",
            "photo_3",
            "photo_review_status",
            "photo_review_key",
        )
        .order_by("pk")
        .iterator()
    )
    for row in rows:
        for field in ("photo_1", "photo_2", "photo_3"):
            key = row[field] or ""
            if not key:
                continue
            state_key, state_status = states.get((row["pk"], field), ("", ""))
            status = state_status if state_key == key else "pending"
            if field == "photo_1":
                if (
                    row["photo_review_status"] == "needs_revision"
                    or status in MODERATED
                ):
                    continue
            elif status != "approved":
                continue
            batch.append(
                Published(
                    profile_id=row["pk"],
                    photo_field=field,
                    photo_key=key,
                    published_at=now,
                )
            )
        if len(batch) >= 500:
            Published.objects.bulk_create(batch, ignore_conflicts=True)
            batch = []
    if batch:
        Published.objects.bulk_create(batch, ignore_conflicts=True)


class Migration(migrations.Migration):

    dependencies = [
        ("crush_lu", "0271_photo_hold_and_member_deck"),
    ]

    operations = [
        migrations.RunPython(publish_existing, migrations.RunPython.noop),
    ]
