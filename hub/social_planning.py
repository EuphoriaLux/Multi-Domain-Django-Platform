"""Posting proposals and review fingerprints; proposals never dispatch to Buffer."""

import hashlib
import json
from datetime import date, datetime, time, timedelta, timezone as datetime_timezone
from zoneinfo import ZoneInfo

from django.utils import timezone

LUXEMBOURG = ZoneInfo("Europe/Luxembourg")
REVIEW_FIELDS = (
    "content",
    "media_urls",
    "media_url",
    "platforms",
    "language",
    "scheduled_for",
    "source_metadata",
)


def review_fingerprint(post):
    payload = {field: getattr(post, field) for field in REVIEW_FIELDS}
    if post.scheduled_for:
        payload["scheduled_for"] = post.scheduled_for.astimezone(
            datetime_timezone.utc
        ).isoformat()
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode()
    ).hexdigest()


def source_deadline(post, metadata=None):
    if post.source_event_id:
        return post.source_event.date_time
    event_date = (post.source_metadata if metadata is None else metadata or {}).get(
        "event_date"
    )
    if event_date:
        # A date-only external event must be promoted before its day begins.
        return datetime.combine(date.fromisoformat(event_date), time(), LUXEMBOURG)
    return None


def reserved_times(exclude_id=None):
    from .models import SocialPost

    return list(
        SocialPost.objects.filter(
            status__in=["draft", "pending_review", "approved", "scheduled"],
            scheduled_for__gte=timezone.now(),
        )
        .exclude(pk=exclude_id)
        .values_list("scheduled_for", flat=True)
    )


def posting_proposal(
    post=None, *, posting_date=None, profiles=None, now=None, occupied=None
):
    now = now or timezone.now()
    deadline = source_deadline(post) if post else None
    metadata = (post.source_metadata or {}) if post else {}
    occupied = (
        reserved_times(post.pk if post else None) if occupied is None else occupied
    )
    base = {
        "timezone": "Europe/Luxembourg",
        "latest_before": deadline.isoformat() if deadline else None,
    }
    if deadline and deadline <= now:
        return {
            **base,
            "scheduled_for": None,
            "reason": "L'événement source est passé : révisez le contenu.",
        }
    if (
        post
        and post.scheduled_for
        and post.scheduled_for > now
        and (not deadline or post.scheduled_for < deadline)
    ):
        return {
            **base,
            "scheduled_for": post.scheduled_for.isoformat(),
            "reason": metadata.get("posting_reason")
            or "Horaire proposé pour cette publication.",
        }
    earliest = now + timedelta(hours=4)
    requested = posting_date or metadata.get("posting_date")
    first_day = earliest.astimezone(LUXEMBOURG).date()
    if requested:
        first_day = max(first_day, date.fromisoformat(requested))

    # Prefer a common configured slot for exactly one eligible account per network.
    profiles = profiles or []
    candidates = {
        service: [
            p
            for p in profiles
            if p.get("service") == service
            and not any(
                p.get(k) for k in ("is_queue_paused", "is_disconnected", "is_locked")
            )
        ]
        for service in ("instagram", "facebook")
    }
    selected = [items[0] for items in candidates.values() if len(items) == 1]
    for offset in range(21):
        day = first_day + timedelta(days=offset)
        common = None
        for profile in selected:
            slots = set()
            try:
                zone = ZoneInfo(profile.get("timezone") or "Europe/Luxembourg")
                # Channel dates can differ from the Luxembourg date.
                for delta in (-1, 0, 1):
                    channel_day = day + timedelta(days=delta)
                    weekday = channel_day.strftime("%a").lower()
                    for schedule in profile.get("posting_schedule", []):
                        if schedule.get("day") != weekday or schedule.get("paused"):
                            continue
                        for value in schedule.get("times", []):
                            slot = datetime.combine(
                                channel_day, time.fromisoformat(value), zone
                            )
                            if slot.astimezone(LUXEMBOURG).date() == day:
                                slots.add(slot.astimezone(LUXEMBOURG))
            except (ValueError, KeyError):
                slots = set()
            common = slots if common is None else common & slots
        options = sorted(common or [])
        fallback = datetime.combine(day, time(18, 30), LUXEMBOURG)
        for candidate in options + [fallback]:
            if candidate < earliest or (deadline and candidate >= deadline):
                continue
            if any(abs(candidate - booked) < timedelta(hours=1) for booked in occupied):
                continue
            reason = (
                "Créneau configuré dans Buffer."
                if candidate in options
                else "Créneau de départ à 18:30 au Luxembourg ; ajustable après vos premiers résultats."
            )
            return {**base, "scheduled_for": candidate.isoformat(), "reason": reason}
    return {
        **base,
        "scheduled_for": None,
        "reason": "Aucun créneau disponible avant la date limite du contenu.",
    }
