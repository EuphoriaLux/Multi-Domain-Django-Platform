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
            user=self.in_user,
            last_seen=_aware(date(2026, 6, 11)),
            is_pwa_user=True,
            last_pwa_visit=_aware(date(2026, 6, 11)),
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
        # Cumulative counts Crush users (those with a CrushProfile) up to
        # week_end: the in-week user + the legacy verified one. out_user has no
        # CrushProfile (e.g. an account from another platform) and is excluded.
        self.assertEqual(m["cumulative_total_users"], 2)
        # Both the in-week verified member AND the legacy NULL-approved_at one.
        self.assertEqual(m["cumulative_verified_members"], 2)

    def test_signups_exclude_non_crush_users(self):
        # An account created in-week on another platform (no CrushProfile) must
        # not inflate the crush.lu signup numbers.
        User.objects.create_user(
            username="entreprinder@example.com",
            email="entreprinder@example.com",
            password="x",
            date_joined=_aware(date(2026, 6, 10)),
        )
        m = compute_weekly_snapshot(WEEK_START)["acquisition"]
        # Still only the in-week Crush user counts.
        self.assertEqual(m["new_signups"], 1)
        self.assertEqual(m["cumulative_total_users"], 2)

    def test_engagement_distinguishes_active_and_dormant(self):
        m = compute_weekly_snapshot(WEEK_START)["engagement"]
        self.assertEqual(m["wau"], 1)
        self.assertEqual(m["new_active"], 1)
        self.assertEqual(m["pwa_active"], 1)
        # The out-of-week user last seen 2026-05-01 is dormant.
        self.assertEqual(m["dormant"], 1)

    def test_pwa_active_keys_on_visit_not_sticky_flag(self):
        # A user who installed the PWA long ago (is_pwa_user stays True forever)
        # but whose last PWA visit predates the week must NOT count as PWA-active,
        # even though they were active via a normal browser this week.
        stale = User.objects.create_user(
            username="stalepwa@example.com",
            email="stalepwa@example.com",
            password="x",
            date_joined=_aware(date(2026, 5, 1)),
        )
        UserActivity.objects.create(
            user=stale,
            last_seen=_aware(date(2026, 6, 11)),  # active this week (browser)
            is_pwa_user=True,
            last_pwa_visit=_aware(date(2026, 5, 20)),  # but PWA visit is pre-week
        )
        m = compute_weekly_snapshot(WEEK_START)["engagement"]
        # Only the in-week user's PWA visit falls inside the window.
        self.assertEqual(m["pwa_active"], 1)
        # Sanity: the stale user still counts as active this week (browser visit).
        self.assertEqual(m["wau"], 2)


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


class EventRegistrationAdminPaymentDateTests(TestCase):
    """Guard the admin path that feeds the 'paid event registrations' KPI.

    The KPI windows confirmed registrations on payment_date, so the admin must
    stamp that timestamp whenever staff flip payment_confirmed (including via the
    changelist's list_editable checkbox, which routes through save_model).
    """

    def _admin_and_request(self):
        from django.contrib.admin.sites import AdminSite
        from django.test import RequestFactory

        from crush_lu.admin.events import EventRegistrationAdmin
        from crush_lu.models import EventRegistration

        request = RequestFactory().post("/admin/")
        request.user = self.staff
        return EventRegistrationAdmin(EventRegistration, AdminSite()), request

    class _FakeForm:
        def __init__(self, changed_data):
            self.changed_data = changed_data

    def setUp(self):
        from crush_lu.models import EventRegistration, MeetupEvent

        self.staff = User.objects.create_user(
            username="staff@example.com", email="staff@example.com", password="x"
        )
        attendee = User.objects.create_user(
            username="attendee@example.com", email="attendee@example.com", password="x"
        )
        event = MeetupEvent.objects.create(
            title="Paid Night",
            description="event",
            date_time=timezone.now() + timezone.timedelta(days=7),
            registration_deadline=timezone.now() + timezone.timedelta(days=5),
            location="Luxembourg",
            address="1 Test St",
            max_participants=20,
        )
        self.reg = EventRegistration.objects.create(
            user=attendee, event=event, status="pending"
        )

    def test_confirming_payment_stamps_payment_date(self):
        admin_obj, request = self._admin_and_request()
        self.assertIsNone(self.reg.payment_date)
        self.reg.payment_confirmed = True
        admin_obj.save_model(
            request, self.reg, self._FakeForm(["payment_confirmed"]), change=True
        )
        self.reg.refresh_from_db()
        self.assertIsNotNone(self.reg.payment_date)

    def test_unconfirming_payment_clears_payment_date(self):
        self.reg.payment_confirmed = True
        self.reg.payment_date = timezone.now()
        self.reg.save()
        admin_obj, request = self._admin_and_request()
        self.reg.payment_confirmed = False
        admin_obj.save_model(
            request, self.reg, self._FakeForm(["payment_confirmed"]), change=True
        )
        self.reg.refresh_from_db()
        self.assertIsNone(self.reg.payment_date)
