"""RFC 5545 calendar generation for screening-call bookings.

Small, dependency-free generator. Mirrors the pattern already used by
views_events.event_calendar_download for event tickets. We factor it out
here so Phase 5 booking confirmations can attach ICS invites without
pulling in a third-party library.
"""
from datetime import timezone as dt_timezone

from django.utils import timezone


def _ical_escape(text: str) -> str:
    """Escape text per RFC 5545 section 3.3.11."""
    return (
        text.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def _ical_fold(line: str) -> str:
    """Fold a content line to max 75 octets per RFC 5545 section 3.1."""
    encoded = line.encode("utf-8")
    if len(encoded) <= 75:
        return line
    parts = []
    first = True
    while encoded:
        limit = 75 if first else 74
        if len(encoded) <= limit:
            parts.append(encoded.decode("utf-8"))
            break
        cut = limit
        while cut > 0 and (encoded[cut] & 0xC0) == 0x80:
            cut -= 1
        parts.append(encoded[:cut].decode("utf-8"))
        encoded = encoded[cut:]
        first = False
    return "\r\n ".join(parts)


def generate_screening_ics(submission, slot, booking_url: str = "") -> bytes:
    """Build an ICS payload for a booked screening call.

    Returns raw bytes suitable for EmailMessage.attach(
        filename='screening.ics',
        content=<bytes>,
        mimetype='text/calendar; method=REQUEST',
    )
    """
    start_utc = slot.start_at.astimezone(dt_timezone.utc)
    end_utc = slot.end_at.astimezone(dt_timezone.utc)
    dtstart = start_utc.strftime("%Y%m%dT%H%M%SZ")
    dtend = end_utc.strftime("%Y%m%dT%H%M%SZ")
    dtstamp = timezone.now().astimezone(dt_timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    coach = slot.coach
    coach_name = (coach.user.get_full_name() or coach.user.username) if coach else ""

    summary = f"Crush.lu screening call with {coach_name}".strip()
    description_parts = [
        "Your Crush.lu screening call.",
        f"Coach: {coach_name}" if coach_name else "",
    ]
    if booking_url:
        description_parts.append(f"Manage: {booking_url}")
    description = _ical_escape("\n".join(p for p in description_parts if p))

    uid = f"screening-slot-{slot.pk}@crush.lu"
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Crush.lu//Screening Calls//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:REQUEST",
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{dtstamp}",
        f"DTSTART:{dtstart}",
        f"DTEND:{dtend}",
        _ical_fold(f"SUMMARY:{_ical_escape(summary)}"),
        _ical_fold(f"DESCRIPTION:{description}"),
    ]
    if booking_url:
        lines.append(_ical_fold(f"URL:{booking_url}"))
    lines += [
        "STATUS:CONFIRMED",
        "TRANSP:OPAQUE",
        "SEQUENCE:0",
        "END:VEVENT",
        "END:VCALENDAR",
    ]
    return ("\r\n".join(lines) + "\r\n").encode("utf-8")
