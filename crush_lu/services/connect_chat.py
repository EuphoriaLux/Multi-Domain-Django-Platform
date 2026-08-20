"""
Crush Connect — Temporary Chat, Venue Picker, Post-Meeting Confirmation, and
1-Click Block (Epic 13 follow-up to Task 13.2 / PR #886).

Builds on the ``ConnectTemporaryChat`` / ``ConnectChatMessage`` /
``ConnectCoffeeDate`` / ``ConnectPairExclusion`` / ``ConnectReport`` rows PR
#883 shipped (``crush_lu.models.crush_connect_cycle``) and the chat
``respond_to_weekly_request`` (PR #886, ``services.connect_cycle``) already
opens via ``get_or_create`` on mutual accept. This module is the mechanics
layer for everything that happens *inside* that chat — see
``views_connect_chat`` for the HTTP surface.

Scope notes:

- The chat's ``expires_at`` is a rolling **inactivity** timer while
  ACTIVE/MEETING_SCHEDULED: every successful ``send_message`` pushes it
  another 7 days out (a fixed "7 days from creation" timer would kill an
  active conversation mid-sentence on day 7 — that's a lifetime, not an
  inactivity timeout). It stops rolling once ``ConnectCoffeeDate.
  confirm_by_user`` flips the chat to MEETING_CONFIRMED — the model's own
  +3-day extension there is a hard post-meeting retention window, not
  another inactivity clock, and must not be pushed further by chatter.
- ``sync_chat_state`` is the sync-on-read guard (mirrors
  ``connect_cycle.sync_session_state`` / ``sync_request_state``): it also
  checks ``services.blocking.is_blocked_pair`` so a block placed from ANY
  OTHER surface (a Spark card, a member page) closes this chat too, not
  only a block placed from inside it.
- ``block_chat_partner`` calls ``services.blocking.apply_block`` (the same
  choke point ``views_moderation.block_user`` uses) so a block placed from
  inside a Connect Cycle chat reaches every other facilitation surface too
  — "you won't be able to reach each other" has to be true everywhere that
  copy is shown, not just inside this one chat. It additionally writes a
  ``ConnectPairExclusion`` (permanent Cycle re-match exclusion — the
  general block system has no notion of the Cycle's card pool) and closes
  this chat. The optional coach-escalation report reuses ``ConnectReport``
  (PR #883, previously unused by any view/service) rather than the general
  ``UserReport`` queue — its ``reviewed_by`` FK to ``CrushCoach``
  specifically routes it to coach review, matching what a Connect Cycle
  in-person-meeting safety report needs.
"""

from __future__ import annotations

from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_date

CHAT_INACTIVITY_WINDOW_DAYS = 7
CHAT_MESSAGE_MAX_LENGTH = 1000
VENUE_NAME_MAX_LENGTH = 255
VENUE_ADDRESS_MAX_LENGTH = 255
TIME_SLOT_MAX_LENGTH = 50
REPORT_DETAILS_MAX_LENGTH = 2000


# ---------------------------------------------------------------------------
# Chat lifecycle
# ---------------------------------------------------------------------------


def sync_chat_state(chat):
    """Advance a chat's lifecycle to "now" — sync-on-read, mirrors
    ``connect_cycle.sync_session_state`` / ``sync_request_state``.

    Three ways a chat closes automatically:
    - A block exists between the pair (either direction, placed from ANY
      surface) -> BLOCKED.
    - An ACTIVE/MEETING_SCHEDULED chat passes its rolling inactivity
      ``expires_at`` -> CLOSED/INACTIVITY_TIMEOUT.
    - A MEETING_CONFIRMED chat passes its fixed +3-day post-meeting
      ``expires_at`` -> CLOSED/POST_MEETING_RETENTION.

    Idempotent; a terminal chat (CLOSED/BLOCKED) is returned as-is.
    """
    from crush_lu.models.crush_connect_cycle import ConnectTemporaryChat
    from crush_lu.services.blocking import is_blocked_pair

    Status = ConnectTemporaryChat.Status
    if chat.status in (Status.CLOSED, Status.BLOCKED):
        return chat

    now = timezone.now()

    if is_blocked_pair(chat.participant_1, chat.participant_2):
        chat.status = Status.BLOCKED
        chat.close_reason = ConnectTemporaryChat.CloseReason.BLOCKED
        chat.closed_at = now
        chat.save(update_fields=["status", "close_reason", "closed_at"])
        return chat

    if chat.expires_at and now >= chat.expires_at:
        reason = (
            ConnectTemporaryChat.CloseReason.POST_MEETING_RETENTION
            if chat.status == Status.MEETING_CONFIRMED
            else ConnectTemporaryChat.CloseReason.INACTIVITY_TIMEOUT
        )
        chat.status = Status.CLOSED
        chat.close_reason = reason
        chat.closed_at = now
        chat.save(update_fields=["status", "close_reason", "closed_at"])
    return chat


def chat_is_open(chat) -> bool:
    """Whether new activity (messages, venue plans) is currently allowed."""
    from crush_lu.models.crush_connect_cycle import ConnectTemporaryChat

    return chat.status in (
        ConnectTemporaryChat.Status.ACTIVE,
        ConnectTemporaryChat.Status.MEETING_SCHEDULED,
        ConnectTemporaryChat.Status.MEETING_CONFIRMED,
    )


def user_has_non_closed_chat(user) -> bool:
    """Whether ``user`` has any Connect Cycle temp chat worth surfacing a
    "Your chats" link for — everything except the fully-CLOSED terminal
    state (a BLOCKED chat still counts: it's the record of what happened).

    Used to gate the entry point into ``connect_week_chats`` from
    ``connect_week_home`` — without this, a chat opened via a mutual accept
    has exactly one reachable moment (the accept redirect) and no way back
    in once that tab closes; the Connect Week review page it also linked
    from stops existing once the session completes (a new cycle starts).
    """
    from django.db.models import Q

    from crush_lu.models.crush_connect_cycle import ConnectTemporaryChat

    return (
        ConnectTemporaryChat.objects.filter(
            Q(participant_1=user) | Q(participant_2=user)
        )
        .exclude(status=ConnectTemporaryChat.Status.CLOSED)
        .exists()
    )


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------


def list_messages(chat):
    return list(chat.messages.select_related("sender").order_by("sent_at"))


def send_message(chat, sender, text):
    """Post a message and roll the 7-day inactivity window forward.

    Raises ``ValueError``: ``not_participant`` if ``sender`` isn't in the
    chat, ``chat_closed`` if the chat is no longer open (synced first so a
    just-expired chat is caught, not a stale flag), ``empty_message`` for a
    blank/whitespace-only body. The body is stripped and hard-capped at
    ``CHAT_MESSAGE_MAX_LENGTH`` — the model's ``max_length=1000`` is
    form-only (TextField), not DB-enforced, so this is the actual guard
    (mirrors the ``[:2000]`` truncation ``views_moderation.report_user``
    applies to report details).

    Does NOT roll ``expires_at`` forward while MEETING_CONFIRMED — that
    window is a fixed post-meeting retention period set by
    ``ConnectCoffeeDate.confirm_by_user``, not an inactivity timer (see the
    module docstring).
    """
    from crush_lu.models.crush_connect_cycle import (
        ConnectChatMessage,
        ConnectTemporaryChat,
    )

    if sender.id not in (chat.participant_1_id, chat.participant_2_id):
        raise ValueError("not_participant")

    chat = sync_chat_state(chat)
    if not chat_is_open(chat):
        raise ValueError("chat_closed")

    body = (text or "").strip()[:CHAT_MESSAGE_MAX_LENGTH]
    if not body:
        raise ValueError("empty_message")

    with transaction.atomic():
        message = ConnectChatMessage.objects.create(
            chat=chat, sender=sender, message=body
        )
        if chat.status in (
            ConnectTemporaryChat.Status.ACTIVE,
            ConnectTemporaryChat.Status.MEETING_SCHEDULED,
        ):
            chat.expires_at = timezone.now() + timedelta(
                days=CHAT_INACTIVITY_WINDOW_DAYS
            )
            chat.save(update_fields=["expires_at"])
    return message


# ---------------------------------------------------------------------------
# Venue picker (ConnectCoffeeDate)
# ---------------------------------------------------------------------------


def get_partner_venues():
    """Curated partner locations available for a Connect coffee date.

    Active-partnership Hub locations only — ``hub.Location`` also carries
    CRM-only fields (``commercial_terms``, ``notes``, ``account_manager``,
    ``next_action``…) that must never reach this member-facing picker; only
    ``name``/``address``/``city`` should ever be rendered from it here.
    """
    from hub.models import Location

    return Location.objects.filter(
        partnership_stage=Location.PartnershipStage.ACTIVE
    ).order_by("name")


def propose_venue(
    chat,
    proposer,
    *,
    venue_location_id=None,
    custom_venue_name="",
    custom_venue_address="",
    proposed_date,
    proposed_time_slot="",
):
    """Create or update the chat's single ``ConnectCoffeeDate`` plan.

    Either participant may propose or edit the plan while it isn't yet
    CONFIRMED_BY_BOTH. The first save creates it (PROPOSED). Editing a plan
    that was already ACCEPTED flips it to RESCHEDULED and clears both
    confirmation timestamps — a changed plan needs fresh mutual agreement
    both for ``accept_venue`` and later for the post-meeting
    double-confirmation. Editing a plan that's still PROPOSED/RESCHEDULED
    (never accepted) just re-saves it as PROPOSED — nothing to invalidate.

    Raises ``ValueError``: ``not_participant``, ``chat_closed`` (chat isn't
    ACTIVE/MEETING_SCHEDULED — a MEETING_CONFIRMED chat's plan is done, and
    a CLOSED/BLOCKED one can't be planned in at all), ``already_confirmed``
    (the existing plan is CONFIRMED_BY_BOTH), ``missing_venue`` (neither a
    partner location nor a custom name was given), ``invalid_date``.
    """
    from crush_lu.models.crush_connect_cycle import (
        ConnectCoffeeDate,
        ConnectTemporaryChat,
    )
    from hub.models import Location

    if proposer.id not in (chat.participant_1_id, chat.participant_2_id):
        raise ValueError("not_participant")

    chat = sync_chat_state(chat)
    if chat.status not in (
        ConnectTemporaryChat.Status.ACTIVE,
        ConnectTemporaryChat.Status.MEETING_SCHEDULED,
    ):
        raise ValueError("chat_closed")

    venue_location = None
    if venue_location_id:
        venue_location = Location.objects.filter(
            pk=venue_location_id, partnership_stage=Location.PartnershipStage.ACTIVE
        ).first()

    custom_venue_name = (custom_venue_name or "").strip()[:VENUE_NAME_MAX_LENGTH]
    custom_venue_address = (custom_venue_address or "").strip()[
        :VENUE_ADDRESS_MAX_LENGTH
    ]
    if venue_location is None and not custom_venue_name:
        raise ValueError("missing_venue")

    parsed_date = proposed_date
    if isinstance(proposed_date, str):
        parsed_date = parse_date(proposed_date)
    if not parsed_date:
        raise ValueError("invalid_date")

    proposed_time_slot = (proposed_time_slot or "").strip()[:TIME_SLOT_MAX_LENGTH]

    coffee_date = getattr(chat, "coffee_date", None)
    with transaction.atomic():
        if coffee_date is None:
            coffee_date = ConnectCoffeeDate.objects.create(
                chat=chat,
                proposer=proposer,
                venue_location=venue_location,
                custom_venue_name="" if venue_location else custom_venue_name,
                custom_venue_address="" if venue_location else custom_venue_address,
                proposed_date=parsed_date,
                proposed_time_slot=proposed_time_slot,
            )
        else:
            if coffee_date.status == ConnectCoffeeDate.Status.CONFIRMED_BY_BOTH:
                raise ValueError("already_confirmed")
            was_accepted = coffee_date.status == ConnectCoffeeDate.Status.ACCEPTED
            # Whoever edits now owns these terms — the self-accept guard in
            # ``accept_venue`` compares against ``proposer_id``, so leaving
            # this pointed at the original proposer would let the editor
            # "accept" terms they themselves just rewrote (no one else's
            # agreement obtained). Reassigning here is what keeps "the
            # counterpart accepts" true after an edit, not just after the
            # first proposal.
            coffee_date.proposer = proposer
            coffee_date.venue_location = venue_location
            coffee_date.custom_venue_name = "" if venue_location else custom_venue_name
            coffee_date.custom_venue_address = (
                "" if venue_location else custom_venue_address
            )
            coffee_date.proposed_date = parsed_date
            coffee_date.proposed_time_slot = proposed_time_slot
            coffee_date.status = (
                ConnectCoffeeDate.Status.RESCHEDULED
                if was_accepted
                else ConnectCoffeeDate.Status.PROPOSED
            )
            coffee_date.participant_1_confirmed_at = None
            coffee_date.participant_2_confirmed_at = None
            coffee_date.save()

        if chat.status == ConnectTemporaryChat.Status.ACTIVE:
            chat.status = ConnectTemporaryChat.Status.MEETING_SCHEDULED
            chat.save(update_fields=["status"])

    return coffee_date


def accept_venue(coffee_date, user):
    """The counterpart (not the proposer) accepts a pending plan.

    PROPOSED/RESCHEDULED -> ACCEPTED. Raises ``ValueError``:
    ``not_participant``, ``chat_closed`` (the chat has since gone
    BLOCKED/CLOSED — synced first so a block/expiry that landed after the
    plan was proposed is caught here too, not just on the next
    ``chat_detail`` GET; mirrors ``send_message``/``propose_venue``),
    ``cannot_self_accept`` (the proposer can't accept their own plan —
    accepting is the *other* side's step), ``not_pending`` (already
    ACCEPTED/CONFIRMED_BY_BOTH/CANCELLED).
    """
    from crush_lu.models.crush_connect_cycle import ConnectCoffeeDate

    if user.id not in (
        coffee_date.chat.participant_1_id,
        coffee_date.chat.participant_2_id,
    ):
        raise ValueError("not_participant")

    chat = sync_chat_state(coffee_date.chat)
    if not chat_is_open(chat):
        raise ValueError("chat_closed")

    if user.id == coffee_date.proposer_id:
        raise ValueError("cannot_self_accept")
    if coffee_date.status not in (
        ConnectCoffeeDate.Status.PROPOSED,
        ConnectCoffeeDate.Status.RESCHEDULED,
    ):
        raise ValueError("not_pending")

    coffee_date.status = ConnectCoffeeDate.Status.ACCEPTED
    coffee_date.save(update_fields=["status"])
    return coffee_date


def cancel_venue(coffee_date, user):
    """Either participant cancels the plan. Raises ``ValueError``:
    ``not_participant``, ``chat_closed`` (synced first, same reasoning as
    ``accept_venue``), ``already_confirmed`` (can't cancel a completed
    meeting)."""
    from crush_lu.models.crush_connect_cycle import ConnectCoffeeDate

    if user.id not in (
        coffee_date.chat.participant_1_id,
        coffee_date.chat.participant_2_id,
    ):
        raise ValueError("not_participant")

    chat = sync_chat_state(coffee_date.chat)
    if not chat_is_open(chat):
        raise ValueError("chat_closed")

    if coffee_date.status == ConnectCoffeeDate.Status.CONFIRMED_BY_BOTH:
        raise ValueError("already_confirmed")

    coffee_date.status = ConnectCoffeeDate.Status.CANCELLED
    coffee_date.save(update_fields=["status"])
    return coffee_date


# ---------------------------------------------------------------------------
# Post-meeting confirmation loop
# ---------------------------------------------------------------------------


def confirm_meeting(chat, coffee_date, user):
    """Record ``user``'s post-meeting confirmation.

    Delegates the actual bookkeeping to ``ConnectCoffeeDate.confirm_by_user``
    — the model owns the +3-day chat extension, which fires only once BOTH
    participants have confirmed (see the model docstring; NOT on a single
    "partial" confirmation — verified against ``test_crush_connect_cycle_
    models.py`` before writing this). Guards against a re-POST after both
    already confirmed: without this guard, calling ``confirm_by_user`` again
    would re-run the both-confirmed branch and push ``expires_at`` another 3
    days out indefinitely, effectively an unbounded chat.

    Raises ``ValueError``: ``not_participant``, ``chat_closed`` (synced
    first, same reasoning as ``accept_venue``/``cancel_venue`` — a block or
    expiry that landed after the plan was ACCEPTED must not let a stale tab
    still record a confirmed meeting), ``already_confirmed`` (status is
    already CONFIRMED_BY_BOTH), ``not_accepted`` (the plan itself was never
    mutually accepted — nothing to confirm attendance against).
    """
    from crush_lu.models.crush_connect_cycle import ConnectCoffeeDate

    if user.id not in (chat.participant_1_id, chat.participant_2_id):
        raise ValueError("not_participant")

    chat = sync_chat_state(chat)
    if not chat_is_open(chat):
        raise ValueError("chat_closed")

    if coffee_date.status == ConnectCoffeeDate.Status.CONFIRMED_BY_BOTH:
        raise ValueError("already_confirmed")
    if coffee_date.status != ConnectCoffeeDate.Status.ACCEPTED:
        raise ValueError("not_accepted")

    coffee_date.confirm_by_user(user)
    coffee_date.refresh_from_db()
    chat.refresh_from_db()
    return coffee_date, chat


# ---------------------------------------------------------------------------
# 1-click block
# ---------------------------------------------------------------------------


def block_chat_partner(chat, user, *, escalate=False, details=""):
    """1-click block from inside a temp chat.

    Always: creates the app-wide ``UserBlock`` (via ``services.blocking.
    apply_block`` — same choke point ``views_moderation.block_user`` uses,
    so the block reaches Sparks/EventConnections/coach picks too, not just
    this chat), permanently excludes the pair from future Connect Cycle
    re-matching (``ConnectPairExclusion.exclude_pair``), and closes this
    chat (BLOCKED). Optionally (``escalate=True``) also files a
    ``ConnectReport`` for coach review — see the module docstring for why
    ``ConnectReport`` rather than the general ``UserReport`` queue.

    Raises ``ValueError('not_participant')`` if ``user`` isn't in the chat.
    Returns ``(partner, report_or_none)``.
    """
    from crush_lu.models.crush_connect_cycle import (
        ConnectPairExclusion,
        ConnectReport,
        ConnectTemporaryChat,
    )
    from crush_lu.services.blocking import apply_block

    if user.id not in (chat.participant_1_id, chat.participant_2_id):
        raise ValueError("not_participant")

    partner = chat.get_other_participant(user)
    now = timezone.now()

    with transaction.atomic():
        apply_block(user, partner)
        ConnectPairExclusion.exclude_pair(
            user, partner, reason=ConnectPairExclusion.Reason.MEMBER_BLOCKED
        )
        if chat.status not in (
            ConnectTemporaryChat.Status.CLOSED,
            ConnectTemporaryChat.Status.BLOCKED,
        ):
            chat.status = ConnectTemporaryChat.Status.BLOCKED
            chat.close_reason = ConnectTemporaryChat.CloseReason.BLOCKED
            chat.closed_at = now
            chat.save(update_fields=["status", "close_reason", "closed_at"])

        report = None
        if escalate:
            report = ConnectReport.objects.create(
                reporter=user,
                reported_user=partner,
                reason="connect_chat_block",
                details=(details or "").strip()[:REPORT_DETAILS_MAX_LENGTH],
            )

    return partner, report
