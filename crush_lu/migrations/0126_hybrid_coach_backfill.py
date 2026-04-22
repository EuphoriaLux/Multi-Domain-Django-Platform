"""
Backfill for the Hybrid Coach Review System.

- ProfileSubmission.assigned_at: use submitted_at for any submission that
  already has a coach attached.
- ProfileSubmission.sla_deadline: for `pending` submissions, set to
  max(submitted_at + 48h, deploy_time + 24h). The 24h floor bounds the
  day-one escalation wave so 100+ stuck submissions don't all breach SLA
  the instant the scheduler flips on.
- ProfileSubmission.recontact_started_at: for `recontact_coach`
  submissions, fall back to reviewed_at or submitted_at so the 14-day
  auto-expiry has an anchor.

Reverse is a no-op — we don't restore the pre-populated fields because
no code reads them before this runs.
"""
from datetime import timedelta

from django.db import migrations
from django.utils import timezone


def forwards(apps, schema_editor):
    ProfileSubmission = apps.get_model("crush_lu", "ProfileSubmission")
    now = timezone.now()
    floor = now + timedelta(hours=24)

    # assigned_at: submitted_at for already-assigned submissions.
    assigned_qs = ProfileSubmission.objects.filter(
        coach__isnull=False, assigned_at__isnull=True
    )
    for sub in assigned_qs.iterator(chunk_size=500):
        sub.assigned_at = sub.submitted_at
        sub.save(update_fields=["assigned_at"])

    # sla_deadline for still-pending reviews.
    pending_qs = ProfileSubmission.objects.filter(
        status="pending", sla_deadline__isnull=True
    )
    for sub in pending_qs.iterator(chunk_size=500):
        candidate = sub.submitted_at + timedelta(hours=48)
        sub.sla_deadline = max(candidate, floor)
        sub.save(update_fields=["sla_deadline"])

    # recontact_started_at: anchor for 14-day expiry.
    recontact_qs = ProfileSubmission.objects.filter(
        status="recontact_coach", recontact_started_at__isnull=True
    )
    for sub in recontact_qs.iterator(chunk_size=500):
        sub.recontact_started_at = sub.reviewed_at or sub.submitted_at
        sub.save(update_fields=["recontact_started_at"])


def reverse(apps, schema_editor):
    # Intentional no-op — reversing would require distinguishing backfill
    # values from subsequently-set production values, which isn't safe.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("crush_lu", "0125_hybrid_coach_schema"),
    ]

    operations = [
        migrations.RunPython(forwards, reverse),
    ]
