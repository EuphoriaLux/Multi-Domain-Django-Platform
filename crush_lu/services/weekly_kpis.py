"""
Weekly KPI computation for Crush.lu.

``compute_weekly_snapshot(week_start)`` runs the funnel / engagement / revenue /
matching aggregations for a single ISO week and returns a JSON-serializable dict.
``upsert_snapshot(week_start)`` persists that dict on a
:class:`~crush_lu.models.metrics.WeeklyMetricsSnapshot` (idempotent per week).

The query patterns mirror those proven in the ``business_plan_metrics``
management command — same date-window filters, ``Count``/``Avg``/``Exists``
aggregations — but framed as *new-in-the-week* counts plus a few cumulative
totals, which is what week-over-week tracking needs.
"""
from __future__ import annotations

from datetime import date, timedelta

from django.contrib.auth.models import User
from django.db.models import Avg, Count, ExpressionWrapper, F, FloatField, Q

# ISO weeks run Monday (1) .. Sunday (7).
_WEEK_LENGTH = timedelta(days=7)


def last_completed_week_start(today: date) -> date:
    """Return the Monday of the most recently *completed* ISO week.

    Given any day, walk back to this week's Monday, then back one more week —
    so a Monday-morning job reports the week that just ended.
    """
    this_monday = today - timedelta(days=today.weekday())
    return this_monday - _WEEK_LENGTH


def _pct(numerator: int, denominator: int) -> float:
    """Percentage rounded to 1 decimal, 0.0 when the denominator is 0."""
    if not denominator:
        return 0.0
    return round(numerator * 100.0 / denominator, 1)


def compute_weekly_snapshot(week_start: date) -> dict:
    """Compute the full KPI payload for the ISO week beginning ``week_start``.

    ``week_start`` must be a Monday. The window is the inclusive date range
    ``[week_start, week_start + 6 days]`` (Mon..Sun), filtered with ``__date``
    lookups so it stays timezone-correct under the project's current TZ.
    """
    # Imported here (not at module load) to avoid import-time coupling between
    # the services package and the large models package.
    from crush_lu.models import CrushProfile, ProfileSubmission
    from crush_lu.models.connections import EventConnection
    from crush_lu.models.crush_connect import (
        CrushConnectMembership,
        CrushConnectWaitlist,
    )
    from crush_lu.models.events import EventRegistration, MeetupEvent
    from crush_lu.models.profiles import PremiumMembership, UserActivity
    from crush_lu.models.referrals import ReferralAttribution

    week_end = week_start + timedelta(days=6)  # Sunday, inclusive

    def in_week(qs, field):
        return qs.filter(
            **{f"{field}__date__gte": week_start, f"{field}__date__lte": week_end}
        )

    # ── Acquisition funnel (new in the week) ─────────────────────────
    new_signups = in_week(User.objects.all(), "date_joined").count()
    new_profiles = in_week(CrushProfile.objects.all(), "created_at").count()
    phone_verifications = in_week(
        CrushProfile.objects.filter(phone_verified=True), "phone_verified_at"
    ).count()
    profiles_submitted = in_week(
        ProfileSubmission.objects.all(), "submitted_at"
    ).count()
    profiles_verified = in_week(
        CrushProfile.objects.filter(verification_status="verified"), "approved_at"
    ).count()

    acquisition = {
        "new_signups": new_signups,
        "new_profiles": new_profiles,
        "phone_verifications": phone_verifications,
        "profiles_submitted": profiles_submitted,
        "profiles_verified": profiles_verified,
        # Rough week conversion (not a strict cohort — signups and verifications
        # in the same week are different people; useful as a trend, not a truth).
        "signup_to_verified_pct": _pct(profiles_verified, new_signups),
        "submitted_to_verified_pct": _pct(profiles_verified, profiles_submitted),
        # Cumulative position at the end of the week.
        "cumulative_total_users": User.objects.filter(
            date_joined__date__lte=week_end
        ).count(),
        # Count every verified member as of week_end. Legacy profiles verified
        # before approved_at was tracked have a NULL timestamp — include them so
        # the running total isn't silently undercounted (5 such rows in prod as
        # of 2026-06). Weekly "profiles_verified" still keys on approved_at, so
        # these undated legacy verifications aren't misattributed to any week.
        "cumulative_verified_members": CrushProfile.objects.filter(
            verification_status="verified"
        )
        .filter(Q(approved_at__date__lte=week_end) | Q(approved_at__isnull=True))
        .count(),
    }

    # ── Engagement / retention ───────────────────────────────────────
    active_qs = in_week(UserActivity.objects.all(), "last_seen")
    wau = active_qs.count()
    new_active = active_qs.filter(first_seen__date__gte=week_start).count()
    engagement = {
        "wau": wau,
        "new_active": new_active,
        "returning_active": active_qs.filter(
            first_seen__date__lt=week_start
        ).count(),
        # PWA-active = users whose most recent PWA visit landed in this week.
        # is_pwa_user is sticky ("ever used the installed PWA"), so keying on it
        # would count browser-only visitors who installed the PWA long ago; the
        # activity middleware stamps last_pwa_visit per real PWA request, so
        # window on that instead (its NULLs for non-PWA users drop out for free).
        "pwa_active": in_week(
            UserActivity.objects.all(), "last_pwa_visit"
        ).count(),
        # Tracked users who went quiet: last seen before the week even started.
        "dormant": UserActivity.objects.filter(last_seen__date__lt=week_start).count(),
    }

    # ── Revenue / premium ────────────────────────────────────────────
    revenue = {
        "new_premium": in_week(
            PremiumMembership.objects.filter(payment_confirmed=True), "payment_date"
        ).count(),
        "total_active_premium": PremiumMembership.objects.filter(
            status="active"
        ).count(),
        "new_connect_optins": in_week(
            CrushConnectMembership.objects.all(), "onboarded_at"
        ).count(),
        "total_connect_onboarded": CrushConnectMembership.objects.filter(
            onboarded_at__isnull=False
        ).count(),
        "waitlist_new": in_week(
            CrushConnectWaitlist.objects.all(), "joined_at"
        ).count(),
        "waitlist_total": CrushConnectWaitlist.objects.count(),
        "paid_event_registrations": in_week(
            EventRegistration.objects.filter(payment_confirmed=True), "payment_date"
        ).count(),
    }

    # ── Matching & events ────────────────────────────────────────────
    events_held_qs = in_week(
        MeetupEvent.objects.filter(is_published=True, is_cancelled=False),
        "date_time",
    )
    fill_data = events_held_qs.filter(max_participants__gt=0).annotate(
        confirmed_count=Count(
            "eventregistration",
            filter=Q(eventregistration__status__in=["confirmed", "attended"]),
        )
    ).annotate(
        fill_pct=ExpressionWrapper(
            F("confirmed_count") * 100.0 / F("max_participants"),
            output_field=FloatField(),
        )
    )
    avg_fill = fill_data.aggregate(avg=Avg("fill_pct"))["avg"]

    matching_events = {
        "events_held": events_held_qs.count(),
        "registrations": in_week(
            EventRegistration.objects.all(), "registered_at"
        ).count(),
        "attended": in_week(
            EventRegistration.objects.filter(status="attended"), "checked_in_at"
        ).count(),
        "no_show": EventRegistration.objects.filter(
            status="no_show",
            event__date_time__date__gte=week_start,
            event__date_time__date__lte=week_end,
        ).count(),
        "avg_fill_rate_pct": round(avg_fill, 1) if avg_fill is not None else 0.0,
        "connections_requested": in_week(
            EventConnection.objects.all(), "requested_at"
        ).count(),
        "connections_shared": in_week(
            EventConnection.objects.filter(shared_at__isnull=False), "shared_at"
        ).count(),
        "referrals_converted": in_week(
            ReferralAttribution.objects.filter(status="converted"), "converted_at"
        ).count(),
    }

    return {
        "acquisition": acquisition,
        "engagement": engagement,
        "revenue": revenue,
        "matching_events": matching_events,
    }


def upsert_snapshot(week_start: date):
    """Compute and persist the snapshot for ``week_start`` (idempotent).

    Returns the ``(snapshot, created)`` tuple from ``update_or_create`` so a
    re-run for the same week overwrites its row instead of duplicating it.
    """
    from crush_lu.models import WeeklyMetricsSnapshot

    metrics = compute_weekly_snapshot(week_start)
    return WeeklyMetricsSnapshot.objects.update_or_create(
        week_start=week_start,
        defaults={
            "week_end": week_start + timedelta(days=6),
            "metrics": metrics,
        },
    )


def compute_deltas(current: dict, previous: dict | None) -> dict:
    """Return a dict mirroring ``current`` with the numeric delta vs ``previous``.

    Leaves that are numbers become ``current - previous`` (or ``None`` when there
    is no previous value to compare against); nested dicts recurse. Used by the
    email/admin layer so deltas are derived from persisted snapshots rather than
    stored redundantly.
    """
    deltas: dict = {}
    for key, value in current.items():
        if isinstance(value, dict):
            deltas[key] = compute_deltas(value, (previous or {}).get(key))
        elif isinstance(value, (int, float)):
            prev = (previous or {}).get(key) if previous else None
            deltas[key] = round(value - prev, 1) if isinstance(prev, (int, float)) else None
        else:
            deltas[key] = None
    return deltas


def snapshot_with_deltas(week_start: date) -> dict:
    """Load the persisted snapshot for ``week_start`` plus deltas vs the prior week.

    Returns ``{"week_start", "week_end", "metrics", "deltas", "previous_week_start"}``.
    Raises ``WeeklyMetricsSnapshot.DoesNotExist`` if the week was never computed.
    """
    from crush_lu.models import WeeklyMetricsSnapshot

    current = WeeklyMetricsSnapshot.objects.get(week_start=week_start)
    previous = (
        WeeklyMetricsSnapshot.objects.filter(week_start__lt=week_start)
        .order_by("-week_start")
        .first()
    )
    return {
        "week_start": current.week_start,
        "week_end": current.week_end,
        "metrics": current.metrics,
        "deltas": compute_deltas(current.metrics, previous.metrics if previous else None),
        "previous_week_start": previous.week_start if previous else None,
    }
