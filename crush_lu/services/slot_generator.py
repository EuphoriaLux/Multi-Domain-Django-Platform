"""Virtual screening-slot generator.

Given a coach, produces bookable 30-minute slot candidates over the next
N days based on their availability_windows (JSONField on CrushCoach).
Used by the Phase 5 booking view so hybrid-mode coaches don't have to
manually create every slot — they declare weekly windows once and the
system materialises the next 2 weeks of bookable slots on demand.

The returned dicts look like actual ScreeningSlot records so the
template can render real + virtual slots in a single list. Virtual
slots have `id=None` until the user actually books — at that moment
the booking view creates a real ScreeningSlot row inside the claim
transaction.
"""
from datetime import date, datetime, time, timedelta

from django.utils import timezone


WEEKDAY_BY_NAME = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}

SLOT_DURATION = timedelta(minutes=30)


def _parse_hhmm(value: str) -> time:
    h, m = value.split(":")
    return time(int(h), int(m))


def _is_coach_away(coach, when):
    if not coach.is_away:
        return False
    if coach.away_until is None:
        return True
    return when < coach.away_until


def virtual_slots_for(coach, days: int = 14):
    """Yield candidate ScreeningSlot-like dicts for the next `days` days.

    Skips coaches that aren't opted in or are fully away. Excludes slots
    that are already covered by a real ScreeningSlot for the same coach
    (any non-cancelled status) so we never double-book.
    """
    from crush_lu.models import ScreeningSlot  # local import avoids app-loading cycle

    if not coach.hybrid_features_enabled:
        return []

    windows = coach.availability_windows or []
    if not windows and coach.working_mode != "booking":
        return []

    now = timezone.now()
    horizon_end = now + timedelta(days=days)

    # Pull all non-cancelled slots in the window for overlap exclusion.
    existing = list(
        ScreeningSlot.objects.filter(
            coach=coach,
            start_at__gte=now,
            start_at__lt=horizon_end,
        ).exclude(status__in=("cancelled", "no_show"))
    )

    def overlaps_existing(start_at, end_at):
        for s in existing:
            if s.start_at < end_at and s.end_at > start_at:
                return True
        return False

    tz = timezone.get_current_timezone()
    today = date.fromordinal(now.astimezone(tz).toordinal())

    virtual = []
    for day_offset in range(days):
        target_date = today + timedelta(days=day_offset)
        weekday = target_date.weekday()
        for window in windows:
            if WEEKDAY_BY_NAME.get((window.get("day") or "").lower()) != weekday:
                continue
            try:
                w_start = _parse_hhmm(window["start"])
                w_end = _parse_hhmm(window["end"])
            except (KeyError, ValueError):
                continue

            window_start_naive = datetime.combine(target_date, w_start)
            window_end_naive = datetime.combine(target_date, w_end)
            window_start = timezone.make_aware(window_start_naive, tz)
            window_end = timezone.make_aware(window_end_naive, tz)

            cursor = window_start
            while cursor + SLOT_DURATION <= window_end:
                slot_end = cursor + SLOT_DURATION
                if cursor <= now:
                    cursor = slot_end
                    continue
                if _is_coach_away(coach, cursor):
                    cursor = slot_end
                    continue
                if overlaps_existing(cursor, slot_end):
                    cursor = slot_end
                    continue
                virtual.append(
                    {
                        "id": None,
                        "coach_id": coach.id,
                        "start_at": cursor,
                        "end_at": slot_end,
                        "label": window.get("label", ""),
                    }
                )
                cursor = slot_end

    return virtual


def bookable_slots(coach, days: int = 14):
    """Return real + virtual slots merged and sorted by start_at.

    Real slots are concrete ScreeningSlot rows with status='available'
    (booking-mode coaches pre-create these). Virtual slots are generated
    on the fly from availability_windows (hybrid-mode). Both shapes are
    dicts with the same keys so the template doesn't need to branch.
    """
    from crush_lu.models import ScreeningSlot

    if not coach.hybrid_features_enabled:
        return []

    now = timezone.now()
    horizon_end = now + timedelta(days=days)

    real = [
        {
            "id": s.id,
            "coach_id": s.coach_id,
            "start_at": s.start_at,
            "end_at": s.end_at,
            "label": "",
        }
        for s in ScreeningSlot.objects.filter(
            coach=coach,
            status="available",
            start_at__gte=now,
            start_at__lt=horizon_end,
        ).order_by("start_at")
    ]

    virtual = virtual_slots_for(coach, days=days)
    merged = real + virtual
    merged.sort(key=lambda s: s["start_at"])
    return merged
