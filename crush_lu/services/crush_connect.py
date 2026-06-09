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

    The requester must be eligible to RECEIVE (profile approved, PREMIUM =
    personal coach assigned, onboarded into Crush Connect, not coach-excluded),
    otherwise an empty queryset. Candidates don't need Premium — the catalogue
    requires LuxID + opt-in instead (asymmetric model).
    """
    from crush_lu.models import CrushProfile, EventConnection, EventRegistration

    # --- Requester self-eligibility -----------------------------------------
    user_profile = getattr(user, "crushprofile", None)
    if user_profile is None or not user_profile.is_approved:
        return User.objects.none()

    # Premium gate: Crush Connect requires a personal coach (the Premium product).
    if not user_profile.assigned_coach_id:
        return User.objects.none()

    user_membership = getattr(user, "crush_connect_membership", None)
    if user_membership is None or not user_membership.is_onboarded:
        # Not opted in to Crush Connect yet — no Drop for them.
        return User.objects.none()

    # --- Target filters ------------------------------------------------------
    inactivity_cutoff = timezone.now() - timedelta(days=CONNECT_INACTIVITY_WINDOW_DAYS)

    existing_connection_subq = EventConnection.objects.filter(
        Q(requester=user, recipient=OuterRef("pk"))
        | Q(requester=OuterRef("pk"), recipient=user)
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
            last_login__gte=inactivity_cutoff,
        )
        .annotate(
            _has_connection=Exists(existing_connection_subq),
            _has_luxid_native=Exists(luxid_native_subq),
            _has_luxid_oidc=Exists(luxid_oidc_subq),
        )
        .filter(_has_connection=False)
        .filter(Q(_has_luxid_native=True) | Q(_has_luxid_oidc=True))
        .exclude(pk=user.pk)
        .select_related("crushprofile", "crush_connect_membership")
    )

    # --- Mutual gender preference (empty list = no preference, pass-through)
    user_pref_genders = user_profile.preferred_genders or []
    if user_pref_genders:
        qs = qs.filter(crushprofile__gender__in=user_pref_genders)

    if user_profile.gender:
        gender = user_profile.gender
        # JSONField array-containment lookups are unreliable on SQLite across
        # all supported versions. Evaluate in Python after select_related has
        # already loaded crushprofile — no extra per-row queries needed.
        eligible_pks = [
            u.pk for u in qs
            if not u.crushprofile.preferred_genders
            or gender in u.crushprofile.preferred_genders
        ]
        qs = (
            User.objects.filter(pk__in=eligible_pks)
            .select_related("crushprofile", "crush_connect_membership")
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

# ---------------------------------------------------------------------------
# Curiosity Sparks (M5)
# ---------------------------------------------------------------------------


def is_catalogue_eligible(user) -> bool:
    """
    Whether ``user`` currently qualifies for the candidate catalogue:
    verified profile + LuxID linked + onboarded (not coach-excluded) +
    active within CONNECT_INACTIVITY_WINDOW_DAYS (same gate as Drops).

    Drop snapshots and pending Sparks are immutable records — eligibility
    lost AFTER they were created (rejection, LuxID unlink, exclusion,
    inactivity) must be re-checked at every action point: sending a Spark
    to them, listing their received Sparks, and accepting one. The
    inactivity arm only ever bites the SEND side: a recipient acting on
    the site has, by definition, just logged in.
    """
    profile = getattr(user, "crushprofile", None)
    membership = getattr(user, "crush_connect_membership", None)
    inactivity_cutoff = timezone.now() - timedelta(
        days=CONNECT_INACTIVITY_WINDOW_DAYS
    )
    return bool(
        profile is not None
        and profile.verification_status == "verified"
        and profile.has_luxid_connected
        and membership is not None
        and membership.is_onboarded
        and user.last_login is not None
        and user.last_login >= inactivity_cutoff
    )


def can_send_spark(sender, recipient) -> Tuple[bool, str]:
    """
    Whether ``sender`` may send a Curiosity Spark to ``recipient``.

    Returns ``(allowed, reason)`` — ``reason`` is a machine-readable code for
    the view layer ("not_receiver", "not_surfaced", "already_sparked",
    "recipient_unavailable", "ok").

    Rules (asymmetric model):
    - Only Drop receivers (Premium + onboarded, not excluded) can send.
    - The recipient must have actually appeared in one of the sender's Drops
      (the ConnectDailyDrop snapshot is the audit trail).
    - The recipient must STILL be catalogue-eligible — verified, LuxID
      linked, onboarded, not coach-excluded. Drop snapshots are immutable,
      so eligibility lost after surfacing (rejection, LuxID unlink) must be
      re-checked here, not assumed from the snapshot.
    - One Spark per pair, in either direction.
    """
    from crush_lu.models import ConnectDailyDrop, CuriositySpark

    sender_profile = getattr(sender, "crushprofile", None)
    sender_membership = getattr(sender, "crush_connect_membership", None)
    if (
        sender_profile is None
        or not sender_profile.is_approved
        or not sender_profile.assigned_coach_id
        or sender_membership is None
        or not sender_membership.is_onboarded
    ):
        return False, "not_receiver"

    if not is_catalogue_eligible(recipient):
        return False, "recipient_unavailable"

    if not ConnectDailyDrop.objects.filter(
        user=sender, recipients=recipient
    ).exists():
        return False, "not_surfaced"

    if CuriositySpark.objects.filter(
        Q(sender=sender, recipient=recipient)
        | Q(sender=recipient, recipient=sender)
    ).exists():
        return False, "already_sparked"

    return True, "ok"


def send_spark(sender, recipient, message: str = "", request=None):
    """
    Create a Curiosity Spark and notify the recipient (in-app + email).

    Raises ``ValueError`` when ``can_send_spark`` fails — callers should have
    checked first; the raise is a safety net against race conditions.
    """
    from crush_lu.models import ConnectDailyDrop, CuriositySpark

    allowed, reason = can_send_spark(sender, recipient)
    if not allowed:
        raise ValueError(reason)

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
    if spark.status != "pending":
        return spark
    if accept and not is_catalogue_eligible(spark.recipient):
        # Eligibility lost since the Spark arrived (rejection, LuxID unlink,
        # exclusion) — an accept must not fire the mutual notification or
        # land in the coach queue. Leave the Spark pending; the views block
        # the page before this, so this is the race-condition safety net.
        return spark
    spark.status = "accepted" if accept else "declined"
    spark.responded_at = timezone.now()
    spark.save(update_fields=["status", "responded_at"])
    if accept:
        _notify_spark_accepted(spark, request=request)
    return spark


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
