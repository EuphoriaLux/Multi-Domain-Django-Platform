"""
Post-event swiping (decision 2026-11-20, replaces "My Crush!" coach leads).

After an event ends, each verified attendee swipes through the other verified
attendees they are compatible with (gender preferences both ways). Every
decision is final and private. When two attendees like each other, the pair
gets an ``EventConnection`` with ``flow='swipe'`` and status ``shared``: both
are notified ("It's a match") and the existing connection page opens the chat.
Coaches see the matches of their events read-only — no coach step remains.

The window is the event's existing post-event connection window
(``end_time`` → ``end_time + connection_window_hours``). While a
``SwipeCampaign`` runs (December 2026 reactivation), every event that ended
before the campaign start is swipeable again until the campaign ends — in
"campaign mode", with extra rules: opted-out and on-break members are left
out, someone already swiped or matched at any event is not shown again, and
a person met at several events appears only in the newest shared one.
"""

import logging
import random

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _lazy

from crush_lu.models.connections import EventConnection
from crush_lu.models.event_swipes import (
    EventSwipe,
    PastEventDeckOptOut,
    SwipeCampaign,
)
from crush_lu.models.events import EventRegistration

logger = logging.getLogger(__name__)


class SwipeNotAllowed(Exception):
    """The swipe was refused. ``code`` is one of ``SWIPE_ERROR_MESSAGES``.

    Views show the member the fixed message for the code, never the
    exception text itself.
    """

    def __init__(self, code):
        super().__init__(code)
        self.code = code


SWIPE_ERROR_MESSAGES = {
    "self": _lazy("You cannot swipe on yourself."),
    "closed": _lazy("Swiping is closed for this event."),
    "not_attendee": _lazy("You must attend this event to swipe."),
    "unavailable": _lazy("This person is not available."),
    "hidden": _lazy(
        "You chose not to appear in past-event decks. Turn that off to swipe "
        "your past events."
    ),
}


def swipe_error_message(code):
    """The member-facing text for a ``SwipeNotAllowed`` code."""
    return SWIPE_ERROR_MESSAGES.get(code, SWIPE_ERROR_MESSAGES["unavailable"])


# ---------------------------------------------------------------------------
# Gates
# ---------------------------------------------------------------------------


def with_event_end(qs, prefix="event__"):
    """Alias ``_event_end`` (the event's scheduled end) for DB filtering.

    ``MeetupEvent.end_time`` is a property, so querysets need the same sum
    spelled as an expression (same shape as services/member_context.py).
    """
    from datetime import timedelta

    from django.db.models import (
        DateTimeField,
        DurationField,
        ExpressionWrapper,
        F,
        IntegerField,
        Value,
    )
    from django.db.models.functions import Cast

    duration = ExpressionWrapper(
        Cast(F(f"{prefix}duration_minutes"), IntegerField())
        * Value(timedelta(minutes=1)),
        output_field=DurationField(),
    )
    return qs.alias(
        _event_end=ExpressionWrapper(
            F(f"{prefix}date_time") + duration, output_field=DateTimeField()
        )
    )


MODE_EVENT = "event"
MODE_CAMPAIGN = "campaign"


def active_campaign(now=None):
    """The running, switched-on ``SwipeCampaign``, or ``None``."""
    now = now or timezone.now()
    return (
        SwipeCampaign.objects.filter(
            is_active=True, starts_at__lte=now, ends_at__gte=now
        )
        .order_by("-starts_at")
        .first()
    )


def campaign_covers(campaign, event) -> bool:
    """Every event that ended before the campaign started is in it."""
    return campaign is not None and event.end_time < campaign.starts_at


def swipe_mode(event, now=None):
    """``(mode, campaign)``: why swiping is open for ``event`` right now.

    ``MODE_EVENT`` inside the event's own post-event window (that wins),
    ``MODE_CAMPAIGN`` when a running campaign covers the event, otherwise
    ``(None, None)``.
    """
    now = now or timezone.now()
    if event.end_time <= now <= event.connection_window_deadline:
        return MODE_EVENT, None
    campaign = active_campaign(now)
    if campaign_covers(campaign, event):
        return MODE_CAMPAIGN, campaign
    return None, None


def swipe_window_open(event, now=None) -> bool:
    """True while the event's own window or a covering campaign is open."""
    return swipe_mode(event, now)[0] is not None


def swipe_deadline(event, now=None):
    """When swiping for ``event`` closes in the mode that is open now."""
    mode, campaign = swipe_mode(event, now)
    if mode == MODE_CAMPAIGN:
        return campaign.ends_at
    return event.connection_window_deadline


def _registration(user, event):
    return (
        EventRegistration.objects.filter(event=event, user=user)
        .select_related("user__crushprofile")
        .first()
    )


def hidden_from_past_decks(user) -> bool:
    """Opted out of campaign decks, or on a self-service break."""
    if PastEventDeckOptOut.objects.filter(user=user).exists():
        return True
    profile = getattr(user, "crushprofile", None)
    return bool(profile and profile.on_break_at)


def can_swipe(user, event) -> bool:
    """The member attended, is verified, and the window is open."""
    reg = _registration(user, event)
    if not (reg and reg.can_make_connections):
        return False
    mode, _campaign = swipe_mode(event)
    if mode is None:
        return False
    if mode == MODE_CAMPAIGN and hidden_from_past_decks(user):
        return False
    return True


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


def _candidate_registrations(user, event, campaign=None):
    """Verified attendees of ``event`` other than ``user``, not hidden.

    With ``campaign`` (campaign mode) the campaign rules apply on top: see
    ``_campaign_excluded_ids``.
    """
    qs = (
        EventRegistration.objects.filter(
            event=event,
            status="attended",
            user__crushprofile__verification_status="verified",
        )
        .exclude(user=user)
        .exclude(user_id__in=_hidden_ids(user))
        .select_related("user__crushprofile")
    )
    if campaign is not None:
        qs = qs.exclude(user_id__in=_campaign_excluded_ids(user, event, campaign))
    return qs


def _campaign_excluded_ids(user, event, campaign) -> set[int]:
    """Who a campaign deck for ``event`` leaves out for ``user``.

    - members who opted out of past-event decks or are on a break;
    - anyone ``user`` already swiped at another event (a decision is final
      for the person, not only for the event);
    - anyone ``user`` already has a shared connection with;
    - anyone ``user`` also attended a *later* campaign event with: that pair
      is shown once, in the newest shared event's deck.
    """
    from crush_lu.models.connections import EventConnection
    from crush_lu.models.profiles import CrushProfile

    excluded = set(PastEventDeckOptOut.objects.values_list("user_id", flat=True))
    excluded |= set(
        CrushProfile.objects.filter(on_break_at__isnull=False).values_list(
            "user_id", flat=True
        )
    )
    excluded |= set(
        EventSwipe.objects.filter(swiper=user)
        .exclude(event=event)
        .values_list("target_id", flat=True)
    )
    for requester_id, recipient_id in (
        EventConnection.objects.filter(status="shared")
        .for_user(user)
        .values_list("requester_id", "recipient_id")
    ):
        excluded.add(recipient_id if requester_id == user.pk else requester_id)
    later_events = (
        with_event_end(EventRegistration.objects.filter(user=user, status="attended"))
        .filter(_event_end__gt=event.end_time, _event_end__lt=campaign.starts_at)
        .values_list("event_id", flat=True)
    )
    excluded |= set(
        EventRegistration.objects.filter(
            event_id__in=list(later_events), status="attended"
        ).values_list("user_id", flat=True)
    )
    excluded.discard(user.pk)
    return excluded


def swipe_deck(user, event) -> list[dict]:
    """Cards still to swipe, in a stable per-member shuffled order.

    Each card is ``{"user": User, "profile": CrushProfile}``. The order is
    shuffled with a seed of (event, viewer) so a reload shows the same next
    card, while two members never see the room in the same order.
    """
    own_profile = getattr(user, "crushprofile", None)
    if own_profile is None:
        return []
    mode, campaign = swipe_mode(event)
    swiped_ids = set(
        EventSwipe.objects.filter(event=event, swiper=user).values_list(
            "target_id", flat=True
        )
    )
    registrations = _candidate_registrations(user, event, campaign).prefetch_related(
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
    """How many compatible attendees the deck has in total (swiped or not)."""
    own_profile = getattr(user, "crushprofile", None)
    if own_profile is None:
        return 0
    mode, campaign = swipe_mode(event)
    if mode == MODE_CAMPAIGN:
        # Campaign decks drop people decided on elsewhere, so count what is
        # left plus what was decided at this event.
        return (
            len(swipe_deck(user, event))
            + EventSwipe.objects.filter(event=event, swiper=user).count()
        )
    return sum(
        1
        for reg in _candidate_registrations(user, event)
        if is_compatible(own_profile, reg.user.crushprofile)
    )


def past_event_decks(user, campaign):
    """The member's campaign events, newest first, with what is left to swipe.

    Each row: ``{"event", "remaining", "matches"}``.
    """
    from crush_lu.models.connections import EventConnection

    registrations = (
        with_event_end(EventRegistration.objects.filter(user=user, status="attended"))
        .filter(_event_end__lt=campaign.starts_at)
        .select_related("event")
        .order_by("-_event_end")
    )
    rows = []
    for reg in registrations:
        event = reg.event
        rows.append(
            {
                "event": event,
                "remaining": len(swipe_deck(user, event)),
                "matches": EventConnection.objects.filter(
                    event=event, flow=EventConnection.FLOW_SWIPE
                )
                .for_user(user)
                .count(),
            }
        )
    return rows


def profiles_left(user, campaign) -> bool:
    """Is any card left in any of the member's campaign decks?"""
    return any(row["remaining"] for row in past_event_decks(user, campaign))


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
        raise SwipeNotAllowed("self")
    mode, campaign = swipe_mode(event)
    if mode is None:
        raise SwipeNotAllowed("closed")

    swiper_reg = _registration(swiper, event)
    if not (swiper_reg and swiper_reg.can_make_connections):
        raise SwipeNotAllowed("not_attendee")
    if mode == MODE_CAMPAIGN and hidden_from_past_decks(swiper):
        raise SwipeNotAllowed("hidden")

    existing = EventSwipe.objects.filter(
        event=event, swiper=swiper, target=target
    ).first()
    if existing is not None:
        # A double submit: answer as before, even though campaign rules
        # would now leave this person out of the deck.
        connection = _swipe_connection(swiper, target, event)
        return {
            "match": bool(existing.liked and connection),
            "connection": connection,
            "already_swiped": True,
        }

    target_reg = (
        _candidate_registrations(swiper, event, campaign).filter(user=target).first()
    )
    if target_reg is None or not is_compatible(
        swiper.crushprofile, target_reg.user.crushprofile
    ):
        # One message for "not an attendee", "blocked" and "not compatible"
        # so the endpoint cannot be used to probe any of them.
        raise SwipeNotAllowed("unavailable")

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

        connection = _create_match(
            first_liker=target, second_liker=swiper, event=event, campaign=campaign
        )
        open_connect_chat(connection)

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


def _create_match(*, first_liker, second_liker, event, campaign=None):
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
    details = {}
    if campaign is not None:
        details["campaign"] = campaign.pk
    if connection is None:
        connection = EventConnection(
            requester=first_liker, recipient=second_liker, event=event
        )
    else:
        details["replaced"] = f"{connection.flow}/{connection.status}"
        if connection.requester_note:
            # A crush note was promised to the coach alone ("never your
            # crush"), and member surfaces only redact it on flow='crush'
            # rows. Move it into the audit log (staff/admin only) rather
            # than let the recipient read it on the swipe match.
            details["coach_only_note"] = connection.requester_note
            connection.requester_note = ""

    connection.flow = EventConnection.FLOW_SWIPE
    connection.status = "shared"
    connection.requester_consents_to_share = True
    connection.recipient_consents_to_share = True
    connection.responded_at = connection.responded_at or now
    connection.shared_at = connection.shared_at or now
    connection.log_system_action("swipe_match", **details)
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
    hidden = _hidden_ids(user)
    return (
        EventConnection.objects.filter(event=event, flow=EventConnection.FLOW_SWIPE)
        .for_user(user)
        # Same symmetric hiding as the deck, my_connections and
        # connection_detail: a block or encounter removal after the match
        # hides the counterpart here too.
        .exclude(requester_id__in=hidden)
        .exclude(recipient_id__in=hidden)
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


# ---------------------------------------------------------------------------
# Crush Connect
# ---------------------------------------------------------------------------


def _connect_participating(user) -> bool:
    membership = getattr(user, "crush_connect_membership", None)
    return bool(membership and membership.is_participating)


def open_connect_chat(connection):
    """Open a Crush Connect chat for a swipe match of two Connect members.

    Decision 2026-11-20: matches should become Crush Connect conversations
    (venue picker, coffee date). Only when both members take part in Connect
    (onboarded, not excluded, not paused); everyone else keeps the chat on
    the connection page. Returns the chat or ``None``.
    """
    from crush_lu.models.crush_connect_cycle import ConnectTemporaryChat

    if not (
        _connect_participating(connection.requester)
        and _connect_participating(connection.recipient)
    ):
        return None
    chat, _created = ConnectTemporaryChat.objects.get_or_create(
        event_connection=connection,
        defaults={
            "participant_1": connection.requester,
            "participant_2": connection.recipient,
        },
    )
    return chat


def live_connect_chat(connection):
    """The match's Connect chat while it is still open, else ``None``."""
    from crush_lu.services.connect_chat import chat_is_open, sync_chat_state

    chat = getattr(connection, "connect_chat", None)
    if chat is None:
        return None
    if not (
        _connect_participating(chat.participant_1)
        and _connect_participating(chat.participant_2)
    ):
        # One of them left or paused Connect: the Connect chat would 404, so
        # the pair falls back to the chat on the connection page.
        return None
    from crush_lu.services.event_lobby import hidden_encounter_user_ids

    if chat.participant_2_id in hidden_encounter_user_ids(chat.participant_1):
        return None
    chat = sync_chat_state(chat)
    return chat if chat_is_open(chat) else None


def match_chat_route(connection):
    """``(url_name, kwargs)`` of where the pair chats: Connect or the match page."""
    chat = live_connect_chat(connection)
    if chat is not None:
        return "crush_lu:connect_week_chat_detail", {"chat_id": chat.pk}
    return "crush_lu:connection_detail", {"connection_id": connection.pk}


def campaign_stats(campaign) -> dict:
    """KPI counts for a campaign: swipers, swipes, likes, matches, chats."""
    from crush_lu.models.connections import ConnectionMessage
    from crush_lu.models.crush_connect_cycle import ConnectChatMessage

    window = {
        "created_at__gte": campaign.starts_at,
        "created_at__lte": campaign.ends_at,
    }
    swipes = with_event_end(EventSwipe.objects.filter(**window)).filter(
        _event_end__lt=campaign.starts_at
    )
    matches = with_event_end(
        EventConnection.objects.filter(
            flow=EventConnection.FLOW_SWIPE,
            shared_at__gte=campaign.starts_at,
            shared_at__lte=campaign.ends_at,
        )
    ).filter(_event_end__lt=campaign.starts_at)
    match_ids = list(matches.values_list("pk", flat=True))
    chatted = set(
        ConnectionMessage.objects.filter(connection_id__in=match_ids).values_list(
            "connection_id", flat=True
        )
    ) | set(
        ConnectChatMessage.objects.filter(
            chat__event_connection_id__in=match_ids
        ).values_list("chat__event_connection_id", flat=True)
    )
    return {
        "swipers": swipes.values("swiper").distinct().count(),
        "swipes": swipes.count(),
        "likes": swipes.filter(liked=True).count(),
        "matches": len(match_ids),
        "matches_with_chat": len(chatted),
        "connect_chats": matches.filter(connect_chat__isnull=False).count(),
    }
