"""Backfill coach_intro_seen_at for users who are already past step 3.

Without this, existing production users (welcome_seen_at set, phone_verified
True, or already submitted) would be yanked back to the step-3 coach-intro
page on the next /onboarding/ hit because the new field defaults to NULL.
"""
from django.db import migrations


def backfill(apps, schema_editor):
    CrushProfile = apps.get_model("crush_lu", "CrushProfile")
    from django.utils import timezone
    now = timezone.now()
    CrushProfile.objects.filter(
        coach_intro_seen_at__isnull=True,
    ).filter(
        # Any signal that the user is past step 3: phone verified, or they
        # already finished the old flow and submitted.
        phone_verified=True,
    ).update(coach_intro_seen_at=now)

    # Also catch anyone who submitted without phone_verified (rare edge case
    # via legacy paths) so they don't regress into the intro page.
    CrushProfile.objects.filter(
        coach_intro_seen_at__isnull=True,
        completion_status="submitted",
    ).update(coach_intro_seen_at=now)


def reverse(apps, schema_editor):
    # Nothing to undo — leaving the timestamps in place is safe.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("crush_lu", "0131_crushprofile_coach_intro_seen_at"),
    ]

    operations = [
        migrations.RunPython(backfill, reverse),
    ]
