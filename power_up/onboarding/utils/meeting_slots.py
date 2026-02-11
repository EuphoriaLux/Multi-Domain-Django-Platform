"""
Meeting slot generator — ported from dateSlotGenerator.ts.

Generates 30-minute meeting slots on Tuesdays and Thursdays,
morning (10:00–12:00) and afternoon (14:00–16:00).
"""

from datetime import date, datetime, time, timedelta


# Slot time windows (start hours and half-hours)
MORNING_SLOTS = [time(10, 0), time(10, 30), time(11, 0), time(11, 30)]
AFTERNOON_SLOTS = [time(14, 0), time(14, 30), time(15, 0), time(15, 30)]
ALL_SLOT_TIMES = MORNING_SLOTS + AFTERNOON_SLOTS

# Tuesday = 1, Thursday = 3 (Python weekday convention)
SLOT_DAYS = {1, 3}


def generate_meeting_slots(num_weeks=2):
    """
    Generate available meeting slot datetimes for the next *num_weeks* weeks.

    Only Tuesdays and Thursdays, with 30-min slots in the morning and afternoon.
    Starts from tomorrow.

    Returns a list of ``datetime`` objects (naive, local time).
    """
    tomorrow = date.today() + timedelta(days=1)
    end_date = tomorrow + timedelta(weeks=num_weeks)

    slots = []
    current = tomorrow
    while current < end_date:
        if current.weekday() in SLOT_DAYS:
            for t in ALL_SLOT_TIMES:
                slots.append(datetime.combine(current, t))
        current += timedelta(days=1)
    return slots


def group_slots_by_day(slots):
    """
    Group a flat list of slot datetimes into a dict keyed by date string.

    Returns::

        {
            "2026-02-12": {
                "date": date(2026, 2, 12),
                "morning": [datetime(..., 10, 0), ...],
                "afternoon": [datetime(..., 14, 0), ...],
            },
            ...
        }
    """
    grouped = {}
    for slot in slots:
        key = slot.strftime("%Y-%m-%d")
        if key not in grouped:
            grouped[key] = {"date": slot.date(), "morning": [], "afternoon": []}
        if slot.time() < time(12, 0):
            grouped[key]["morning"].append(slot)
        else:
            grouped[key]["afternoon"].append(slot)
    return grouped


def format_slot(slot):
    """Format a single slot as 'HH:MM – HH:MM' (30 min duration)."""
    end = (datetime.combine(date.today(), slot.time()) + timedelta(minutes=30)).time()
    return f"{slot.strftime('%H:%M')} – {end.strftime('%H:%M')}"
