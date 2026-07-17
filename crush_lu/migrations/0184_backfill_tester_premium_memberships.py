# Data migration for the Stage-5 receiver-gate rework.
#
# The Premium/receiver gates now key off an ACTIVE PremiumMembership instead
# of bare CrushProfile.assigned_coach (which the 0150 backfill and the
# attendance auto-assign signal set without payment). Hand-picked beta
# testers (CrushConnectWaitlist.selected_as_tester) were comped by assigning
# a coach directly — give them the membership row the new gate expects so
# their beta access is uninterrupted. Waitlist.payment_confirmed (the manual
# €10/month flag) carries over.

from django.db import migrations


def backfill_tester_memberships(apps, schema_editor):
    CrushConnectWaitlist = apps.get_model("crush_lu", "CrushConnectWaitlist")
    PremiumMembership = apps.get_model("crush_lu", "PremiumMembership")

    testers = CrushConnectWaitlist.objects.filter(
        selected_as_tester=True,
        user__crushprofile__assigned_coach__isnull=False,
    ).select_related("user__crushprofile")

    for entry in testers.iterator():
        profile = entry.user.crushprofile
        has_active = PremiumMembership.objects.filter(
            user_id=entry.user_id, status="active"
        ).exists()
        if has_active:
            continue
        PremiumMembership.objects.create(
            user_id=entry.user_id,
            coach_id=profile.assigned_coach_id,
            status="active",
            payment_confirmed=entry.payment_confirmed,
            # Use the waitlist's actual payment timestamp when available so
            # weekly KPI windowing (services/weekly_kpis.py keys on
            # payment_date) reports these testers in the correct week. Falls
            # back to selected_at for unpaid/unstamped testers.
            payment_date=entry.payment_date or entry.selected_at,
        )


class Migration(migrations.Migration):

    dependencies = [
        ("crush_lu", "0183_cachestation_manual_code"),
    ]

    operations = [
        migrations.RunPython(backfill_tester_memberships, migrations.RunPython.noop),
    ]
