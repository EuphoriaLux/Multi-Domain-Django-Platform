"""
Post-event swiping (decision 2026-11-20, replaces "My Crush!" coach leads).

After an event ends, each verified attendee swipes through the other verified
attendees they are compatible with (gender preferences both ways). Every
decision is final and private. When two attendees like each other, the pair
gets an ``EventConnection`` with ``flow='swipe'`` and status ``shared``: both
are notified ("It's a match") and the existing connection page opens the chat.
Coaches see the matches of their events read-only — no coach step remains.

The window is the event's existing post-event connection window
(``end_time`` → ``end_time + connection_window_hours``).
"""

import logging
import random

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext as _

from crush_lu.models.connections import EventConnection
from crush_lu.models.event_swipes import EventSwipe
from crush_lu.models.events import EventRegistration

logger = logging.getLogger(__name__)


class SwipeNotAllowed(Exception):
    """The swipe was refused; ``str(exc)`` is safe to show the member."""


# ---------------------------------------------------------------------------
# Gates
# ---------------------------------------------------------------------------


def swipe_window_open(event, now=None) -> bool:
    """True between the event's scheduled end and its connection deadline."""
    now = now or timezone.now()
    return event.end_time <= now <= event.connection_window_deadline


def _registration(user, event):
    return (
        EventRegistration.objects.filter(event=event, user=user)
        .select_related("user__crushprofile")
        .first()
    )


def can_swipe(user, event) -> bool:
    """The member attended, is verified, and the window is open."""
    reg = _registration(user, event)
    return bool(reg and reg.can_make_connections and swipe_window_open(event))


# ---------------------------------------------------------------------------
# Compatibility
# ---------------------------------------------------------------------------


def _wants(viewer_profile, other_profile) -> bool:
    """Would ``viewer`` want to see ``other``?

    Uses ``preferred_genders`` when the viewer set it. Without a preference
    the default is the other gender (Tom's brief: "für das andere
    Geschlecht"); a viewer with neither gender nor preference sees everyone.
    """
    prefs = viewer_profile.preferred_genders or []
    other_gender = other_profile.gender or ""
    if prefs:
        return other_gender in prefs
    if not viewer_profile.gender:
        return True
    return other_gender != viewer_profile.gender


def is_compatible(profile_a, profile_b) -> bool:
    """Both sides would want to see each other."""
    return _wants(profile_a, profile_b) and _wants(profile_b, profile_a)


# ---------------------------------------------------------------------------
# Deck
# ---------------------------------------------------------------------------


def _hidden_ids(user) -> set[int]:
    from crush_lu.services.blocking import blocked_user_ids
    from crush_lu.services.event_lobby import hidden_encounter_user_ids

    return blocked_user_ids(user) | hidden_encounter_user_ids(user)


def _candidate_registrations(user, event):
    """Verified attendees of ``event`` other than ``user``, not hidden."""
    return (
        EventRegistration.objects.filter(
            event=event,
            status="attended",
            user__crushprofile__verification_status="verified",
        )
        .exclude(user=user)
        .exclude(user_id__in=_hidden_ids(user))
        .select_related("user__crushprofile")
    )


def swipe_deck(user, event) -> list[dict]:
    """Cards still to swipe, in a stable per-member shuffled order.

    Each card is ``{"user": User, "profile": CrushProfile}``. The order is
    shuffled with a seed of (event, viewer) so a reload shows the same next
    card, while two members never see the room in the same order.
    """
    own_profile = getattr(user, "crushprofile", None)
    if own_profile is None:
        return []
    swiped_ids = set(
        EventSwipe.objects.filter(event=event, swiper=user).values_list(
            "target_id", flat=True
        )
    )
    registrations = _candidate_registrations(user, event).prefetch_related(
        "user__crushprofile__interests_new",
        "user__crushprofile__photo_review_states",
        "user__crushprofile__published_photos",
        "user__crushprofile__photo_framings",
    )
    cards = [
        {"user": reg.user, "profile": reg.user.crushprofile}
        for reg in registrations.order_by("id")
        if reg.user_id not in swiped_ids
        and is_compatible(own_profile, reg.user.crushprofile)
    ]
    random.Random(f"{event.pk}:{user.pk}").shuffle(cards)
    return cards


def deck_size(user, event) -> int:
    """How many compatible attendees exist in total (swiped or not)."""
    own_profile = getattr(user, "crushprofile", None)
    if own_profile is None:
        return 0
    return sum(
        1
        for reg in _candidate_registrations(user, event)
        if is_compatible(own_profile, reg.user.crushprofile)
    )


# ---------------------------------------------------------------------------
# Swiping
# ---------------------------------------------------------------------------


def record_swipe(swiper, event, target, liked: bool, request=None) -> dict:
    """Record a final like/pass and create the match when it is mutual.

    Returns ``{"match": bool, "connection": EventConnection | None,
    "already_swiped": bool}``. Raises ``SwipeNotAllowed`` when the swiper or
    the target is not eligible.

    Both attendees' registration rows are locked in id order before the
    reverse like is read, so two simultaneous likes cannot both miss each
    other and leave a mutual pair without a match.
    """
    if swiper.pk == target.pk:
        raise SwipeNotAllowed(_("You cannot swipe on yourself."))
    if not swipe_window_open(event):
        raise SwipeNotAllowed(_("Swiping is closed for this event."))

    swiper_reg = _registration(swiper, event)
    if not (swiper_reg and swiper_reg.can_make_connections):
        raise SwipeNotAllowed(_("You must attend this event to swipe."))

    target_reg = _candidate_registrations(swiper, event).filter(user=target).first()
    if target_reg is None or not is_compatible(
        swiper.crushprofile, target_reg.user.crushprofile
    ):
        # One message for "not an attendee", "blocked" and "not compatible"
        # so the endpoint cannot be used to probe any of them.
        raise SwipeNotAllowed(_("This person is not available."))

    with transaction.atomic():
        list(
            EventRegistration.objects.select_for_update()
            .filter(pk__in=[swiper_reg.pk, target_reg.pk])
            .order_by("pk")
        )
        try:
            with transaction.atomic():
                EventSwipe.objects.create(
                    event=event, swiper=swiper, target=target, liked=liked
                )
        except IntegrityError:
            existing = EventSwipe.objects.get(event=event, swiper=swiper, target=target)
            connection = _swipe_connection(swiper, target, event)
            return {
                "match": bool(existing.liked and connection),
                "connection": connection,
                "already_swiped": True,
            }

        if not liked:
            return {"match": False, "connection": None, "already_swiped": False}

        reverse_like = EventSwipe.objects.filter(
            event=event, swiper=target, target=swiper, liked=True
        ).exists()
        if not reverse_like:
            return {"match": False, "connection": None, "already_swiped": False}

        connection = _create_match(first_liker=target, second_liker=swiper, event=event)

    transaction.on_commit(lambda: _notify_match(connection, request))
    return {"match": True, "connection": connection, "already_swiped": False}


def _swipe_connection(user_a, user_b, event):
    return (
        EventConnection.objects.filter(event=event, flow=EventConnection.FLOW_SWIPE)
        .filter(
            Q(requester=user_a, recipient=user_b)
            | Q(requester=user_b, recipient=user_a)
        )
        .first()
    )


def _create_match(*, first_liker, second_liker, event):
    """Create the pair's shared connection, or upgrade an existing row.

    An older row for the pair (a legacy request or a "My Crush!" lead from
    before the switch) is reused because ``(requester, recipient, event)`` is
    unique: it becomes a ``swipe`` match, which also takes an open crush lead
    out of the coach queue — both members have now said yes on their own.
    """
    now = timezone.now()
    connection = (
        EventConnection.objects.select_for_update()
        .filter(event=event)
        .filter(
            Q(requester=first_liker, recipient=second_liker)
            | Q(requester=second_liker, recipient=first_liker)
        )
        .first()
    )
    if connection is None:
        connection = EventConnection(
            requester=first_liker, recipient=second_liker, event=event
        )
        previous = None
    else:
        previous = f"{connection.flow}/{connection.status}"

    connection.flow = EventConnection.FLOW_SWIPE
    connection.status = "shared"
    connection.requester_consents_to_share = True
    connection.recipient_consents_to_share = True
    connection.responded_at = connection.responded_at or now
    connection.shared_at = connection.shared_at or now
    connection.log_system_action(
        "swipe_match", **({"replaced": previous} if previous else {})
    )
    connection.save()
    return connection


def _notify_match(connection, request=None):
    """'It's a match' to both members (push first, email fallback)."""
    from crush_lu.notification_service import notify_mutual_match

    for user, other in (
        (connection.requester, connection.recipient),
        (connection.recipient, connection.requester),
    ):
        try:
            notify_mutual_match(
                user=user, other_user=other, connection=connection, request=request
            )
        except Exception:
            logger.exception(
                "Swipe match notification failed for connection %s", connection.pk
            )


# ---------------------------------------------------------------------------
# Read models
# ---------------------------------------------------------------------------


def matches_for_user(user, event):
    """The member's swipe matches at ``event``, newest first."""
    return (
        EventConnection.objects.filter(event=event, flow=EventConnection.FLOW_SWIPE)
        .for_user(user)
        .select_related("requester__crushprofile", "recipient__crushprofile")
        .order_by("-shared_at")
    )


def coach_matches(coach, *, all_events=False):
    """Swipe matches a coach may see read-only: their events' (or all)."""
    qs = EventConnection.objects.filter(flow=EventConnection.FLOW_SWIPE)
    if not all_events:
        qs = qs.filter(event__coaches=coach)
    return (
        qs.select_related("event", "requester__crushprofile", "recipient__crushprofile")
        .order_by("-shared_at")
        .distinct()
    )


def event_swipe_stats(event) -> dict:
    """Counts for coach/admin views: swipes, likes, matches."""
    swipes = EventSwipe.objects.filter(event=event)
    return {
        "swipers": swipes.values("swiper").distinct().count(),
        "swipes": swipes.count(),
        "likes": swipes.filter(liked=True).count(),
        "matches": EventConnection.objects.filter(
            event=event, flow=EventConnection.FLOW_SWIPE
        ).count(),
    }
