"""
Crush Connect — 7-Day Connect Cycle service layer (Epic 13 / Task 13.2).

Builds the daily-card generation, 24h weekly review, one-or-none request,
and recipient-inbox mechanics on top of the models PR #883 shipped
(``crush_lu.models.crush_connect_cycle``) and the entitlement gate it added
(``crush_lu.connect_phase.cycle_access_open``).

Scope notes for this PR (see the PR description for the full Restrisiken list):

- No model/schema changes. "Exactly one session in progress" and "exactly
  one non-terminal weekly request per session" are enforced here in
  application code, not database constraints — a concurrent-write race could
  in theory create two; ``get_or_create_active_session``/
  ``can_send_weekly_request`` narrow the window but don't close it (SQLite
  ignores ``select_for_update`` in tests AND in this repo's CI, so this can't
  be proven by a test either — see the worktree-postgres memory).
- Card questions reuse the *target's* existing 3 "Read-the-Photo" gate
  questions (``CrushConnectMembership.active_gate_questions``) instead of a
  new question bank — on-brand with Today's Drop and avoids a second content
  system. Guesses are recorded ONLY in ``ConnectCycleCard.answers_json`` and
  NEVER touch ``ConnectQuestionAnswer`` / ``submit_gate_answers`` — playing a
  Connect Week card must not fire a Today's-Drop match/Spark side effect.
  This does mean the cycle pool is narrowed to targets who already picked
  their 3 gate questions (mirrors ``GATE_QUESTION_COUNT``).
- Cross-cycle freshness ("never show the same person twice") is enforced by a
  dynamic query over past ``ConnectCycleCard`` rows, NOT a permanent
  ``ConnectPairExclusion``. The blueprint's "a pair is permanently excluded
  once the cycle has concluded" is read narrowly here — only a *declined* or
  *expired weekly request* creates a permanent exclusion. Reading it broadly
  (excluding all ~21 cards' targets on every completed cycle) would exhaust a
  beta-sized pool (~154 verified members) in 2-3 cycles and write permanent
  rows that are hard to undo; see the PR description.
- Accepting a weekly request opens a ``ConnectTemporaryChat`` row so a
  follow-up PR has something to build the coffee-planning chat UI on top of.
  This PR ships no chat UI — accepting just confirms the match.
- Daily-card selection is a simple seeded-deterministic sample (no
  match-score/language/interest weighting like Today's Drop's
  ``get_or_create_daily_drop``) — a documented simplification, not parity.
"""

from __future__ import annotations

import hashlib
import random
from datetime import date, timedelta
from typing import List, Tuple

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Count, Exists, OuterRef, Q
from django.utils import timezone

User = get_user_model()

CYCLE_LENGTH_DAYS = 7
CARDS_PER_DAY = 3
REVIEW_WINDOW_HOURS = 24


def _years_ago(years: int) -> date:
    """Approximate date offset by ``years``; good enough for age-range filters.
    Deliberately duplicated from ``services.crush_connect`` (a private helper
    there) rather than imported, to keep this module's dependency surface to
    public names only."""
    today = date.today()
    try:
        return today.replace(year=today.year - years)
    except ValueError:  # Feb-29 edge case
        return today.replace(year=today.year - years, day=28)


def _seeded_sample(candidates: List["User"], k: int, seed_key: str) -> List["User"]:
    """Deterministic, unweighted sample of up to ``k`` users from
    ``candidates``, seeded by ``seed_key`` — same inputs always produce the
    same picks (idempotent regeneration, agrees across workers)."""
    if k <= 0 or not candidates:
        return []
    seed = int.from_bytes(hashlib.sha256(seed_key.encode()).digest()[:8], "big")
    rng = random.Random(seed)
    ordered = sorted(candidates, key=lambda u: u.pk)
    keyed = sorted((rng.random(), u.pk, u) for u in ordered)
    return [u for _, _, u in keyed[:k]]


# ---------------------------------------------------------------------------
# Eligible pool
# ---------------------------------------------------------------------------


def get_cycle_eligible_pool(user):
    """
    Users eligible to appear as one of ``user``'s Connect Week cards.

    Requester gate: ``cycle_access_open`` (event-verified widens beyond the
    Premium-only Today's Drop receiver track) + approved profile + onboarded
    Connect membership. Unlike ``services.crush_connect.get_eligible_pool``,
    this does NOT require Premium.

    Candidate filters mirror ``get_eligible_pool``'s target side (verified,
    onboarded, not coach-excluded, photo-share consent, has a photo, active
    within the inactivity window, identity-verified, not an assigned-coach
    pair) plus two Cycle-specific rules: the candidate must have their 3 gate
    questions picked (the cards need something to ask), and must not already
    have appeared in one of ``user``'s past cycle cards (cross-cycle
    freshness) or be under a ``ConnectPairExclusion`` with them.

    Cross-cycle freshness is bidirectional: ``already_carded_ids`` stops
    ``user`` from re-carding someone they already carded, and
    ``received_active_requester_ids`` (below) separately stops ``user`` from
    being handed, as a "fresh stranger", someone who already sent *them* a
    still-pending or accepted weekly request — that person carded ``user``,
    not the other way around, so ``already_carded_ids`` alone misses it.
    Declined/expired requests need no extra handling: those already write a
    permanent ``ConnectPairExclusion`` (see ``respond_to_weekly_request`` /
    ``sync_request_state``), which ``excluded_ids`` below already covers.
    """
    from crush_lu.connect_phase import cycle_access_open
    from crush_lu.models import EventConnection
    from crush_lu.models.crush_connect_cycle import (
        ConnectCycleCard,
        ConnectPairExclusion,
        ConnectWeeklyRequest,
    )
    from crush_lu.services.blocking import block_exists_subquery
    from crush_lu.services.crush_connect import (
        CONNECT_INACTIVITY_WINDOW_DAYS,
        GATE_QUESTION_COUNT,
        exclude_assigned_coach_pairs,
        filter_connect_identity_verified,
    )

    if not cycle_access_open(user):
        return User.objects.none()

    user_profile = getattr(user, "crushprofile", None)
    if user_profile is None or not user_profile.is_approved:
        return User.objects.none()

    user_membership = getattr(user, "crush_connect_membership", None)
    if user_membership is None or not user_membership.is_onboarded:
        return User.objects.none()

    inactivity_cutoff = timezone.now() - timedelta(days=CONNECT_INACTIVITY_WINDOW_DAYS)

    already_carded_ids = ConnectCycleCard.objects.filter(
        session__user=user
    ).values("target_user_id")

    received_active_requester_ids = ConnectWeeklyRequest.objects.filter(
        recipient=user,
        status__in=[
            ConnectWeeklyRequest.Status.PENDING,
            ConnectWeeklyRequest.Status.ACCEPTED,
        ],
    ).values("requester_id")

    excluded_ids: set[int] = set()
    for a, b in ConnectPairExclusion.objects.filter(
        Q(user_a=user) | Q(user_b=user)
    ).values_list("user_a_id", "user_b_id"):
        excluded_ids.add(a)
        excluded_ids.add(b)
    excluded_ids.discard(user.pk)

    # A pair with any existing EventConnection (crush declared, connection
    # requested) is excluded from each other's cards, symmetrically — the
    # Cycle doesn't carry Today's Drop's directional pre-`shared`-crush
    # exemption (no equivalent privacy concern here).
    existing_connection_subq = EventConnection.objects.filter(
        Q(requester=user, recipient=OuterRef("pk"))
        | Q(requester=OuterRef("pk"), recipient=user)
    )

    qs = (
        User.objects.filter(
            crushprofile__verification_status="verified",
            crush_connect_membership__onboarded_at__isnull=False,
            crush_connect_membership__excluded_by_coach=False,
            crush_connect_membership__photo_share_consent=True,
            last_login__gte=inactivity_cutoff,
        )
        .exclude(Q(crushprofile__photo_1="") | Q(crushprofile__photo_1__isnull=True))
        .exclude(pk__in=already_carded_ids)
        .exclude(pk__in=received_active_requester_ids)
        .exclude(pk__in=excluded_ids)
        .exclude(pk=user.pk)
        .annotate(
            _gate_q_count=Count(
                "crush_connect_membership__gate_questions", distinct=True
            ),
            _has_connection=Exists(existing_connection_subq),
            _has_block=block_exists_subquery(user),
        )
        .filter(_gate_q_count__gte=GATE_QUESTION_COUNT)
        .filter(_has_connection=False)
        .filter(_has_block=False)
        .select_related("crushprofile", "crush_connect_membership")
    )
    qs = filter_connect_identity_verified(qs)
    qs = exclude_assigned_coach_pairs(qs, user)

    # --- Mutual gender preference (empty list = no preference, pass-through).
    # Copied from get_eligible_pool: JSONField array-containment lookups are
    # unreliable on SQLite, so the candidate-side check runs in Python.
    user_pref_genders = user_membership.preferred_genders or []
    if user_pref_genders:
        qs = qs.filter(crushprofile__gender__in=user_pref_genders)

    if user_profile.gender:
        gender = user_profile.gender
        eligible_pks = [
            u.pk
            for u in qs
            if not u.crush_connect_membership.preferred_genders
            or gender in u.crush_connect_membership.preferred_genders
        ]
        qs = User.objects.filter(pk__in=eligible_pks).select_related(
            "crushprofile", "crush_connect_membership"
        )

    # --- Mutual age range.
    user_age = user_profile.age
    if user_age is not None:
        qs = qs.filter(
            crush_connect_membership__preferred_age_min__lte=user_age,
            crush_connect_membership__preferred_age_max__gte=user_age,
        )

    pref_min = user_membership.preferred_age_min or 18
    pref_max = user_membership.preferred_age_max or 99
    latest_dob = _years_ago(pref_min)
    earliest_dob = _years_ago(pref_max + 1) + timedelta(days=1)
    qs = qs.filter(
        crushprofile__date_of_birth__lte=latest_dob,
        crushprofile__date_of_birth__gte=earliest_dob,
    )

    return qs.order_by("pk")


# ---------------------------------------------------------------------------
# Session lifecycle
# ---------------------------------------------------------------------------


def get_or_create_active_session(user):
    """Return the user's in-progress ``ConnectWeekSession`` (ACTIVE or
    REVIEW_OPEN), creating a new ACTIVE one only if they have none. Does NOT
    check ``cycle_access_open`` — callers gate access before calling this."""
    from crush_lu.models.crush_connect_cycle import ConnectWeekSession

    session = (
        ConnectWeekSession.objects.filter(
            user=user,
            status__in=[
                ConnectWeekSession.Status.ACTIVE,
                ConnectWeekSession.Status.REVIEW_OPEN,
            ],
        )
        .order_by("-started_at")
        .first()
    )
    if session is not None:
        return session
    return ConnectWeekSession.objects.create(user=user)


def _expire_incomplete_cards(session, up_to_day: int) -> None:
    from crush_lu.models.crush_connect_cycle import ConnectCycleCard

    ConnectCycleCard.objects.filter(
        session=session,
        day_number__lte=up_to_day,
        is_completed=False,
        is_expired=False,
    ).update(is_expired=True)


def _compute_compatibility_highlight(session):
    """Pick the session's one "Passt besonders gut zu dir" profile among its
    completed cards: highest cached Ideal-Crush ``MatchScore`` (neutral 0.5
    fallback, mirroring ``services.crush_connect._weight_for``), tie-broken
    by how well the member read that person's gate questions, then a
    deterministic seeded jitter so a genuine tie is stable but not always the
    lowest pk. Returns ``None`` if no card was completed."""
    from crush_lu.models import MatchScore
    from crush_lu.services.crush_connect import MATCHSCORE_NEUTRAL

    completed = list(
        session.cards.filter(is_completed=True).select_related("target_user")
    )
    if not completed:
        return None

    target_ids = [c.target_user_id for c in completed]
    match_scores = {}
    for row in MatchScore.objects.filter(
        Q(user_a=session.user, user_b_id__in=target_ids)
        | Q(user_b=session.user, user_a_id__in=target_ids)
    ).values("user_a_id", "user_b_id", "score_final"):
        other = (
            row["user_b_id"]
            if row["user_a_id"] == session.user_id
            else row["user_a_id"]
        )
        match_scores[other] = row["score_final"]

    seed = int.from_bytes(
        hashlib.sha256(f"{session.pk}:highlight".encode()).digest()[:8], "big"
    )
    rng = random.Random(seed)

    def sort_key(card):
        base = match_scores.get(card.target_user_id, MATCHSCORE_NEUTRAL)
        align = (card.answers_json or {}).get("gate_align", 0)
        return (-base, -align, rng.random(), card.target_user_id)

    ranked = sorted(completed, key=sort_key)
    return ranked[0].target_user


def sync_session_state(session):
    """Advance a session's day/review bookkeeping to "now".

    Source of truth is wall-clock elapsed days since ``started_at``, not the
    lazily-updated ``current_day_number`` display field — so a session that
    hasn't been visited in a few days still catches up correctly on the next
    visit: any still-incomplete cards for elapsed days flip ``is_expired``,
    ``current_day_number`` advances, and the 24h review opens once day 7 has
    fully elapsed. The review itself closes (-> COMPLETED) independently once
    its own 24h window elapses; a still-pending weekly request is NOT forced
    to resolve at that point (the recipient's response window is tracked on
    the request, not the session — see ``sync_request_state``). Idempotent:
    calling this twice in a row without time passing is a no-op the second
    time. Terminal sessions (COMPLETED/EXPIRED/ABANDONED) are returned as-is.
    """
    from crush_lu.models.crush_connect_cycle import ConnectWeekSession

    Status = ConnectWeekSession.Status
    if session.status not in (Status.ACTIVE, Status.REVIEW_OPEN):
        return session

    now = timezone.now()

    if session.status == Status.ACTIVE:
        start_date = timezone.localtime(session.started_at).date()
        elapsed_days = (timezone.localdate() - start_date).days
        wall_day = elapsed_days + 1  # 1-indexed: elapsed_days=0 -> still day 1

        if wall_day > CYCLE_LENGTH_DAYS:
            _expire_incomplete_cards(session, up_to_day=CYCLE_LENGTH_DAYS)
            if session.current_day_number != CYCLE_LENGTH_DAYS:
                session.current_day_number = CYCLE_LENGTH_DAYS
                session.save(update_fields=["current_day_number"])
            highlight = _compute_compatibility_highlight(session)
            session.open_weekly_review(highlight_user=highlight)
        else:
            _expire_incomplete_cards(session, up_to_day=wall_day - 1)
            if wall_day != session.current_day_number:
                session.current_day_number = wall_day
                session.save(update_fields=["current_day_number"])
        return session

    # REVIEW_OPEN
    if session.review_expires_at and now >= session.review_expires_at:
        session.status = Status.COMPLETED
        session.is_review_open = False
        session.completed_at = now
        session.save(update_fields=["status", "is_review_open", "completed_at"])
    return session


# ---------------------------------------------------------------------------
# Daily cards
# ---------------------------------------------------------------------------


def get_or_create_todays_cards(session):
    """Idempotently return (creating on first visit) today's up-to-3 cards
    for an ACTIVE session. Returns the persisted cards for a day already
    generated (even fewer than 3, if the pool ran dry) without re-rolling.
    Returns ``[]`` for a non-ACTIVE session or a day beyond the cycle."""
    from crush_lu.models.crush_connect_cycle import ConnectCycleCard, ConnectWeekSession

    day = session.current_day_number
    existing = list(
        ConnectCycleCard.objects.filter(session=session, day_number=day)
        .select_related(
            "target_user__crushprofile", "target_user__crush_connect_membership"
        )
        .order_by("card_index")
    )
    if existing:
        return existing

    if session.status != ConnectWeekSession.Status.ACTIVE or day > CYCLE_LENGTH_DAYS:
        return []

    pool = list(
        get_cycle_eligible_pool(session.user).prefetch_related(
            "crush_connect_membership__gate_questions__question"
        )
    )
    if not pool:
        return []

    chosen = _seeded_sample(
        pool, min(CARDS_PER_DAY, len(pool)), seed_key=f"{session.pk}:{day}"
    )

    cards = []
    with transaction.atomic():
        for idx, target in enumerate(chosen, start=1):
            card, _created = ConnectCycleCard.objects.get_or_create(
                session=session,
                day_number=day,
                card_index=idx,
                defaults={
                    "target_user": target,
                    "generated_date": timezone.localdate(),
                },
            )
            cards.append(card)
    return cards


def record_card_answer(card, guesses: dict):
    """Record a member's private guesses at one of today's cards.

    ``guesses`` is ``{question_id: bool}`` for the target's 3 gate questions.
    Stored only on the card (never in ``ConnectQuestionAnswer``) — see the
    module docstring. Also stores ``gate_align`` (0-3), used later to break
    ties when picking the review's compatibility highlight.
    """
    from crush_lu.services.crush_connect import owner_gate_truths

    truths = owner_gate_truths(card.target_user)
    align = sum(1 for qid, truth in truths.items() if guesses.get(qid) == truth)
    card.answers_json = {
        "guesses": {str(qid): bool(val) for qid, val in guesses.items()},
        "gate_align": align,
    }
    card.is_completed = True
    card.completed_at = timezone.now()
    card.save(update_fields=["answers_json", "is_completed", "completed_at"])
    return card


def get_review_cards(session):
    """Every completed card from the session, for the 24h review grid —
    normally up to 21 (3 x 7 days), fewer if the pool ran dry some days or
    a day was missed.

    No gate-questions prefetch here deliberately: the review must show the
    questions the member's stored ``answers_json.guesses`` were actually
    scored against, not the target's CURRENT (possibly re-picked) gate
    questions — see the caller (``views_connect_cycle.connect_week_review``),
    which resolves question text from the stored guess keys instead of
    ``CrushConnectMembership.active_gate_questions``.
    """
    return list(
        session.cards.filter(is_completed=True)
        .select_related(
            "target_user__crushprofile", "target_user__crush_connect_membership"
        )
        .order_by("day_number", "card_index")
    )


# ---------------------------------------------------------------------------
# Weekly request (one-or-none) + recipient inbox
# ---------------------------------------------------------------------------


def sync_request_state(weekly_request):
    """Flip a PENDING request past its 24h ``expires_at`` to EXPIRED and
    permanently exclude the pair — re-checked at every read/action point
    since the row itself is immutable once sent.

    Both writes are one ``transaction.atomic()`` block: this function's only
    entry guard is ``status == PENDING``, so a partial failure that left
    ``status`` at EXPIRED without the exclusion row would never be retried —
    every future call would see the row as already EXPIRED and skip both
    writes, silently and permanently breaking the "expired requests exclude
    the pair" guarantee. Atomicity makes a mid-write failure roll back to
    PENDING instead, so the next sync retries both writes together.
    """
    from crush_lu.models.crush_connect_cycle import ConnectPairExclusion, ConnectWeeklyRequest

    if (
        weekly_request.status == ConnectWeeklyRequest.Status.PENDING
        and timezone.now() > weekly_request.expires_at
    ):
        with transaction.atomic():
            weekly_request.status = ConnectWeeklyRequest.Status.EXPIRED
            weekly_request.save(update_fields=["status"])
            ConnectPairExclusion.exclude_pair(
                weekly_request.requester,
                weekly_request.recipient,
                reason=ConnectPairExclusion.Reason.REQUEST_EXPIRED,
            )
    return weekly_request


def can_send_weekly_request(session, requester, recipient) -> Tuple[bool, str]:
    """Whether ``requester`` may send their one weekly request to
    ``recipient`` from ``session``'s review. Returns ``(allowed, reason)`` —
    reason is a machine code for the view layer, mirroring
    ``services.crush_connect.can_send_spark``, including its "the card is an
    immutable snapshot, re-check live eligibility before acting on it" rule —
    the completed card was pinned when it was generated; the recipient may
    have been rejected, coach-excluded, or unlinked LuxID since. Also
    re-checks ``is_assigned_coach_pair``: ``get_cycle_eligible_pool`` only
    excludes coach/member pairs at card-*generation* time, and a coach
    assignment made after that (the door assigns one on first attendance)
    must retire the card, not just hide it from future pools — same
    reasoning as ``can_send_spark``."""
    from crush_lu.models.crush_connect_cycle import ConnectPairExclusion
    from crush_lu.services.blocking import is_blocked_pair
    from crush_lu.services.crush_connect import (
        is_assigned_coach_pair,
        is_catalogue_eligible,
    )

    if session.user_id != requester.pk:
        return False, "not_owner"
    if not session.is_review_active:
        return False, "review_closed"
    if not session.cards.filter(target_user=recipient, is_completed=True).exists():
        return False, "not_in_review"
    if session.weekly_requests.exists():
        # One-or-none is a one-shot action: once used, it's used regardless of
        # the outcome (pending/accepted/declined/expired/cancelled).
        return False, "already_sent"
    if is_assigned_coach_pair(requester, recipient):
        return False, "recipient_unavailable"
    if not is_catalogue_eligible(recipient):
        return False, "recipient_unavailable"
    if is_blocked_pair(requester, recipient):
        return False, "blocked"
    if ConnectPairExclusion.are_excluded(requester, recipient):
        return False, "excluded"
    return True, "ok"


def send_weekly_request(session, requester, recipient, request=None):
    """Create the session's single ``ConnectWeeklyRequest`` and notify the
    recipient. Raises ``ValueError(reason)`` when ``can_send_weekly_request``
    fails — callers should have checked first; this is the race-condition
    safety net."""
    from crush_lu.models.crush_connect_cycle import ConnectWeeklyRequest

    allowed, reason = can_send_weekly_request(session, requester, recipient)
    if not allowed:
        raise ValueError(reason)

    card = session.cards.get(target_user=recipient, is_completed=True)
    with transaction.atomic():
        weekly_request = ConnectWeeklyRequest.objects.create(
            session=session,
            requester=requester,
            recipient=recipient,
            target_card=card,
        )
    _notify_weekly_request_received(weekly_request, request=request)
    return weekly_request


def respond_to_weekly_request(weekly_request, accept: bool, request=None):
    """Record the recipient's decision on a pending weekly request.

    Accept -> opens a ``ConnectTemporaryChat`` (no UI yet — a follow-up PR
    builds on this row) and notifies the requester.
    Decline (explicit, ``accept=False``) -> always permanently excludes the
    pair; the requester is never told a decline happened (only that it's no
    longer pending), matching Sparks' silent-decline privacy contract.

    An accept where a block appeared, the requester lost catalogue
    eligibility (rejection, coach exclusion, LuxID unlink), or the pair
    became an assigned coach/client pair (a door check-in during the 24h
    response window), since the request was sent leaves the request
    untouched (still PENDING) rather than silently declining it — same
    discipline as ``services.crush_connect.respond_to_spark``. Unlike an
    explicit decline, this is often a transient/recoverable state
    (re-verify, re-link), so it must NOT write a permanent exclusion; the
    view layer's inbox listing (``get_pending_inbox``) already hides it from
    the recipient in the meantime, and this is the race-condition safety net
    for a request already open in a stale tab.
    """
    from crush_lu.models.crush_connect_cycle import (
        ConnectPairExclusion,
        ConnectTemporaryChat,
        ConnectWeeklyRequest,
    )
    from crush_lu.services.blocking import is_blocked_pair
    from crush_lu.services.crush_connect import (
        is_assigned_coach_pair,
        is_catalogue_eligible,
    )

    sync_request_state(weekly_request)
    if weekly_request.status != ConnectWeeklyRequest.Status.PENDING:
        return weekly_request

    if accept and (
        is_blocked_pair(weekly_request.requester, weekly_request.recipient)
        or is_assigned_coach_pair(weekly_request.requester, weekly_request.recipient)
        or not is_catalogue_eligible(weekly_request.requester)
    ):
        return weekly_request

    weekly_request.responded_at = timezone.now()

    # Each branch's status save + its dependent row (chat / permanent
    # exclusion) is one atomic unit — same reasoning as sync_request_state's:
    # this function only acts on status == PENDING, so a write that leaves
    # the status changed without its dependent row would never be retried.
    if accept:
        with transaction.atomic():
            weekly_request.status = ConnectWeeklyRequest.Status.ACCEPTED
            weekly_request.save(update_fields=["status", "responded_at"])
            ConnectTemporaryChat.objects.get_or_create(
                request=weekly_request,
                defaults={
                    "participant_1": weekly_request.requester,
                    "participant_2": weekly_request.recipient,
                },
            )
        _notify_weekly_request_accepted(weekly_request, request=request)
    else:
        with transaction.atomic():
            weekly_request.status = ConnectWeeklyRequest.Status.DECLINED
            weekly_request.save(update_fields=["status", "responded_at"])
            ConnectPairExclusion.exclude_pair(
                weekly_request.requester,
                weekly_request.recipient,
                reason=ConnectPairExclusion.Reason.REQUEST_DECLINED,
            )
    return weekly_request


def get_pending_inbox(user):
    """The recipient's live pending weekly requests — sync-checked for 24h
    expiry, filtered against blocks and assigned-coach pairs, and hidden once
    the requester loses catalogue eligibility (rejection, coach exclusion,
    LuxID unlink) since sending, mirroring
    ``crush_connect_sparks_received``'s ``exclude_assigned_coach_pairs`` +
    ``is_sender_eligible`` filters: accepting a request from someone who can
    no longer be reached on the platform, or who has since become the
    recipient's assigned coach/client, must not be offered."""
    from crush_lu.models.crush_connect_cycle import ConnectWeeklyRequest
    from crush_lu.services.blocking import blocked_user_ids
    from crush_lu.services.crush_connect import (
        exclude_assigned_coach_pairs,
        is_catalogue_eligible,
    )

    candidates = (
        exclude_assigned_coach_pairs(
            ConnectWeeklyRequest.objects.filter(
                recipient=user, status=ConnectWeeklyRequest.Status.PENDING
            ),
            user,
            field="requester_id",
        )
        .exclude(requester_id__in=blocked_user_ids(user))
        .select_related(
            "requester__crushprofile",
            "requester__crush_connect_membership",
            "target_card",
        )
        .order_by("-sent_at")
    )
    fresh = [sync_request_state(r) for r in candidates]
    return [
        r
        for r in fresh
        if r.status == ConnectWeeklyRequest.Status.PENDING
        and is_catalogue_eligible(r.requester)
    ]


# ---------------------------------------------------------------------------
# Notifications — in-app bell + email, never block the caller
# ---------------------------------------------------------------------------


def _notify_weekly_request_received(weekly_request, request=None):
    """In-app bell only for this PR — no dedicated email template yet
    (``request`` is accepted for signature symmetry with the Sparks
    notifiers and future use, but unused). Restrisiko: a recipient who
    doesn't check the bell has no other way to learn a request is waiting."""
    try:
        from django.urls import reverse
        from django.utils.translation import gettext as _g

        from crush_lu.models import Notification

        Notification.objects.create(
            user=weekly_request.recipient,
            notification_type="connect_week_request_received",
            title=_g("Someone wants to get to know you"),
            body=_g(
                "A Crush Connect member chose you after a full week of Connect "
                "encounters. Take a look and decide."
            ),
            link_url=reverse("crush_lu:connect_week_inbox"),
            metadata={"weekly_request_id": weekly_request.pk},
        )
    except Exception:  # pragma: no cover - notification must never block
        import logging

        logging.getLogger(__name__).exception(
            "Connect Week request-received notification failed"
        )


def _notify_weekly_request_accepted(weekly_request, request=None):
    try:
        from django.urls import reverse
        from django.utils.translation import gettext as _g

        from crush_lu.models import Notification

        Notification.objects.create(
            user=weekly_request.requester,
            notification_type="connect_week_request_accepted",
            title=_g("It's mutual!"),
            body=_g("%(name)s said yes — your chat is open.")
            % {"name": weekly_request.recipient.first_name or _g("They")},
            link_url=reverse("crush_lu:connect_week_home"),
            metadata={"weekly_request_id": weekly_request.pk},
        )
    except Exception:  # pragma: no cover
        import logging

        logging.getLogger(__name__).exception(
            "Connect Week request-accepted notification failed"
        )


# ---------------------------------------------------------------------------
# Post-cycle feedback (Task 13.4)
# ---------------------------------------------------------------------------

FEEDBACK_PROMPT_WINDOW_DAYS = 7


def get_pending_feedback_session(user):
    """The member's most recently COMPLETED cycle that still deserves a
    feedback prompt, or ``None``.

    Three conditions, and all three matter:

    - **Completed.** A cycle in REVIEW_OPEN is still being played; asking "how
      was your week" before the member has sent their one request would bias
      the answer and cost them the review time.
    - **No feedback row.** ``ConnectCycleFeedback`` is a ``OneToOne``, and a
      dismissal writes a row just like a submission does — so a member who
      closed the prompt is never asked about that cycle again.
    - **Completed within ``FEEDBACK_PROMPT_WINDOW_DAYS``.** This is the exit.
      ``connect_week_home`` opens a NEW session the moment the old one
      completes, so no page ever naturally stops rendering the prompt; without
      a recency window an unanswered, undismissed cycle would follow the member
      into every future week forever. A week-old verdict is also worth little,
      so nothing is lost by letting it lapse.

    Only the latest completed session is considered — a member returning after
    a long absence is asked about the week they just finished, never handed a
    backlog of surveys.
    """
    from crush_lu.models.crush_connect_cycle import ConnectWeekSession

    cutoff = timezone.now() - timedelta(days=FEEDBACK_PROMPT_WINDOW_DAYS)
    return (
        ConnectWeekSession.objects.filter(
            user=user,
            status=ConnectWeekSession.Status.COMPLETED,
            completed_at__isnull=False,
            completed_at__gte=cutoff,
            feedback__isnull=True,
        )
        .order_by("-completed_at")
        .first()
    )


def record_cycle_feedback(
    session, sentiment="", match_quality="", comment="", dismissed=False
):
    """Store one member's verdict on ``session`` (or their dismissal).

    ``get_or_create`` on the session, not ``create``: the prompt is a plain
    form POST, so a double-submit (impatient tap, back button) must land on the
    existing row rather than raising ``IntegrityError`` at the user. The first
    answer wins — a later POST for the same cycle is ignored, which is also why
    a stray dismissal cannot erase a verdict already given.

    Returns:
        tuple[ConnectCycleFeedback, bool]: the row and whether it was created.
    """
    from crush_lu.models.crush_connect_cycle import ConnectCycleFeedback

    return ConnectCycleFeedback.objects.get_or_create(
        session=session,
        defaults={
            "user": session.user,
            "sentiment": sentiment,
            "match_quality": match_quality,
            "comment": (comment or "").strip()[:1000],
            "dismissed": dismissed,
        },
    )
