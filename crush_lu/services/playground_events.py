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

``title`` and ``description`` are translated fields (modeltranslation). A plain
``title=`` lookup reads only the active language's column and a plain
``title=`` write fills only that column, so the mixer is written to and looked
up in every language column explicitly. Before that, a coach who opened the
playground in another language got a second mixer (local test 2026-11-15:
ai-memory-hub/reviews/2026-11-15-coach-playground-local-test.md).
"""

from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from crush_lu.services.playground_practice import PlaygroundError
from crush_lu.services.staging_playground import require_playground

DURATION_MINUTES = 120
LEAD_MINUTES = 60
ROLL_FORWARD_BELOW_MINUTES = 20
DESCRIPTION = (
    "Practice mixer on test.crush.lu for the coach playground. Nothing here is real."
)


#: Every practice mixer's title starts with this, archived ones included.
TITLE_PREFIX = "Practice mixer (coach "


def mixer_title(coach_user):
    return f"{TITLE_PREFIX}{coach_user.pk})"


def is_practice_mixer(event):
    """True for a playground practice mixer (live or archived), in any language.

    echo.lu and Google Indexing ask this before they list an event: a practice
    mixer is published so the real register and door pages work, but it must
    never reach a public listing, even if a slot that runs the playground
    switches either integration on.
    """
    for lang in _languages():
        title = getattr(event, f"title_{lang}", None)
        if isinstance(title, str) and title.startswith(TITLE_PREFIX):
            return True
    return False


def exclude_practice_mixers(queryset):
    """``queryset`` without practice mixers (any language column)."""
    for lang in _languages():
        queryset = queryset.exclude(**{f"title_{lang}__startswith": TITLE_PREFIX})
    return queryset


def _languages():
    return tuple(getattr(settings, "MODELTRANSLATION_LANGUAGES", ("en",)))


def _in_every_language(field, value):
    """``{"title_en": value, "title_de": value, ...}`` for a translated field."""
    return {f"{field}_{lang}": value for lang in _languages()}


def _practice_mixers(coach_user):
    """The coach's published practice mixers, newest first, in any language."""
    from crush_lu.models.events import MeetupEvent

    title = mixer_title(coach_user)
    matches_title = Q()
    for column in _in_every_language("title", title):
        matches_title |= Q(**{column: title})
    return MeetupEvent.objects.filter(matches_title, is_published=True).order_by("-pk")


def practice_mixer(coach_user):
    return _practice_mixers(coach_user).first()


def _archive(event):
    """Unpublish ``event`` and rename it in every language. Nothing is deleted."""
    stamp = timezone.localtime().strftime("%Y-%m-%d %H:%M")
    base = next(
        (
            value
            for value in (getattr(event, f"title_{lang}") for lang in _languages())
            if value
        ),
        "Practice mixer",
    )
    titles = _in_every_language("title", f"{base} archived {stamp}"[:200])
    for column, value in titles.items():
        setattr(event, column, value)
    event.is_published = False
    event.save(update_fields=[*titles, "is_published"])


def _same_in_every_language(event, coach_user):
    """Fill any language column a per-language mixer left empty."""
    wanted = {
        **_in_every_language("title", mixer_title(coach_user)),
        **_in_every_language("description", DESCRIPTION),
    }
    if all(getattr(event, column) == value for column, value in wanted.items()):
        return
    for column, value in wanted.items():
        setattr(event, column, value)
    event.save(update_fields=list(wanted))


def ensure_practice_mixer(coach_user):
    """Return the coach's practice mixer, creating it if needed.

    A mixer that has not started yet is kept at least 20 minutes ahead, so
    registration never closes under a coach. One that has started (or was
    ended on purpose) is left alone until the coach asks for a fresh one.
    Duplicates left by the old per-language lookup are archived, keeping the
    newest.
    """
    from crush_lu.models.events import MeetupEvent

    require_playground()
    now = timezone.now()
    mixers = list(_practice_mixers(coach_user))
    for duplicate in mixers[1:]:
        _archive(duplicate)
    event = mixers[0] if mixers else None
    if event is None:
        start = now + timedelta(minutes=LEAD_MINUTES)
        return MeetupEvent.objects.create(
            **_in_every_language("title", mixer_title(coach_user)),
            **_in_every_language("description", DESCRIPTION),
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
    _same_in_every_language(event, coach_user)
    lead = event.date_time - now
    if timedelta(0) < lead < timedelta(minutes=ROLL_FORWARD_BELOW_MINUTES):
        start = now + timedelta(minutes=LEAD_MINUTES)
        event.date_time = start
        event.registration_deadline = start
        event.save(update_fields=["date_time", "registration_deadline"])
    return event


def upcoming_practice_mixer(coach_user):
    """The coach's mixer if it has not started yet, else a fresh one.

    For scenarios that hand the practice member a ticket: after "end the
    mixer now" the old one is over, and a ticket for it would open the Lobby
    straight on its recap.
    """
    event = ensure_practice_mixer(coach_user)
    if event.date_time <= timezone.now():
        event = fresh_practice_mixer(coach_user)
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
    for event in _practice_mixers(coach_user):
        _archive(event)
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
