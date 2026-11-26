"""Close what is still waiting for a seat once an event is over.

Nothing used to move a ``waitlist`` row on after its event: promotion stops at
the start (``MeetupEvent.accepts_waitlist_promotion``), so the row read
"Waitlist" forever, on the coach's member overview months later and on the
member's own event page ("We'll email you if a spot opens up"). Curated
speed dating had the same gap: an ``applied`` application nobody closed stayed
"applied" after the evening.

Once an event has ended:

- ``waitlist`` becomes ``waitlist_closed`` (no seat ever came free);
- ``applied`` becomes ``not_selected``, the status the curated selection
  already uses for "no place given". **No notice is sent**: telling someone
  after the evening that they were not picked for it helps nobody.

``.update()`` on purpose, like ``services.no_shows``: neither status holds a
seat, so there is nothing for the save hooks or signals to do (no promotion,
no ticket, no wallet card, no group seat), and the event is over, so no
curated group can still change.

The automatic pass (hourly, riding the ``EventRecaps`` timer) only looks at
events that ended within ``AUTO_LOOKBACK``. Older rows are the backlog from
before this existed and are closed by hand with
``manage.py close_finished_waitlists --all-past``, so a production write of
that size is run by a person who has looked at the dry run first.
"""

import logging
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

#: How far back the automatic pass reaches. Wide enough that a timer outage
#: of several days still gets caught up; the backlog is a manual job.
AUTO_LOOKBACK = timedelta(days=14)

#: Still-waiting status -> what it becomes once the event is over.
CLOSING_TRANSITIONS = {
    "waitlist": "waitlist_closed",
    "applied": "not_selected",
}


def finished_event_ids(now=None, lookback=AUTO_LOOKBACK):
    """Events that have ended (``end_time <= now``), within ``lookback``.

    ``lookback=None`` means every finished event. ``end_time`` is
    ``date_time + duration_minutes``, a Python property, so the start-time
    filter narrows the query and the end time is checked per event.
    """
    from crush_lu.models.events import EventRegistration, MeetupEvent

    now = now or timezone.now()
    events = MeetupEvent.objects.filter(
        date_time__lte=now,
        pk__in=EventRegistration.objects.filter(
            status__in=tuple(CLOSING_TRANSITIONS)
        ).values("event_id"),
    )
    if lookback is not None:
        # An event may run for days (multi-day formats), so look back from the
        # start by the lookback plus the longest duration we could miss.
        events = events.filter(date_time__gte=now - lookback - timedelta(days=7))
    ids = []
    for event in events.only("pk", "date_time", "duration_minutes"):
        end = event.end_time
        if end > now:
            continue
        if lookback is not None and end < now - lookback:
            continue
        ids.append(event.pk)
    return ids


def still_waiting(event_ids):
    """Registrations on ``event_ids`` that are still waiting for a seat."""
    from crush_lu.models.events import EventRegistration

    return EventRegistration.objects.filter(
        event_id__in=event_ids, status__in=tuple(CLOSING_TRANSITIONS)
    )


def close_finished_waitlists(now=None, lookback=AUTO_LOOKBACK, dry_run=False):
    """Close every still-waiting registration on events that have ended.

    Returns ``{"events": n, "waitlist_closed": n, "not_selected": n}``. With
    ``dry_run`` the counts are what *would* change and nothing is written.
    """
    now = now or timezone.now()
    event_ids = finished_event_ids(now=now, lookback=lookback)
    counts = {"events": len(event_ids)}
    if not event_ids:
        counts.update({new: 0 for new in CLOSING_TRANSITIONS.values()})
        return counts

    with transaction.atomic():
        for old, new in CLOSING_TRANSITIONS.items():
            rows = still_waiting(event_ids).filter(status=old)
            if dry_run:
                counts[new] = rows.count()
            else:
                # .update() skips auto_now: stamp updated_at by hand, it is the
                # only record of when the wait was closed.
                counts[new] = rows.update(status=new, updated_at=now)

    if not dry_run and (counts["waitlist_closed"] or counts["not_selected"]):
        logger.info(
            "Closed finished waitlists on %d event(s): %d waitlist_closed, "
            "%d not_selected",
            counts["events"],
            counts["waitlist_closed"],
            counts["not_selected"],
        )
    return counts
