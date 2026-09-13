"""
Which events can still verify a member at the door.

A pending member is verified in person by a coach at an event
(`services.profile_verification.claim_profile_verification`), so a booking
only counts while that event's door is still open. The coach "Unverified
profiles" page (`views_coach`) and the Crush-admin Action Center
(`admin.verification_queues`) both ask who is booked, and both answer from
here, so the page's "Booked on an event" chip and the Action Center's
"booked" count cannot disagree.
"""

from datetime import timedelta

from crush_lu.models import MeetupEvent
from crush_lu.models.events import SEAT_HOLDING_STATUSES

#: Registration statuses `coach_event_checkin` actually renders — its own
#: roster is `SEAT_HOLDING_STATUSES` plus the waitlist. "applied" is an
#: expression of interest and explicitly not a seat (models/events.py), and
#: "no_show" is recorded after the fact. Accepting everything-but-cancelled
#: sent a coach to a scanner where the member has no row at all.
DOOR_VISIBLE_REGISTRATION_STATUSES = tuple(SEAT_HOLDING_STATUSES) + ("waitlist",)


def live_or_future_event_ids(now):
    """Events a member can still be verified at: not started yet, or running.

    ``end_time`` is a Python property — ``timedelta * F()`` is unsupported on
    SQLite — so the precise check cannot live in the query. The bounded
    `live_lookback_cutoff` pre-filter keeps this to events that started within
    the duration ceiling plus everything still to come, and materialising
    their ids is what lets the coach page's `upcoming` filter and its rendered
    badge agree. They disagreed before: the filter admitted an event that had
    already ended while `_annotate_unverified_page` correctly dropped it, so
    the row showed up under "Booked on an event" with no event badge
    explaining why.
    """
    return [
        event_id
        for event_id, date_time, duration in MeetupEvent.objects.filter(
            date_time__gte=MeetupEvent.live_lookback_cutoff(now),
            # Cancelling an event flips this flag and leaves its registrations
            # alone, so filtering on time only kept sending coaches to a door
            # that is not happening.
            is_cancelled=False,
        ).values_list("id", "date_time", "duration_minutes")
        if date_time + timedelta(minutes=duration) >= now
    ]
