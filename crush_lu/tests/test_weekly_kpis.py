"""
Tests for the weekly KPI snapshot service, command, and email.

Run with: pytest crush_lu/tests/test_weekly_kpis.py -v
"""
from datetime import date, datetime, time

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from crush_lu.models import (
    CrushProfile,
    ProfileSubmission,
    UserActivity,
    WeeklyMetricsSnapshot,
)
from crush_lu.services.weekly_kpis import (
    compute_weekly_snapshot,
    last_completed_week_start,
    snapshot_with_deltas,
    upsert_snapshot,
)

User = get_user_model()

# A known Monday and the week it begins (2026-06-08 .. 2026-06-14).
WEEK_START = date(2026, 6, 8)


def _aware(d: date, hour: int = 12):
    """Timezone-aware datetime at a given hour on date ``d``."""
    return timezone.make_aware(datetime.combine(d, time(hour, 0)))


class ComputeWeeklySnapshotTests(TestCase):
    def setUp(self):
        # ── In-week user: signed up, verified, active this week ──────
        self.in_user = User.objects.create_user(
            username="inweek@example.com",
            email="inweek@example.com",
            password="x",
            date_joined=_aware(date(2026, 6, 10)),
        )
        profile = CrushProfile.objects.create(
            user=self.in_user,
            gender="F",
            location="Luxembourg",
            verification_status="verified",
            phone_number="+352123456789",
            phone_verified=True,
            phone_verified_at=_aware(date(2026, 6, 11)),
            approved_at=_aware(date(2026, 6, 12)),
        )
        # created_at is auto_now_add; force it into the week via queryset update.
        CrushProfile.objects.filter(pk=profile.pk).update(
            created_at=_aware(date(2026, 6, 10))
        )
        sub = ProfileSubmission.objects.create(profile=profile, status="pending")
        ProfileSubmission.objects.filter(pk=sub.pk).update(
            submitted_at=_aware(date(2026, 6, 9))
        )
        ua = UserActivity.objects.create(
            user=self.in_user, last_seen=_aware(date(2026, 6, 11)), is_pwa_user=True
        )
        UserActivity.objects.filter(pk=ua.pk).update(
            first_seen=_aware(date(2026, 6, 10))
        )

        # ── Out-of-week user: joined and last active before the week ─
        self.out_user = User.objects.create_user(
            username="outweek@example.com",
            email="outweek@example.com",
            password="x",
            date_joined=_aware(date(2026, 5, 1)),
        )
        UserActivity.objects.create(
            user=self.out_user, last_seen=_aware(date(2026, 5, 1))
        )

        # ── Legacy verified member with no approved_at timestamp ─────
        self.legacy_user = User.objects.create_user(
            username="legacy@example.com",
            email="legacy@example.com",
            password="x",
            date_joined=_aware(date(2026, 5, 1)),
        )
        CrushProfile.objects.create(
            user=self.legacy_user,
            gender="M",
            location="Luxembourg",
            verification_status="verified",
            approved_at=None,
        )

    def test_acquisition_counts_only_in_week(self):
        m = compute_weekly_snapshot(WEEK_START)["acquisition"]
        self.assertEqual(m["new_signups"], 1)
        self.assertEqual(m["new_profiles"], 1)
        self.assertEqual(m["phone_verifications"], 1)
        self.assertEqual(m["profiles_submitted"], 1)
        # Weekly verified keys on approved_at, so the legacy (NULL) verified
        # member is NOT attributed to this week.
        self.assertEqual(m["profiles_verified"], 1)
        # Cumulative includes everyone up to week_end (in-week + legacy users).
        self.assertEqual(m["cumulative_total_users"], 3)
        # Both the in-week verified member AND the legacy NULL-approved_at one.
        self.assertEqual(m["cumulative_verified_members"], 2)

    def test_engagement_distinguishes_active_and_dormant(self):
        m = compute_weekly_snapshot(WEEK_START)["engagement"]
        self.assertEqual(m["wau"], 1)
        self.assertEqual(m["new_active"], 1)
        self.assertEqual(m["pwa_active"], 1)
        # The out-of-week user last seen 2026-05-01 is dormant.
        self.assertEqual(m["dormant"], 1)


class UpsertIdempotencyTests(TestCase):
    def test_upsert_is_idempotent_per_week(self):
        _, created_first = upsert_snapshot(WEEK_START)
        _, created_second = upsert_snapshot(WEEK_START)
        self.assertTrue(created_first)
        self.assertFalse(created_second)
        self.assertEqual(
            WeeklyMetricsSnapshot.objects.filter(week_start=WEEK_START).count(), 1
        )


class DeltaTests(TestCase):
    def test_deltas_compare_to_previous_week(self):
        prev_week = date(2026, 6, 1)
        WeeklyMetricsSnapshot.objects.create(
            week_start=prev_week,
            week_end=date(2026, 6, 7),
            metrics={"acquisition": {"new_signups": 2}},
        )
        WeeklyMetricsSnapshot.objects.create(
            week_start=WEEK_START,
            week_end=date(2026, 6, 14),
            metrics={"acquisition": {"new_signups": 5}},
        )
        payload = snapshot_with_deltas(WEEK_START)
        self.assertEqual(payload["previous_week_start"], prev_week)
        self.assertEqual(payload["deltas"]["acquisition"]["new_signups"], 3)

    def test_delta_is_none_without_previous_week(self):
        WeeklyMetricsSnapshot.objects.create(
            week_start=WEEK_START,
            week_end=date(2026, 6, 14),
            metrics={"acquisition": {"new_signups": 5}},
        )
        payload = snapshot_with_deltas(WEEK_START)
        self.assertIsNone(payload["previous_week_start"])
        self.assertIsNone(payload["deltas"]["acquisition"]["new_signups"])


@override_settings(WEEKLY_KPI_RECIPIENTS=["kpi@example.com"])
class SendWeeklyKpisCommandTests(TestCase):
    def test_command_persists_and_emails(self):
        call_command("send_weekly_kpis", "--week-start", WEEK_START.isoformat())
        self.assertTrue(
            WeeklyMetricsSnapshot.objects.filter(week_start=WEEK_START).exists()
        )
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("weekly KPIs", mail.outbox[0].subject)

    def test_no_email_flag_skips_send(self):
        call_command(
            "send_weekly_kpis", "--week-start", WEEK_START.isoformat(), "--no-email"
        )
        self.assertTrue(
            WeeklyMetricsSnapshot.objects.filter(week_start=WEEK_START).exists()
        )
        self.assertEqual(len(mail.outbox), 0)


class LastCompletedWeekTests(TestCase):
    def test_returns_prior_monday(self):
        # 2026-06-17 is a Wednesday; the last full week began Mon 2026-06-08.
        self.assertEqual(last_completed_week_start(date(2026, 6, 17)), WEEK_START)
