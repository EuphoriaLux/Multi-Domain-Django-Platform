"""One practice mixer per coach on test.crush.lu (coach playground).

Spec: ai-memory-hub/specs/2026-11-13-crush-coach-playground.md

The practice member registers on the real event page, the coach scans the
ticket on the real door page, and the Event Lobby, recap and My Crush follow
from the real code. The playground only sets the stage:

* the mixer starts an hour ahead, so registration stays open (registration
  needs ``now < date_time``); the Lobby counts as live before the start and
  check-in is open within 12 hours of it;
* the coach is not on the event, so "fill the room" assigns no coach to the
  cast, while the real door scan still makes the scanning coach the practice
  member's coach;
* "end the mixer now" moves the start back, which opens the recap and the
  48-hour My Crush window;
* "fresh mixer" archives the old event and creates a new one. Nothing is
  deleted.
"""

from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from crush_lu.services.playground_practice import PlaygroundError
from crush_lu.services.staging_playground import require_playground

DURATION_MINUTES = 120
LEAD_MINUTES = 60
ROLL_FORWARD_BELOW_MINUTES = 20


def mixer_title(coach_user):
    return f"Practice mixer (coach {coach_user.pk})"


def practice_mixer(coach_user):
    from crush_lu.models.events import MeetupEvent

    return (
        MeetupEvent.objects.filter(title=mixer_title(coach_user), is_published=True)
        .order_by("-pk")
        .first()
    )


def ensure_practice_mixer(coach_user):
    """Return the coach's practice mixer, creating it if needed.

    A mixer that has not started yet is kept at least 20 minutes ahead, so
    registration never closes under a coach. One that has started (or was
    ended on purpose) is left alone until the coach asks for a fresh one.
    """
    from crush_lu.models.events import MeetupEvent

    require_playground()
    now = timezone.now()
    event = practice_mixer(coach_user)
    if event is None:
        start = now + timedelta(minutes=LEAD_MINUTES)
        return MeetupEvent.objects.create(
            title=mixer_title(coach_user),
            description=(
                "Practice mixer on test.crush.lu for the coach playground. "
                "Nothing here is real."
            ),
            event_type="mixer",
            date_time=start,
            duration_minutes=DURATION_MINUTES,
            location="test.crush.lu",
            address="Practice venue, Luxembourg",
            max_participants=40,
            registration_deadline=start,
            registration_fee=0,
            profile_requirement="profile_exists",
            is_published=True,
        )
    lead = event.date_time - now
    if timedelta(0) < lead < timedelta(minutes=ROLL_FORWARD_BELOW_MINUTES):
        start = now + timedelta(minutes=LEAD_MINUTES)
        event.date_time = start
        event.registration_deadline = start
        event.save(update_fields=["date_time", "registration_deadline"])
    return event


def end_practice_mixer(coach_user):
    """End the mixer now: the Lobby moves to its recap, My Crush opens."""
    require_playground()
    event = practice_mixer(coach_user)
    if event is None:
        raise PlaygroundError("no_mixer")
    now = timezone.now()
    event.date_time = now - timedelta(minutes=(event.duration_minutes or 0) + 5)
    event.registration_deadline = event.date_time
    event.save(update_fields=["date_time", "registration_deadline"])
    return event


def fresh_practice_mixer(coach_user):
    """Archive the current mixer (unpublished, renamed) and start a new one."""
    require_playground()
    event = practice_mixer(coach_user)
    if event is not None:
        stamp = timezone.localtime().strftime("%Y-%m-%d %H:%M")
        event.title = f"{event.title} archived {stamp}"[:200]
        event.is_published = False
        event.save(update_fields=["title", "is_published"])
    return ensure_practice_mixer(coach_user)


def fill_the_room(coach_user):
    """Seat the whole cast as attended guests and let them into the Lobby.

    No scanner and no event coach, so no coach is assigned to the cast.
    Returns how many cast members are now in the room.
    """
    from crush_lu.models.events import EventRegistration
    from crush_lu.services.event_lobby import evaluate_participation
    from crush_lu.services.playground_cast import cast_queryset

    require_playground()
    event = practice_mixer(coach_user)
    if event is None:
        raise PlaygroundError("no_mixer")
    cast = list(cast_queryset())
    if not cast:
        raise PlaygroundError("no_cast")
    seated = 0
    for member in cast:
        with transaction.atomic():
            registration, _created = EventRegistration.objects.get_or_create(
                event=event,
                user=member,
                defaults={"status": "confirmed"},
            )
            if registration.status != "attended":
                registration.status = "attended"
                registration.save(update_fields=["status"])
        evaluate_participation(registration, source="checkin")
        seated += 1
    return seated
