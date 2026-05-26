"""
Crush Connect service layer.

M1: eligible-pool query.
M2: Daily Drop selection.
Future: Spark send/quota (M5), mutual-spark transition (M6).

The eligible pool defines who can appear in another user's Drop. To be in
*anyone's* pool a target must:
- have a CrushProfile with is_approved=True
- have attended at least one event
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


def _years_ago(years: int) -> date:
    """Approximate date offset by ``years``; good enough for age-range filters."""
    today = date.today()
    try:
        return today.replace(year=today.year - years)
    except ValueError:  # Feb-29 edge case
        return today.replace(year=today.year - years, day=28)


def get_eligible_pool(user) -> "QuerySet[User]":
    """
    Return the queryset of users eligible to appear in ``user``'s Crush Connect Drop.

    The requester must also be eligible (profile approved, attended ≥1 event,
    onboarded into Crush Connect, not coach-excluded). Otherwise an empty queryset.
    """
    from crush_lu.models import EventConnection, EventRegistration

    # --- Requester self-eligibility -----------------------------------------
    user_profile = getattr(user, "crushprofile", None)
    if user_profile is None or not user_profile.is_approved:
        return User.objects.none()

    if not EventRegistration.objects.filter(user=user, status="attended").exists():
        return User.objects.none()

    user_membership = getattr(user, "crush_connect_membership", None)
    if user_membership is None or not user_membership.is_onboarded:
        # Not opted in to Crush Connect yet — no Drop for them.
        return User.objects.none()

    # --- Target filters ------------------------------------------------------
    inactivity_cutoff = timezone.now() - timedelta(days=CONNECT_INACTIVITY_WINDOW_DAYS)

    attended_subq = EventRegistration.objects.filter(
        user=OuterRef("pk"), status="attended"
    )
    existing_connection_subq = EventConnection.objects.filter(
        Q(requester=user, recipient=OuterRef("pk"))
        | Q(requester=OuterRef("pk"), recipient=user)
    )

    qs = (
        User.objects.filter(
            crushprofile__is_approved=True,
            crush_connect_membership__onboarded_at__isnull=False,
            crush_connect_membership__excluded_by_coach=False,
            last_login__gte=inactivity_cutoff,
        )
        .annotate(
            _has_attended=Exists(attended_subq),
            _has_connection=Exists(existing_connection_subq),
        )
        .filter(_has_attended=True, _has_connection=False)
        .exclude(pk=user.pk)
        .select_related("crushprofile", "crush_connect_membership")
    )

    # --- Mutual gender preference (empty list = no preference, pass-through)
    user_pref_genders = user_profile.preferred_genders or []
    if user_pref_genders:
        qs = qs.filter(crushprofile__gender__in=user_pref_genders)

    if user_profile.gender:
        qs = qs.filter(
            Q(crushprofile__preferred_genders=[])
            | Q(crushprofile__preferred_genders__contains=[user_profile.gender])
        )

    # --- Mutual age range ----------------------------------------------------
    user_age = user_profile.age
    if user_age is not None:
        qs = qs.filter(
            crushprofile__preferred_age_min__lte=user_age,
            crushprofile__preferred_age_max__gte=user_age,
        )

    pref_min = user_profile.preferred_age_min or 18
    pref_max = user_profile.preferred_age_max or 99
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


def _weight_for(candidate, *, today) -> float:
    """
    Compute the selection weight for a candidate.

    Currently: base weight 1.0, with a ×NEW_MEMBER_BOOST multiplier for users
    who onboarded into Crush Connect within the last NEW_MEMBER_BOOST_WINDOW_DAYS.
    Reflection point — geography/language/interest-overlap boosts can land here
    without touching call sites.
    """
    membership = getattr(candidate, "crush_connect_membership", None)
    if membership is None or membership.onboarded_at is None:
        # Shouldn't happen — eligible-pool filter already requires onboarded.
        return 1.0
    days_since_onboarding = (today - timezone.localtime(membership.onboarded_at).date()).days
    if 0 <= days_since_onboarding <= NEW_MEMBER_BOOST_WINDOW_DAYS:
        return NEW_MEMBER_BOOST
    return 1.0


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
    from crush_lu.models import ConnectDailyDrop

    if drop_date is None:
        # Drops unlock at 06:00 local time. Visiting between 00:00–05:59 should
        # show the previous day's drop, not the upcoming one hours early.
        now = timezone.localtime()
        drop_date = (now - timedelta(days=1)).date() if now.hour < 6 else now.date()

    try:
        return ConnectDailyDrop.objects.get(user=user, drop_date=drop_date)
    except ConnectDailyDrop.DoesNotExist:
        pass

    pool_qs = get_eligible_pool(user)
    if not pool_qs.exists():
        # Still record an empty Drop so we don't recompute the empty pool
        # every page load and so coaches can see "no candidates" in admin.
        with transaction.atomic():
            drop, _created = ConnectDailyDrop.objects.get_or_create(
                user=user, drop_date=drop_date
            )
        return drop

    candidates = list(pool_qs)
    weights = [_weight_for(c, today=drop_date) for c in candidates]
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
