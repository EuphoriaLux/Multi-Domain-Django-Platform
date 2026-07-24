"""
Crush Connect service layer.

M1: eligible-pool query.
M2: Daily Drop selection.
Future: Spark send/quota (M5), mutual-spark transition (M6).

The model is ASYMMETRIC: receiving a Drop requires Premium (assigned coach),
but appearing in someone else's Drop does not. The candidate catalogue is
gated by LuxID instead — government-eID identity is the ticket in.

To be in *anyone's* pool a target must:
- have a verified CrushProfile (verification_status='verified')
- have a LuxID social account linked (the catalogue requirement)
- have a CrushConnectMembership with onboarded_at set (Crush Connect is opt-in)
- not be flagged by a coach via CrushConnectMembership.excluded_by_coach
- have logged in within the last 30 days (active membership signal)
- not already be in an EventConnection (any status) with the requester
- pass mutual gender + age preference filters
"""

from __future__ import annotations

import hashlib
import math
import random
from datetime import date, timedelta
from typing import TYPE_CHECKING, List, Tuple

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Exists, OuterRef, Q, QuerySet
from django.utils import timezone

if TYPE_CHECKING:
    from django.contrib.auth.models import User


User = get_user_model()


# Inactive members fall out of every Drop. The 30-day window matches the
# product-owner spec confirmed during M1 review (2026-05-14). Centralised
# so M2/M4 can read the same constant.
CONNECT_INACTIVITY_WINDOW_DAYS = 30

# Daily Drop sizing & boost. Tuneable here so M2 reflection can shift them
# without changing call sites.
DAILY_DROP_SIZE = 3
NEW_MEMBER_BOOST = 1.5
NEW_MEMBER_BOOST_WINDOW_DAYS = 30

# Soft-signal boosts for Drop selection (all multiplicative and >= 1.0, so they
# only ever lift a candidate, never zero them out). Gender/age stay the only
# HARD filters (in get_eligible_pool); everything here just reweights the pool.
SHARED_LANGUAGE_BOOST = 1.3  # any overlap in spoken languages
INTEREST_OVERLAP_BOOST_PER = 0.1  # per shared interest …
INTEREST_OVERLAP_CAP = 3  # … capped at 3 shared → max ×1.3
MATCHSCORE_NEUTRAL = 0.5  # missing MatchScore pair → neutral 0.5

# "Read-the-Photo" question-gated matching (M8/M9).
GATE_QUESTION_COUNT = 3  # each member picks exactly 3 gate questions
GATE_ALIGN_MIN = 2  # ≥2 of 3 guesses matching truth = "read" them
WEEKLY_CATALOGUE_SIZE = 12  # active questions surfaced in a week's set


def _years_ago(years: int) -> date:
    """Approximate date offset by ``years``; good enough for age-range filters."""
    today = date.today()
    try:
        return today.replace(year=today.year - years)
    except ValueError:  # Feb-29 edge case
        return today.replace(year=today.year - years, day=28)


def get_eligible_pool(user, candidate_pk=None) -> "QuerySet[User]":
    """
    Return the queryset of users eligible to appear in ``user``'s Crush Connect Drop.

    The requester must be eligible to RECEIVE (profile approved, PREMIUM =
    active PremiumMembership, onboarded into Crush Connect, not
    coach-excluded), otherwise an empty queryset. Candidates don't need
    Premium — the catalogue requires LuxID + opt-in instead (asymmetric model).

    ``candidate_pk`` narrows the pool to a single candidate BEFORE the Python
    gender-preference step below — point lookups ("is X in the pool?") must use
    it, otherwise the whole pool is materialized just to check one row.
    """
    from crush_lu.models import CrushProfile, EventConnection
    from crush_lu.services.blocking import block_exists_subquery

    # --- Requester self-eligibility -----------------------------------------
    user_profile = getattr(user, "crushprofile", None)
    if user_profile is None or not user_profile.is_approved:
        return User.objects.none()

    # Premium gate: receiving Drops requires an ACTIVE PremiumMembership.
    # assigned_coach alone is NOT the entitlement (backfill / attendance
    # auto-assign set it without payment).
    if not user_profile.has_active_premium:
        return User.objects.none()

    user_membership = getattr(user, "crush_connect_membership", None)
    if user_membership is None or not user_membership.is_onboarded:
        # Not opted in to Crush Connect yet — no Drop for them.
        return User.objects.none()

    # --- Target filters ------------------------------------------------------
    inactivity_cutoff = timezone.now() - timedelta(days=CONNECT_INACTIVITY_WINDOW_DAYS)

    # Pairs with an existing EventConnection are excluded from each other's
    # pools. The pre-`shared` crush exemption is **directional**, because the
    # privacy reason only exists on one side:
    #
    #  - incoming (someone declared on `user`): secret. Excluding the crusher
    #    would make an open Coach's Pick of them vanish, betraying the
    #    declaration — so the row is ignored and the pool is unchanged.
    #  - outgoing (`user` declared on the candidate): already known to them.
    #    Ignoring it would leave a Coach's Pick for their own crush live and
    #    acceptable, running a parallel Connect journey against the same
    #    person while the lead is open. It excludes like any other connection.
    existing_connection_subq = EventConnection.objects.filter(
        Q(requester=user, recipient=OuterRef("pk"))
        | (
            Q(requester=OuterRef("pk"), recipient=user)
            & ~(Q(flow=EventConnection.FLOW_CRUSH) & ~Q(status="shared"))
        )
    )

    # LuxID is mandatory for the candidate catalogue. SocialAccount is the
    # authoritative store — verification_method only records the FIRST
    # verification path, so coach-verified members who linked LuxID later
    # would be missed by a method check. Generic openid_connect accounts only
    # count when scoped to the LuxID SocialApp (the provider is shared with
    # non-LuxID apps) — see CrushProfile.luxid_account_querysets.
    luxid_native_subq, luxid_oidc_subq = CrushProfile.luxid_account_querysets(
        OuterRef("pk")
    )

    qs = (
        User.objects.filter(
            crushprofile__verification_status="verified",
            crush_connect_membership__onboarded_at__isnull=False,
            crush_connect_membership__excluded_by_coach=False,
            # "Read-the-Photo": the clear photo is only ever shown to the curated
            # few, and only for members who consented to that model. Members who
            # onboarded under the old blurred contract (consent defaults False)
            # are not surfaced until they re-consent.
            crush_connect_membership__photo_share_consent=True,
            last_login__gte=inactivity_cutoff,
        )
        # "Read-the-Photo" needs a photo: photo_1 is optional for event
        # verification, so a member can be verified yet photoless — or clear
        # their photo after onboarding. They must not be surfaced in Drops.
        .exclude(Q(crushprofile__photo_1="") | Q(crushprofile__photo_1__isnull=True))
        .annotate(
            _has_connection=Exists(existing_connection_subq),
            _has_block=block_exists_subquery(user),
            _has_luxid_native=Exists(luxid_native_subq),
            _has_luxid_oidc=Exists(luxid_oidc_subq),
        )
        .filter(_has_connection=False)
        .filter(_has_block=False)
        .filter(Q(_has_luxid_native=True) | Q(_has_luxid_oidc=True))
        .exclude(pk=user.pk)
        .select_related("crushprofile", "crush_connect_membership")
    )

    if candidate_pk is not None:
        qs = qs.filter(pk=candidate_pk)

    # Match preferences (gender + age) now live on CrushConnectMembership, not
    # CrushProfile — that's the catalogue/profile data split. The requester's
    # own gender/age/date_of_birth are still core identity on CrushProfile; only
    # the *preferences* (who they want to see) read from the membership.

    # --- Mutual gender preference (empty list = no preference, pass-through)
    user_pref_genders = user_membership.preferred_genders or []
    if user_pref_genders:
        qs = qs.filter(crushprofile__gender__in=user_pref_genders)

    if user_profile.gender:
        gender = user_profile.gender
        # JSONField array-containment lookups are unreliable on SQLite across
        # all supported versions. Evaluate in Python after select_related has
        # already loaded crush_connect_membership — no extra per-row queries.
        eligible_pks = [
            u.pk
            for u in qs
            if not u.crush_connect_membership.preferred_genders
            or gender in u.crush_connect_membership.preferred_genders
        ]
        qs = User.objects.filter(pk__in=eligible_pks).select_related(
            "crushprofile", "crush_connect_membership"
        )

    # --- Mutual age range ----------------------------------------------------
    # Targets' preferred range lives on membership (non-null defaults 18/99 keep
    # the query shape: migrated members never drop out for a missing value).
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
# Daily Drop selection
# ---------------------------------------------------------------------------


def _weight_for(
    candidate,
    *,
    today,
    viewer_languages,
    viewer_interest_ids,
    match_scores,
) -> float:
    """
    Blended selection weight for a candidate:

        weight = (0.5 + match_score) * language_boost * interest_boost * new_member_boost

    - ``match_score``: cached Ideal-Crush ``MatchScore.score_final`` for the
      (viewer, candidate) pair, in [0, 1]; a missing pair uses
      ``MATCHSCORE_NEUTRAL`` (0.5). The ``0.5 + score`` term spans [0.5, 1.5]
      and is *exactly* 1.0 at the neutral 0.5 — so a migrated member with no
      MatchScore contributes the same base weight as the old flat 1.0.
    - ``language_boost``: ``SHARED_LANGUAGE_BOOST`` when any spoken language
      overlaps, else 1.0.
    - ``interest_boost``: ``1 + 0.1 * min(overlap, 3)`` → 1.0 .. 1.3.
    - ``new_member_boost``: unchanged ×``NEW_MEMBER_BOOST`` inside the window.

    ``viewer_languages`` / ``viewer_interest_ids`` are frozensets and
    ``match_scores`` is a ``{candidate_pk: score_final}`` dict — all precomputed
    once by the caller so this stays a pure, allocation-light function.

    Parity: an un-enriched member (empty languages/interests, missing
    MatchScore) yields ``(0.5 + 0.5) * 1 * 1 * boost`` = exactly the old
    ``1.0`` / ``1.5`` — behaviour is identical to before this change.
    """
    membership = getattr(candidate, "crush_connect_membership", None)
    if membership is None or membership.onboarded_at is None:
        # Shouldn't happen — eligible-pool filter already requires onboarded.
        return 1.0

    # A *missing* pair is neutral (0.5 → base 1.0); a *stored* score_final of 0
    # is intentionally distinct (base 0.5, lower odds) — don't collapse them.
    match_score = match_scores.get(candidate.pk, MATCHSCORE_NEUTRAL)

    cand_languages = frozenset(membership.languages or [])
    language_boost = (
        SHARED_LANGUAGE_BOOST if (viewer_languages & cand_languages) else 1.0
    )

    cand_interest_ids = frozenset(
        i.pk for i in membership.interests.all()
    )  # prefetched
    overlap = len(viewer_interest_ids & cand_interest_ids)
    interest_boost = 1.0 + INTEREST_OVERLAP_BOOST_PER * min(
        overlap, INTEREST_OVERLAP_CAP
    )

    days_since_onboarding = (
        today - timezone.localtime(membership.onboarded_at).date()
    ).days
    new_member_boost = (
        NEW_MEMBER_BOOST
        if 0 <= days_since_onboarding <= NEW_MEMBER_BOOST_WINDOW_DAYS
        else 1.0
    )

    return (
        (MATCHSCORE_NEUTRAL + match_score)
        * language_boost
        * interest_boost
        * new_member_boost
    )


def _seeded_weighted_pick(
    candidates: List["User"],
    weights: List[float],
    k: int,
    seed: int,
) -> List["User"]:
    """
    Deterministic weighted sample without replacement using the
    A-Res keyed-reservoir method: for each candidate compute
    key = u^(1/w) where u is uniform(0,1] seeded by ``seed``+pk,
    pick the top-k by key.

    Equivalent to "Weighted Random Sampling without Replacement" (Efraimidis &
    Spirakis, 2006). Stable for ties — falls back to pk order — so the same
    (seed, candidates, weights) input always returns the same k items in the
    same order across runs and across machines.
    """
    if k <= 0 or not candidates:
        return []

    rng = random.Random(seed)
    # Seed once and burn a deterministic number of values per candidate by
    # generating a random for each. The order of `candidates` matters; we
    # rely on the eligible-pool already being ordered by pk for stability.
    keyed: List[Tuple[float, int, "User"]] = []
    for cand in candidates:
        w = max(weights[len(keyed)], 1e-9)
        u = rng.random() or 1e-12  # avoid log(0)
        key = math.log(u) / w  # equivalent ordering to u^(1/w), more stable
        keyed.append((key, cand.pk, cand))

    # Highest key wins (closest to 0 when log is negative; equivalently
    # we want max). Tiebreak by pk so result is deterministic.
    keyed.sort(key=lambda t: (-t[0], t[1]))
    return [cand for _, _, cand in keyed[:k]]


def get_or_create_daily_drop(user, drop_date: date | None = None):
    """
    Idempotently return the user's ``ConnectDailyDrop`` for the given date.

    First call for a (user, date) pair creates a snapshot by sampling up to
    ``DAILY_DROP_SIZE`` users from ``get_eligible_pool(user)`` with new-member
    boost. Subsequent calls return the same snapshot — refreshing the page
    never re-rolls the Drop.

    Returns ``None`` if the user isn't eligible for Connect at all (e.g. not
    onboarded yet). M4 will translate this into the teaser/empty-state UI.
    """
    from crush_lu.models import ConnectDailyDrop, MatchScore

    if drop_date is None:
        # Drops unlock at 06:00 local time. Visiting between 00:00–05:59 should
        # show the previous day's drop, not the upcoming one hours early.
        now = timezone.localtime()
        drop_date = (now - timedelta(days=1)).date() if now.hour < 6 else now.date()

    try:
        return ConnectDailyDrop.objects.get(user=user, drop_date=drop_date)
    except ConnectDailyDrop.DoesNotExist:
        pass

    # Prefetch the candidates' interests so _weight_for's overlap math is in
    # memory. The prefetch lives on the *caller's* queryset so it survives the
    # Python gender-fallback rebuild inside get_eligible_pool.
    pool_qs = get_eligible_pool(user).prefetch_related(
        "crush_connect_membership__interests"
    )
    if not pool_qs.exists():
        # Still record an empty Drop so we don't recompute the empty pool
        # every page load and so coaches can see "no candidates" in admin.
        with transaction.atomic():
            drop, _created = ConnectDailyDrop.objects.get_or_create(
                user=user, drop_date=drop_date
            )
        return drop

    candidates = list(pool_qs)  # order_by("pk") preserved from get_eligible_pool

    # --- Precompute the viewer's soft-signal inputs (once) ------------------
    viewer_membership = getattr(user, "crush_connect_membership", None)
    viewer_languages = frozenset(
        (viewer_membership.languages or []) if viewer_membership else []
    )
    viewer_interest_ids = frozenset(
        viewer_membership.interests.values_list("pk", flat=True)
        if viewer_membership
        else []
    )

    # One query for every cached MatchScore involving the viewer + pool. The
    # store is symmetric (user_a.pk < user_b.pk), so normalise into
    # {other_user_pk: score_final}; missing pairs fall back to neutral inside
    # _weight_for.
    pool_ids = [c.pk for c in candidates]
    match_scores = {}
    for row in MatchScore.objects.filter(
        Q(user_a=user, user_b__in=pool_ids) | Q(user_b=user, user_a__in=pool_ids)
    ).values("user_a_id", "user_b_id", "score_final"):
        other = row["user_b_id"] if row["user_a_id"] == user.pk else row["user_a_id"]
        match_scores[other] = row["score_final"]

    weights = [
        _weight_for(
            c,
            today=drop_date,
            viewer_languages=viewer_languages,
            viewer_interest_ids=viewer_interest_ids,
            match_scores=match_scores,
        )
        for c in candidates
    ]
    seed = int.from_bytes(
        hashlib.sha256(f"{user.pk}:{drop_date.isoformat()}".encode()).digest()[:8],
        "big",
    )

    chosen = _seeded_weighted_pick(candidates, weights, DAILY_DROP_SIZE, seed)

    with transaction.atomic():
        drop, _created = ConnectDailyDrop.objects.get_or_create(
            user=user, drop_date=drop_date
        )
        if not drop.recipients.exists():
            drop.recipients.set(chosen)
    return drop


# ---------------------------------------------------------------------------
# Weekly question rotation (M8)
# ---------------------------------------------------------------------------


def get_or_create_question_week(today: date | None = None):
    """
    Idempotently return the ``ConnectQuestionWeek`` for ``today``'s ISO week.

    First call for an ISO week builds the set by a deterministic weighted pick of
    up to ``WEEKLY_CATALOGUE_SIZE`` active questions (seeded by the ISO week, so
    every machine agrees and ``rotate_connect_questions`` re-runs are no-ops).
    Members pick their 3 gate questions FROM this snapshot's questions.
    """
    from crush_lu.models import ConnectQuestion, ConnectQuestionWeek

    if today is None:
        today = timezone.localdate()
    iso = today.isocalendar()
    iso_year, iso_week = iso.year, iso.week
    week_start = today - timedelta(days=iso.weekday - 1)  # Monday of this ISO week

    try:
        return ConnectQuestionWeek.objects.get(iso_year=iso_year, iso_week=iso_week)
    except ConnectQuestionWeek.DoesNotExist:
        pass

    candidates = list(ConnectQuestion.objects.filter(is_active=True).order_by("pk"))
    chosen = []
    if candidates:
        weights = [max(c.weight, 1) for c in candidates]
        seed = int.from_bytes(
            hashlib.sha256(f"qweek:{iso_year}:{iso_week}".encode()).digest()[:8],
            "big",
        )
        chosen = _seeded_weighted_pick(candidates, weights, WEEKLY_CATALOGUE_SIZE, seed)

    with transaction.atomic():
        week, _created = ConnectQuestionWeek.objects.get_or_create(
            iso_year=iso_year,
            iso_week=iso_week,
            defaults={"week_start": week_start},
        )
        if chosen and not week.questions.exists():
            week.questions.set(chosen)
    return week


def rotate_question_week(today: date | None = None):
    """Ensure the current (or given) ISO week's question set exists.

    Thin wrapper over :func:`get_or_create_question_week` for the management
    command / admin action; idempotent by construction.
    """
    return get_or_create_question_week(today)


def active_week_questions(today: date | None = None):
    """The active, still-enabled questions for this week's set (for pick forms)."""
    week = get_or_create_question_week(today)
    return week.questions.filter(is_active=True).order_by("category", "id")


# ---------------------------------------------------------------------------
# Curiosity Sparks (M5)
# ---------------------------------------------------------------------------


def is_catalogue_eligible(user) -> bool:
    """
    Whether ``user`` currently qualifies for the candidate catalogue:
    verified profile WITH a photo + LuxID linked + onboarded (not
    coach-excluded) + active within CONNECT_INACTIVITY_WINDOW_DAYS (same
    gate as Drops). The photo arm matters since fast-track event
    verification made photo_1 optional: photoless members must not be
    readable, and a member who clears their photo after onboarding drops
    out at the next action point.

    Drop snapshots and pending Sparks are immutable records — eligibility
    lost AFTER they were created (rejection, LuxID unlink, exclusion,
    inactivity) must be re-checked at every action point: sending a Spark
    to them, listing their received Sparks, and accepting one. The
    inactivity arm only ever bites the SEND side: a recipient acting on
    the site has, by definition, just logged in.
    """
    profile = getattr(user, "crushprofile", None)
    membership = getattr(user, "crush_connect_membership", None)
    inactivity_cutoff = timezone.now() - timedelta(days=CONNECT_INACTIVITY_WINDOW_DAYS)
    return bool(
        profile is not None
        and profile.verification_status == "verified"
        and profile.photo_1
        and profile.has_luxid_connected
        and membership is not None
        and membership.is_onboarded
        and membership.photo_share_consent
        and user.last_login is not None
        and user.last_login >= inactivity_cutoff
    )


def is_sender_eligible(user) -> bool:
    """
    Whether ``user`` currently qualifies to SEND Sparks (the receiver track):
    approved profile WITH a photo + active PremiumMembership + onboarded
    (not excluded) + has given photo-share consent. The photo arm mirrors
    catalogue eligibility: answering back exposes the sender's clear photo,
    so a photoless sender has nothing to show on that surface.

    Consent matters on the SEND side too now: reading a candidate exposes the
    sender's clear photo to that candidate on the answer-back surface, so a
    member who never consented (or revoked it) must not be able to send, be
    listed as a pending Spark, or be accepted.

    Like catalogue eligibility, this must be re-checked at accept time —
    a sender who was rejected, lost their Premium coach, got coach-excluded, or
    revoked photo consent after sending must not land in the accepted-sparks
    coach queue via an old pending Spark.
    """
    profile = getattr(user, "crushprofile", None)
    membership = getattr(user, "crush_connect_membership", None)
    return bool(
        profile is not None
        and profile.is_approved
        and profile.photo_1
        and profile.has_active_premium
        and membership is not None
        and membership.is_onboarded
        and membership.photo_share_consent
    )


def can_send_spark(sender, recipient) -> Tuple[bool, str]:
    """
    Whether ``sender`` may send a Curiosity Spark to ``recipient``.

    Returns ``(allowed, reason)`` — ``reason`` is a machine-readable code for
    the view layer ("not_receiver", "not_surfaced", "already_sparked",
    "recipient_unavailable", "blocked", "ok").

    Rules (asymmetric model):
    - Only Drop receivers (Premium + onboarded, not excluded) can send.
    - Neither party may have blocked the other.
    - The recipient must have actually appeared in one of the sender's Drops
      (the ConnectDailyDrop snapshot is the audit trail).
    - The recipient must STILL be catalogue-eligible — verified, LuxID
      linked, onboarded, not coach-excluded. Drop snapshots are immutable,
      so eligibility lost after surfacing (rejection, LuxID unlink) must be
      re-checked here, not assumed from the snapshot.
    - One Spark per pair, in either direction.
    """
    from crush_lu.models import ConnectDailyDrop, CuriositySpark
    from crush_lu.services.blocking import is_blocked_pair

    if not is_sender_eligible(sender):
        return False, "not_receiver"

    if is_blocked_pair(sender, recipient):
        return False, "blocked"

    if not is_catalogue_eligible(recipient):
        return False, "recipient_unavailable"

    if not ConnectDailyDrop.objects.filter(user=sender, recipients=recipient).exists():
        return False, "not_surfaced"

    existing = CuriositySpark.objects.filter(
        Q(sender=sender, recipient=recipient) | Q(sender=recipient, recipient=sender)
    ).first()
    if existing is not None:
        # A reverse pending Spark means the recipient already read the sender and
        # is waiting — the sender answering back is the "close the gate" path, not
        # a duplicate. submit_gate_answers handles this specially.
        if existing.sender_id == recipient.pk and existing.status == "pending":
            return False, "answer_back"
        return False, "already_sparked"

    return True, "ok"


def send_spark(sender, recipient, message: str = "", request=None, drop=None):
    """
    Create a Curiosity Spark and notify the recipient (in-app + email).

    Raises ``ValueError`` when ``can_send_spark`` fails — callers should have
    checked first; the raise is a safety net against race conditions.
    """
    from crush_lu.models import ConnectDailyDrop, CuriositySpark

    allowed, reason = can_send_spark(sender, recipient)
    if not allowed:
        raise ValueError(reason)

    if drop is None:
        drop = (
            ConnectDailyDrop.objects.filter(user=sender, recipients=recipient)
            .order_by("-drop_date")
            .first()
        )
    spark = CuriositySpark.objects.create(
        sender=sender,
        recipient=recipient,
        drop=drop,
        message=(message or "").strip(),
    )
    _notify_spark_received(spark, request=request)
    return spark


def respond_to_spark(spark, accept: bool, request=None):
    """
    Record the recipient's decision on a pending Spark.

    Accept → sender is notified (in-app + email) and the pair lands in the
    coach's accepted-sparks queue (admin) to arrange the date — the mutual
    reveal itself is M6.
    Decline → silent. The sender is never notified of a decline.
    """
    from crush_lu.services.blocking import is_blocked_pair

    if spark.status != "pending":
        return spark
    if accept and (
        is_blocked_pair(spark.sender, spark.recipient)
        or not (
            is_catalogue_eligible(spark.recipient) and is_sender_eligible(spark.sender)
        )
    ):
        # Either party lost eligibility since the Spark was created
        # (rejection, LuxID unlink, Premium loss, exclusion) or one blocked
        # the other — an accept must not fire the mutual notification or land
        # in the coach queue. Leave the Spark pending; the views filter these
        # out, so this is the race-condition safety net.
        return spark
    spark.status = "accepted" if accept else "declined"
    spark.responded_at = timezone.now()
    spark.save(update_fields=["status", "responded_at"])
    if accept:
        _notify_spark_accepted(spark, request=request)
    return spark


# ---------------------------------------------------------------------------
# Read-the-Photo answer gate (M9) — guesses drive the CuriositySpark state
# ---------------------------------------------------------------------------


def owner_gate_truths(profile_owner) -> dict:
    """``{question_id: owner_answer}`` for a member's current 3 gate questions."""
    membership = getattr(profile_owner, "crush_connect_membership", None)
    if membership is None:
        return {}
    return {gq.question_id: gq.owner_answer for gq in membership.gate_questions.all()}


def alignment_score(responder, profile_owner) -> int:
    """
    How many of ``responder``'s guesses match ``profile_owner``'s truth (0..3).

    Reads the owner's current gate questions, so re-picking retires stale
    alignment cleanly. Used only for the gate decision; the aggregate stat
    (``gate_answer_stats``) counts every guess regardless of correctness.
    """
    from crush_lu.models import ConnectQuestionAnswer

    truths = owner_gate_truths(profile_owner)
    if not truths:
        return 0
    guesses = ConnectQuestionAnswer.objects.filter(
        responder=responder,
        profile_owner=profile_owner,
        question_id__in=list(truths.keys()),
    ).values_list("question_id", "answer")
    return sum(1 for qid, ans in guesses if truths.get(qid) == ans)


def submit_gate_answers(
    responder,
    profile_owner,
    guesses: dict,
    request=None,
    drop_id=None,
):
    """
    Record ``responder``'s guesses at ``profile_owner``'s 3 questions and advance
    the match gate. ``guesses`` is ``{question_id: bool}``.

    Returns ``(outcome, spark)`` where outcome is:
      - ``"miss"``    — alignment < GATE_ALIGN_MIN; guesses recorded (feed the
                        aggregate stat) but no match, the pair stays where it was.
      - ``"sent"``    — first mover read the owner (≥ threshold) → pending Spark.
      - ``"matched"`` — the responder answered back a reverse pending Spark and
                        also read the owner → the pair is now accepted (mutual).

    Two eligibility regimes, mirroring the asymmetric Spark model:
      - FIRST MOVER (no Spark yet): only a Premium Drop-receiver may initiate, and
        only on someone surfaced in their Drop — ``can_send_spark``.
      - ANSWER-BACK (a reverse pending Spark exists): the responder is the
        *recipient* of that Spark and may be a candidate-track member (never a
        Drop-receiver), so gate by catalogue eligibility like ``respond_to_spark``.

    Raises ``ValueError(reason)`` when the guess set is invalid or the pair may
    not interact.
    """
    from crush_lu.models import ConnectDailyDrop, ConnectQuestionAnswer, CuriositySpark
    from crush_lu.services.blocking import is_blocked_pair

    truths = owner_gate_truths(profile_owner)
    if len(truths) < GATE_QUESTION_COUNT:
        raise ValueError("recipient_no_questions")
    if set(guesses.keys()) != set(truths.keys()):
        # The owner re-picked between page load and submit, or a tampered POST.
        raise ValueError("invalid_answers")

    forward = CuriositySpark.objects.filter(
        sender=responder, recipient=profile_owner
    ).first()
    reverse = CuriositySpark.objects.filter(
        sender=profile_owner, recipient=responder
    ).first()
    answering_back = reverse is not None and reverse.status == "pending"
    surfacing_drop = None

    if answering_back:
        # Recipient of an existing Spark reading the sender back. Catalogue
        # eligibility (candidates included); both parties must still be eligible.
        if (
            is_blocked_pair(responder, profile_owner)
            or not is_catalogue_eligible(responder)
            or not is_sender_eligible(profile_owner)
        ):
            raise ValueError("recipient_unavailable")
    elif forward is None:
        # Brand-new interaction → first-mover (Premium receiver) rules.
        allowed, reason = can_send_spark(responder, profile_owner)
        if not allowed:
            raise ValueError(reason)
        # The first mover must have their OWN 3 questions, or the recipient could
        # never answer back and the Spark would be unmatchable. Members onboarded
        # before the question step have none until they redo it.
        if len(owner_gate_truths(responder)) < GATE_QUESTION_COUNT:
            raise ValueError("no_own_questions")
        # One read per Drop: the rendered form carries its originating Drop id
        # so a stale tab cannot spend a newer Drop that also contains this target.
        if drop_id in (None, ""):
            surfacing_drop = (
                ConnectDailyDrop.objects.filter(
                    user=responder, recipients=profile_owner
                )
                .order_by("-drop_date")
                .first()
            )
        else:
            try:
                clean_drop_id = int(drop_id)
            except (TypeError, ValueError):
                raise ValueError("not_surfaced") from None
            surfacing_drop = ConnectDailyDrop.objects.filter(
                pk=clean_drop_id,
                user=responder,
                recipients=profile_owner,
            ).first()
            if surfacing_drop is None:
                raise ValueError("not_surfaced")
        if (
            surfacing_drop is not None
            and surfacing_drop.read_target_id
            and surfacing_drop.read_target_id != profile_owner.pk
        ):
            raise ValueError("drop_read_used")
    # else: forward Spark already exists → idempotent re-POST, no re-gating.

    with transaction.atomic():
        # Record the 3 guesses (idempotent on the unique constraint). Always
        # recorded — even a miss counts toward the owner's anonymous aggregate
        # stat, unless this transaction loses the Drop-read claim below.
        ConnectQuestionAnswer.objects.bulk_create(
            [
                ConnectQuestionAnswer(
                    responder=responder,
                    profile_owner=profile_owner,
                    question_id=qid,
                    answer=bool(val),
                )
                for qid, val in guesses.items()
            ],
            ignore_conflicts=True,
        )

        # Consume the Drop's one read (first-mover only). The isnull filter makes
        # first-write-win under concurrent submissions; a losing request for a
        # different card aborts before scoring or sending a Spark.
        if surfacing_drop is not None:
            claimed = ConnectDailyDrop.objects.filter(
                pk=surfacing_drop.pk, read_target__isnull=True
            ).update(read_target_id=profile_owner.pk, read_at=timezone.now())
            if not claimed:
                read_target_id = (
                    ConnectDailyDrop.objects.filter(pk=surfacing_drop.pk)
                    .values_list("read_target_id", flat=True)
                    .first()
                )
                if read_target_id != profile_owner.pk:
                    raise ValueError("drop_read_used")

        # Score against the PERSISTED guesses (the first ones — the unique
        # constraint locks them in), so a re-POST with better answers can't retry
        # a missed read.
        alignment = alignment_score(responder, profile_owner)

        if answering_back:
            if alignment >= GATE_ALIGN_MIN:
                respond_to_spark(reverse, accept=True, request=request)
                return "matched", reverse
            # Answered back but misread — no match; the Spark stays pending, silent.
            return "miss", reverse

        if forward is not None:
            # Idempotent re-POST after a prior read: report the pair's current state.
            return ("matched" if forward.status == "accepted" else "sent"), forward

        if alignment < GATE_ALIGN_MIN:
            # Silent miss — the first mover didn't read the owner well enough to reach
            # out. No Spark, no notification; guesses still feed the stat.
            return "miss", None

        # First mover clears the bar → open a pending Spark toward the owner.
        spark = send_spark(
            responder, profile_owner, request=request, drop=surfacing_drop
        )
        return "sent", spark


def gate_answer_stats(user) -> dict:
    """
    Anonymous aggregate of how people guessed each of ``user``'s gate questions.

    Returns ``{question_id: {"yes": int, "total": int}}`` — never per-responder
    identity. Powers the "8 of 12 think you work in Finance" viral stat.
    """
    from django.db.models import Count

    from crush_lu.models import ConnectQuestionAnswer

    rows = (
        ConnectQuestionAnswer.objects.filter(profile_owner=user)
        .values("question_id")
        .annotate(
            yes=Count("id", filter=Q(answer=True)),
            total=Count("id"),
        )
    )
    return {r["question_id"]: {"yes": r["yes"], "total": r["total"]} for r in rows}


def _notify_spark_received(spark, request=None):
    """In-app bell + email to the recipient. Never blocks the send flow."""
    try:
        from django.urls import reverse
        from django.utils.translation import gettext as _g

        from crush_lu.models import Notification

        Notification.objects.create(
            user=spark.recipient,
            notification_type="connect_spark_received",
            title=_g("Someone is curious about you"),
            body=_g(
                "A Crush Connect member sent you a Curiosity Spark. "
                "Take a look and decide."
            ),
            link_url=reverse("crush_lu:crush_connect_sparks_received"),
            metadata={"spark_id": spark.pk},
        )
    except Exception:  # pragma: no cover - notification must never block
        import logging

        logging.getLogger(__name__).exception("Spark-received notification failed")
    if request is not None:
        try:
            from crush_lu.email_helpers import send_connect_spark_received_email

            send_connect_spark_received_email(spark, request)
        except Exception:  # pragma: no cover
            import logging

            logging.getLogger(__name__).exception("Spark-received email failed")


def _notify_spark_accepted(spark, request=None):
    """In-app bell + email to the sender on acceptance."""
    try:
        from django.urls import reverse
        from django.utils.translation import gettext as _g

        from crush_lu.models import Notification

        Notification.objects.create(
            user=spark.sender,
            notification_type="connect_spark_accepted",
            title=_g("It's mutual!"),
            body=_g(
                "%(name)s is curious about you too. Your Crush Coach will "
                "be in touch to arrange your date."
            )
            % {"name": spark.recipient.first_name or _g("Your match")},
            link_url=reverse("crush_lu:crush_connect_home"),
            metadata={"spark_id": spark.pk},
        )
    except Exception:  # pragma: no cover
        import logging

        logging.getLogger(__name__).exception("Spark-accepted notification failed")
    if request is not None:
        try:
            from crush_lu.email_helpers import send_connect_spark_accepted_email

            send_connect_spark_accepted_email(spark, request)
        except Exception:  # pragma: no cover
            import logging

            logging.getLogger(__name__).exception("Spark-accepted email failed")


# ---------------------------------------------------------------------------
# Coach Picks (M7) — the coach-curated match proposal
# ---------------------------------------------------------------------------


def get_active_coach_pick(member):
    """The member's current 'proposed' pick with a still-eligible candidate,
    or None. A stale candidate hides the pick (coach re-picks)."""
    from crush_lu.models import ConnectCoachPick

    pick = (
        ConnectCoachPick.objects.filter(member=member, status="proposed")
        .select_related(
            "candidate__crushprofile",
            "candidate__crush_connect_membership",
            "coach__user",
        )
        .order_by("-created_at")
        .first()
    )
    if pick is None:
        return None
    # Full-pool re-check (subsumes catalogue eligibility) so display and
    # accept agree — otherwise a member could be stuck seeing a pick the
    # accept guard would refuse (e.g. EventConnection formed since).
    if not get_eligible_pool(member, candidate_pk=pick.candidate_id).exists():
        return None
    # Coach reassignment orphans the proposal — an ex-coach's pick must not
    # surface as "Your Coach's Pick" (and they couldn't act on a response).
    profile = getattr(member, "crushprofile", None)
    if profile is None or pick.coach_id != profile.assigned_coach_id:
        return None
    return pick


def propose_coach_pick(coach, member, candidate, note: str = ""):
    """
    Create a coach pick. Validates: member is the coach's assigned Premium
    member + Connect-onboarded; candidate is in the member's eligible pool;
    no pick already exists for this pair; only one open proposal at a time
    (a new pick withdraws the previous proposed one).

    Raises ValueError with a machine-readable reason on violation.
    """
    from crush_lu.models import ConnectCoachPick

    member_profile = getattr(member, "crushprofile", None)
    if member_profile is None or member_profile.assigned_coach_id != coach.pk:
        raise ValueError("not_your_member")
    if not is_sender_eligible(member):
        raise ValueError("member_not_ready")
    if not get_eligible_pool(member, candidate_pk=candidate.pk).exists():
        raise ValueError("candidate_not_eligible")
    if ConnectCoachPick.objects.filter(member=member, candidate=candidate).exists():
        raise ValueError("already_picked")

    ConnectCoachPick.objects.filter(member=member, status="proposed").update(
        status="withdrawn", responded_at=timezone.now()
    )
    pick = ConnectCoachPick.objects.create(
        coach=coach, member=member, candidate=candidate, note=(note or "").strip()
    )
    try:
        from django.urls import reverse
        from django.utils.translation import gettext as _g

        from crush_lu.models import Notification

        Notification.objects.create(
            user=member,
            notification_type="connect_coach_pick",
            title=_g("Your Crush Coach picked a match for you"),
            body=_g(
                "Take a look and decide — accept and your coach arranges the date."
            ),
            link_url=reverse("crush_lu:crush_connect_home"),
            metadata={"pick_id": pick.pk},
        )
    except Exception:  # pragma: no cover
        import logging

        logging.getLogger(__name__).exception("Coach-pick notification failed")
    return pick


def respond_to_coach_pick(pick, accept: bool):
    """Member accepts/declines the coach's pick. Either way the coach is
    notified (bell) — accept means 'contact the candidate and arrange the
    date', decline means 'pick someone else'. Idempotent after decision."""
    if pick.status != "proposed":
        return pick
    member_profile = getattr(pick.member, "crushprofile", None)
    coach_is_current = (
        member_profile is not None and pick.coach_id == member_profile.assigned_coach_id
    )
    if accept and not (
        is_sender_eligible(pick.member)
        and coach_is_current
        # Full pool re-check, not just catalogue eligibility: an
        # EventConnection created since the proposal, or changed mutual
        # preferences, must invalidate the pick exactly as it would block
        # proposing the same candidate today.
        and get_eligible_pool(pick.member, candidate_pk=pick.candidate_id).exists()
    ):
        # Either party lost eligibility since the pick was proposed — an
        # accept must not enter the coach's arrangement queue. The Today
        # page already hides stale picks; this guards old/raced POSTs.
        return pick
    pick.status = "accepted" if accept else "declined"
    pick.responded_at = timezone.now()
    pick.save(update_fields=["status", "responded_at"])
    if not accept and not coach_is_current:
        # Member's decline is recorded, but an ex-coach must not receive a
        # response for a member they no longer own.
        return pick
    try:
        from django.urls import reverse
        from django.utils.translation import gettext as _g

        from crush_lu.models import Notification

        if accept:
            title = _g("%(name)s accepted your pick") % {
                "name": pick.member.first_name or _g("Your member")
            }
            body = _g("Contact the candidate to confirm interest and arrange the date.")
        else:
            title = _g("%(name)s declined your pick") % {
                "name": pick.member.first_name or _g("Your member")
            }
            body = _g("Time to propose someone else.")
        Notification.objects.create(
            user=pick.coach.user,
            notification_type="connect_coach_pick_response",
            title=title,
            body=body,
            link_url=reverse("crush_lu:coach_connect_member", args=[pick.member_id]),
            metadata={"pick_id": pick.pk},
        )
    except Exception:  # pragma: no cover
        import logging

        logging.getLogger(__name__).exception("Pick-response notification failed")
    return pick
