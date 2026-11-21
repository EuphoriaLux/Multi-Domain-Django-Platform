"""
Coach "Matches" overview (2026-11-21): one read-only page where every active
coach sees the matches between members.

A match is any ``EventConnection`` in ``shared`` status: post-event swipe
matches (``flow='swipe'``, shared the moment both liked) plus the few older
connections whose coach introduction was completed (legacy and "My Crush!"
rows). Rows that are not ``shared`` are not matches and stay in the old
coach queue, where the routed-coach privacy rules apply.

Privacy, kept from the member-facing views:
- A pair that blocked each other (either direction) or has a pending or
  approved encounter removal is left out entirely, so the page never shows
  that a safety step happened or who took it.
- Message *contents*, the crush ``requester_note`` and ``coach_notes`` never
  reach this page: only counts, the last-activity time and the chat state.
"""

from __future__ import annotations

from django.db.models import Count, F, Max, Q
from django.db.models.functions import Coalesce

from crush_lu.models import EventConnection

CHAT_ALL = "all"
CHAT_ACTIVE = "chatting"
CHAT_SILENT = "silent"
CHAT_DATE = "date"
CHAT_FILTERS = (CHAT_ALL, CHAT_ACTIVE, CHAT_SILENT, CHAT_DATE)

_DATE_STATUSES = ("meeting_scheduled", "meeting_confirmed")


def match_queryset():
    """Every shared connection, annotated with chat activity (no contents).

    Messages live either on the connection page (``ConnectionMessage``,
    coach messages excluded) or, for two Crush Connect members, in the
    linked ``ConnectTemporaryChat``. Both are counted.
    """
    return (
        EventConnection.objects.filter(status="shared")
        .select_related(
            "event",
            "requester__crushprofile",
            "recipient__crushprofile",
            "connect_chat",
        )
        .annotate(
            matched_at=Coalesce("shared_at", "requested_at"),
            page_message_count=Count(
                "messages",
                filter=Q(messages__is_coach_message=False),
                distinct=True,
            ),
            connect_message_count=Count("connect_chat__messages", distinct=True),
            last_page_message_at=Max(
                "messages__sent_at", filter=Q(messages__is_coach_message=False)
            ),
            last_connect_message_at=Max("connect_chat__messages__sent_at"),
        )
        .annotate(message_count=F("page_message_count") + F("connect_message_count"))
        .order_by("-matched_at", "-pk")
    )


def _hidden_pairs(user_ids) -> set[frozenset[int]]:
    """Pairs among ``user_ids`` that must not appear: blocks + removals."""
    from crush_lu.models import ConfirmedEncounter
    from crush_lu.services.blocking import blocked_pairs_among

    ids = {uid for uid in user_ids if uid is not None}
    if len(ids) < 2:
        return set()
    hidden = set(blocked_pairs_among(ids))
    removed = ConfirmedEncounter.objects.filter(
        user_low_id__in=ids,
        user_high_id__in=ids,
        status__in=("removal_pending", "removed"),
    ).values_list("user_low_id", "user_high_id")
    hidden.update(frozenset(pair) for pair in removed)
    return hidden


def _safe_connect_chat(conn):
    try:
        return conn.connect_chat
    except EventConnection.connect_chat.RelatedObjectDoesNotExist:
        return None


def visible_matches(*, event=None, query=""):
    """Matches shown to coaches, as a list (privacy filtering is per pair).

    Every active coach sees the same list (decision 2026-11-21).
    """
    qs = match_queryset()
    if event is not None:
        qs = qs.filter(event=event)
    query = (query or "").strip()
    if query:
        name_q = Q()
        for side in ("requester", "recipient"):
            # ``display_name`` is a property built from the first name.
            name_q |= Q(**{f"{side}__first_name__icontains": query})
        qs = qs.filter(name_q)

    rows = list(qs)
    hidden = _hidden_pairs(
        {c.requester_id for c in rows} | {c.recipient_id for c in rows}
    )
    result = []
    for conn in rows:
        if frozenset((conn.requester_id, conn.recipient_id)) in hidden:
            continue
        chat = _safe_connect_chat(conn)
        conn.chat_place = "connect" if chat else "page"
        conn.chat_status = chat.status if chat else ""
        conn.date_planned = bool(chat and chat.status in _DATE_STATUSES)
        conn.chat_closed = bool(chat and chat.status in ("closed", "blocked"))
        times = [
            t
            for t in (conn.last_page_message_at, conn.last_connect_message_at)
            if t is not None
        ]
        conn.last_message_at = max(times) if times else None
        result.append(conn)
    return result


def match_counters(rows) -> dict:
    """Header counters, computed from exactly the rows the coach can open."""
    members = set()
    for conn in rows:
        members.add(conn.requester_id)
        members.add(conn.recipient_id)
    return {
        "matches": len(rows),
        "chatting": sum(1 for c in rows if c.message_count),
        "silent": sum(1 for c in rows if not c.message_count),
        "dates": sum(1 for c in rows if c.date_planned),
        "members": len(members),
    }


def filter_by_chat(rows, chat_filter):
    if chat_filter == CHAT_ACTIVE:
        return [c for c in rows if c.message_count]
    if chat_filter == CHAT_SILENT:
        return [c for c in rows if not c.message_count]
    if chat_filter == CHAT_DATE:
        return [c for c in rows if c.date_planned]
    return rows
