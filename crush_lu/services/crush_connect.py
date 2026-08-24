"""Crush Connect catalogue, question rotation, and coach-pick services.

Premium is the human curation layer: a member's assigned coach selects from
their eligible catalogue pool. Connect Week cards are generated separately in
``services.connect_cycle``; completing one never creates a match or message.

To be in a coach's eligible pool a target must:
- have a verified CrushProfile (verification_status='verified')
- have a LuxID social account linked (the catalogue requirement)
- have a CrushConnectMembership with onboarded_at set (Crush Connect is opt-in)
- not be flagged by a coach or paused by the member
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


# Inactive members fall out of the coach-pick pool. The 30-day window matches
# the product-owner spec confirmed during M1 review (2026-05-14).
CONNECT_INACTIVITY_WINDOW_DAYS = 30
MATCHSCORE_NEUTRAL = 0.5  # fallback used by Connect Week compatibility highlights

# "Read-the-Photo" question catalogue.
GATE_QUESTION_COUNT = 3  # each member picks exactly 3 gate questions
WEEKLY_CATALOGUE_SIZE = 12  # active questions surfaced in a week's set


def filter_connect_identity_verified(qs: QuerySet) -> QuerySet:
    """Keep users whose current Connect identity gate is still satisfied.

    This is shared by new-pool selection and persisted Connect Week card
    rendering. A card is an immutable audit snapshot, but it must not expose someone who
    has since unlinked LuxID or had their only attendance check-in undone.

    The attendance arm deliberately requires **coach-authenticated** attendance
    — a door grant, or an event whose coaches include the member's assigned
    coach — and not merely ``status="attended"``. Connect's identity gate is a
    claim that somebody looked at this person, and a bare ``attended`` row can
    be written by a self-scan (the attendee holds their own QR code), which
    attests nothing. This mirrors ``CrushProfile.has_attended_event``, which
    applies the same coach-provenance requirement. The narrowing excludes an
    admin/legacy-verified member whose only attendance was self-scanned; see
    ``test_self_scanned_attendance_alone_does_not_satisfy_the_identity_gate``.
    """
    from crush_lu.models import CrushProfile, EventRegistration

    luxid_native_subq, luxid_oidc_subq = CrushProfile.luxid_account_querysets(
        OuterRef("pk")
    )
    attended_event_subq = EventRegistration.objects.filter(
        user_id=OuterRef("pk"),
        status="attended",
    )
    attended_with_grant_subq = attended_event_subq.filter(
        checkin_granted_coach__isnull=False,
    )
    attended_with_assigned_coach_subq = EventRegistration.objects.filter(
        user_id=OuterRef("pk"),
        status="attended",
        event__coaches=OuterRef("crushprofile__assigned_coach_id"),
    )
    # A coach who scans a member that already has a coach grants no new one
    # (`assign_coach_on_first_attendance` is idempotent), and the event may be
    # run by a different coach than the one assigned — so neither subquery above
    # sees that scan. The photo attestation is the trace it does leave: only
    # coach-authenticated endpoints write it, and both revocation paths clear it
    # (`profile_verification._clear_door_photo_attestations`), so a non-empty
    # key is current evidence that a coach stood in front of this member.
    attended_with_attested_photo_subq = attended_event_subq.exclude(
        checkin_attested_photo_key="",
    )
    return (
        qs.annotate(
            _has_luxid_native=Exists(luxid_native_subq),
            _has_luxid_oidc=Exists(luxid_oidc_subq),
            _has_attended_with_grant=Exists(attended_with_grant_subq),
            _has_attended_with_assigned_coach=Exists(attended_with_assigned_coach_subq),
            _has_attended_with_attested_photo=Exists(attended_with_attested_photo_subq),
        )
        .filter(
            crushprofile__verification_status="verified",
        )
        .filter(
            Q(_has_luxid_native=True)
            | Q(_has_luxid_oidc=True)
            | Q(
                crushprofile__verification_method__in=(
                    "coach_event",
                    "premium_coach",
                )
            )
            | Q(_has_attended_with_grant=True)
            | Q(_has_attended_with_assigned_coach=True)
            | Q(_has_attended_with_attested_photo=True)
        )
    )


def _years_ago(years: int) -> date:
    """Approximate date offset by ``years``; good enough for age-range filters."""
    today = date.today()
    try:
        return today.replace(year=today.year - years)
    except ValueError:  # Feb-29 edge case
        return today.replace(year=today.year - years, day=28)


def is_assigned_coach_pair(user_a, user_b) -> bool:
    """True when either user is the ``CrushCoach`` assigned to the other.

    Coaches are ordinary Connect candidates — they are members too, and
    nothing excludes them from the pool at large. The one relationship that
    must never become a dating surface is a coach and their OWN assigned
    member: that coach curates the member's ``ConnectCoachPick``, reads their
    Connect data, and holds ``is_staff``. Surfacing them to each other puts a
    romantic proposition on top of that asymmetry.

    Checked in BOTH directions, because either side can be the member entering
    Connect Week or the candidate considered in a private coach pick.
    """
    from crush_lu.models import CrushCoach

    if user_a is None or user_b is None or user_a.pk == user_b.pk:
        return False

    for viewer, target in ((user_a, user_b), (user_b, user_a)):
        profile = getattr(viewer, "crushprofile", None)
        coach_pk = getattr(profile, "assigned_coach_id", None)
        if not coach_pk:
            continue
        coach_user_id = (
            CrushCoach.objects.filter(pk=coach_pk)
            .values_list("user_id", flat=True)
            .first()
        )
        if coach_user_id == target.pk:
            return True
    return False


def assigned_coach_pair_user_ids(user) -> set:
    """User ids that may never form a romantic pair with ``user``.

    The set form of :func:`is_assigned_coach_pair`, and it has to stay
    symmetric with it. Both directions matter, because a coach may also opt in
    and enter Connect Week like any other member:

      - the ``CrushCoach`` assigned to ``user``, and
      - when ``user`` IS a coach, every member assigned to them.

    Returned as ids so the same rule can be applied to catalogue and cycle
    querysets, the way ``blocked_user_ids`` is used.
    """
    from crush_lu.models import CrushCoach, CrushProfile

    if user is None or not getattr(user, "pk", None):
        return set()

    ids = set()

    profile = getattr(user, "crushprofile", None)
    assigned_coach_pk = getattr(profile, "assigned_coach_id", None)
    if assigned_coach_pk:
        ids.update(
            CrushCoach.objects.filter(pk=assigned_coach_pk).values_list(
                "user_id", flat=True
            )
        )

    coach = getattr(user, "crushcoach", None)
    if coach is not None:
        ids.update(
            CrushProfile.objects.filter(assigned_coach_id=coach.pk).values_list(
                "user_id", flat=True
            )
        )

    ids.discard(user.pk)
    return ids


def exclude_assigned_coach_pairs(qs, user, field="pk"):
    """Exclude ``user``'s coach/member counterparts from a queryset."""
    pair_ids = assigned_coach_pair_user_ids(user)
    if not pair_ids:
        return qs
    return qs.exclude(**{f"{field}__in": pair_ids})


def get_eligible_pool(user, candidate_pk=None) -> "QuerySet[User]":
    """
    The member must have an approved profile, active Premium membership, and
    actively participating in Connect (completed onboarding and not paused). Candidates
    do not need Premium; they need verified identity, Connect opt-in, consent, and mutual preferences.

    ``candidate_pk`` narrows the pool to a single candidate BEFORE the Python
    gender-preference step below — point lookups ("is X in the pool?") must use
    it, otherwise the whole pool is materialized just to check one row.
    """
    from crush_lu.models import EventConnection
    from crush_lu.services.blocking import block_exists_subquery

    # --- Requester self-eligibility -----------------------------------------
    user_profile = getattr(user, "crushprofile", None)
    if (
        user_profile is None
        or not user_profile.is_approved
        or not user_profile.is_active
        or not user.is_active
    ):
        return User.objects.none()

    # Premium gate: coach picks require an ACTIVE PremiumMembership.
    # assigned_coach alone is NOT the entitlement (backfill / attendance
    # auto-assign set it without payment).
    if not user_profile.has_active_premium:
        return User.objects.none()

    user_membership = getattr(user, "crush_connect_membership", None)
    if user_membership is None or not user_membership.is_participating:
        # Not opted in to Crush Connect or currently paused — no coach-pick pool for them.
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

    qs = (
        User.objects.filter(
            is_active=True,
            crushprofile__verification_status="verified",
            crushprofile__is_active=True,
            crush_connect_membership__onboarded_at__isnull=False,
            crush_connect_membership__excluded_by_coach=False,
            crush_connect_membership__paused_at__isnull=True,
            # "Read-the-Photo": the clear photo is only ever shown to the curated
            # few, and only for members who consented to that model.
            crush_connect_membership__photo_share_consent=True,
            last_login__gte=inactivity_cutoff,
        )
        # "Read-the-Photo" needs a photo: photo_1 is optional for event
        # verification, so a member can be verified yet photoless — or clear
        # their photo after onboarding. They must not be offered to a coach.
        .exclude(Q(crushprofile__photo_1="") | Q(crushprofile__photo_1__isnull=True))
        .annotate(
            _has_connection=Exists(existing_connection_subq),
            _has_block=block_exists_subquery(user),
        )
        .filter(_has_connection=False)
        .filter(_has_block=False)
        .exclude(pk=user.pk)
        .select_related("crushprofile", "crush_connect_membership")
    )
    # LuxID OR attended in-person event satisfies identity verification for
    # the candidate catalogue (Option B / Issue #539).
    qs = filter_connect_identity_verified(qs)

    # A coach and their own assigned member are never candidates for each
    # other, in either direction (see is_assigned_coach_pair). Coaches stay in
    # every other member's pool.
    qs = exclude_assigned_coach_pairs(qs, user)

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
# Catalogue and Premium eligibility
# ---------------------------------------------------------------------------


def is_catalogue_eligible(user) -> bool:
    """
    Whether ``user`` currently qualifies for the candidate catalogue:
    verified profile WITH a photo + LuxID linked + onboarded (not
    coach-excluded) + active within CONNECT_INACTIVITY_WINDOW_DAYS. The photo
    arm matters since fast-track event
    verification made photo_1 optional: photoless members must not be
    readable, and a member who clears their photo after onboarding drops
    out at the next action point.

    Eligibility is re-checked whenever a coach pool or cycle card is built.
    """
    profile = getattr(user, "crushprofile", None)
    membership = getattr(user, "crush_connect_membership", None)
    inactivity_cutoff = timezone.now() - timedelta(days=CONNECT_INACTIVITY_WINDOW_DAYS)
    return bool(
        profile is not None
        and profile.verification_status == "verified"
        and profile.photo_1
        and profile.is_connect_identity_verified
        and membership is not None
        and profile.is_active
        and user.is_active
        and membership.is_participating
        and membership.photo_share_consent
        and user.last_login is not None
        and user.last_login >= inactivity_cutoff
    )


def is_premium_connect_eligible(user) -> bool:
    """Whether ``user`` currently qualifies for a Premium coach pick."""
    profile = getattr(user, "crushprofile", None)
    membership = getattr(user, "crush_connect_membership", None)
    return bool(
        profile is not None
        and profile.is_approved
        and profile.photo_1
        and profile.has_active_premium
        and membership is not None
        and profile.is_active
        and user.is_active
        and membership.is_participating
        and membership.photo_share_consent
    )


def owner_gate_truths(profile_owner) -> dict:
    """``{question_id: owner_answer}`` for a member's current 3 gate questions."""
    membership = getattr(profile_owner, "crush_connect_membership", None)
    if membership is None:
        return {}
    return {gq.question_id: gq.owner_answer for gq in membership.gate_questions.all()}


def gate_answer_stats(user) -> dict:
    """
    Anonymous aggregate of how Connect Week members guessed ``user``'s current
    gate questions.

    Returns ``{question_id: {"yes": int, "total": int}}`` — never per-responder
    identity. Completed cards keep private guesses in ``answers_json`` for this
    aggregation only. Question ids that are no longer part of the member's
    current three are ignored.
    """
    from crush_lu.models import ConnectCycleCard

    current_question_ids = set(owner_gate_truths(user))
    stats = {
        question_id: {"yes": 0, "total": 0} for question_id in current_question_ids
    }
    if not current_question_ids:
        return stats

    payloads = ConnectCycleCard.objects.filter(
        target_user=user,
        is_completed=True,
    ).values_list("answers_json", flat=True)
    for payload in payloads.iterator(chunk_size=500):
        guesses = (payload or {}).get("guesses", {})
        if not isinstance(guesses, dict):
            continue
        for raw_question_id, answer in guesses.items():
            try:
                question_id = int(raw_question_id)
            except (TypeError, ValueError):
                continue
            if question_id not in current_question_ids or not isinstance(answer, bool):
                continue
            stats[question_id]["total"] += 1
            if answer:
                stats[question_id]["yes"] += 1
    return stats


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
    if not is_premium_connect_eligible(member):
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
            link_url=reverse("crush_lu:crush_connect_coach_pick"),
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
        is_premium_connect_eligible(pick.member)
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
