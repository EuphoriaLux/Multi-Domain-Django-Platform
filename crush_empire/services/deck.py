"""
Dealing and resolving cards.

The single rule this module exists to enforce: **the client never learns whether
a card is a scam until after it has committed to an action.** Everything else —
the payoff table, the streak, the debuff — is downstream of that.

Two consequences that are easy to miss:

* draw() is idempotent while a challenge is open. Otherwise a player could draw
  repeatedly, watch which card ids reappear, or simply re-roll until they get a
  card they like. One open card at a time, and abandoning it is not free — the
  same card comes back.
* resolve() records the action *before* it reveals the answer, in one
  transaction. There is no request in which knowing the answer is useful.
"""
import random

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .. import economy
from ..models import CardChallenge, GameProfile
from ..models.challenge import ACTION_CLEAR, ACTION_LIKE, ACTION_NOPE, ACTION_REPORT
from .state import accrue, lock_state

ACTIONS = (ACTION_LIKE, ACTION_NOPE, ACTION_REPORT, ACTION_CLEAR)


class DeckError(Exception):
    """Bad challenge id, wrong owner, replayed, or expired."""


def _pool(is_scam, tier):
    qs = GameProfile.objects.filter(is_active=True, is_scam=is_scam)
    if tier == economy.TIER_MODAL:
        qs = qs.filter(tier2_eligible=True)
    return list(qs)


def _pick(rng, state):
    """
    Choose the tier first, then the kind — never the other way round.

    Deciding "is this a scam?" first and then "does a scam get the modal?" would
    correlate tier with kind, and the modal would announce the answer before the
    player read a word. Tier is chosen blind; the scam rate is then applied
    identically inside whichever pool was selected.
    """
    tier = economy.TIER_SWIPE
    if economy.tier2_unlocked(state) and rng.random() < economy.TIER2_DRAW_RATE:
        # Only escalate if BOTH pools can supply a card. A tier-2 draw that can
        # only ever be a scam is worse than no tier-2 draw at all.
        if _pool(True, economy.TIER_MODAL) and _pool(False, economy.TIER_MODAL):
            tier = economy.TIER_MODAL

    want_scam = rng.random() < economy.SCAM_CARD_RATE
    pool = _pool(want_scam, tier)
    if not pool:
        # A deck with no scam cards is still playable; a deck with no genuine
        # cards is not. Fall back rather than 500.
        pool = _pool(not want_scam, tier)
    if not pool:
        raise DeckError("deck is empty")

    return rng.choices(pool, weights=[p.weight for p in pool], k=1)[0], tier


def _card_payload(challenge):
    """
    What the client is allowed to see.

    Note what is absent: is_scam, is_red_flag, flag_type, explanation. Adding any
    of them here silently defeats the whole mechanic, which is why the test
    asserts on the key set rather than on individual keys.

    `tier` is safe to send only because tier-2 cards are drawn from the genuine
    pool at the same rate as the scam pool — see _pick(). `deadline` is the
    server's own clock, sent so the client can render a countdown; the client's
    opinion of when time ran out is never consulted.
    """
    profile = challenge.profile
    payload = {
        "challenge_id": str(challenge.id),
        "tier": challenge.tier,
        "emoji": profile.emoji,
        "name": profile.display_name,
        "age": profile.age,
        "segments": [
            {"id": s.id, "text": s.text}
            for s in profile.segments.all()
        ],
    }
    if challenge.tier == economy.TIER_MODAL:
        deadline = challenge.issued_at + timezone.timedelta(
            seconds=economy.TIER2_SECONDS
        )
        payload["deadline"] = deadline.isoformat()
        payload["seconds"] = economy.TIER2_SECONDS
    return payload


@transaction.atomic
def draw(user, rng=None):
    """Return the player's open card, dealing a new one only if none is open."""
    rng = rng or random

    open_challenge = (
        CardChallenge.objects.select_related("profile")
        .filter(user=user, resolved_at__isnull=True, expires_at__gt=timezone.now())
        .order_by("-issued_at")
        .first()
    )
    if open_challenge:
        # Idempotent, including the tier-2 clock: re-drawing does not buy time.
        return _card_payload(open_challenge)

    state = lock_state(user)
    profile, tier = _pick(rng, state)
    challenge = CardChallenge.objects.create(user=user, profile=profile, tier=tier)
    return _card_payload(challenge)


def _catfish(state):
    """Falling for a scam costs what you have to lose."""
    if economy.is_late_game(state):
        return "catfished", 0, 0, 0, True
    loss = int(state.points * economy.CATFISH_POINT_LOSS)
    return "catfished", -loss, 0, 0, False


def _score_swipe(state, is_scam, action):
    """
    Tier 1. Pure. Returns (outcome, points_delta, flags_delta, streak, debuff).

    `points_delta` may be negative (an early catfish). `debuff` is True when the
    engine should be throttled instead.
    """
    if not is_scam:
        if action == ACTION_LIKE:
            return "neutral", economy.per_like(state), 0, state.streak, False
        if action == ACTION_NOPE:
            return "neutral", economy.per_nope(state), 0, state.streak, False
        # Reported a real person. Costs the reward and the streak, not points.
        return "false_report", economy.GENUINE_REPORT_REWARD, 0, 0, False

    if action == ACTION_REPORT:
        streak = state.streak + 1
        return "correct", 0, economy.flag_award(streak), streak, False

    if action == ACTION_NOPE:
        # Dodged it, but told nobody. Deliberately zero, not one.
        return "missed", economy.SCAM_NOPE_REWARD, 0, state.streak, False

    return _catfish(state)


def _score_modal(state, profile, action, tapped, timed_out):
    """
    Tier 2. Pure.

    `tapped` is the set of segment ids the player marked as red flags. Scoring is
    `found − false_positives`, which is what kills the brute-force strategy: tap
    every line and you catch every flag, but you also accuse every innocent one,
    and the two cancel.

    A timeout is not "liked". On a scam the card slips past you, which is a
    catfish. On a genuine profile it is simply a lost reward — otherwise idling
    would pay the same as clearing correctly, and AFK would be a strategy.
    """
    flags = {s.id for s in profile.segments.all() if s.is_red_flag}
    innocent = {s.id for s in profile.segments.all()} - flags

    found = tapped & flags
    false_positives = tapped & innocent

    if timed_out:
        if profile.is_scam:
            return _catfish(state)
        return "timeout", 0, 0, state.streak, False

    if not profile.is_scam:
        if action == ACTION_CLEAR and not tapped:
            return "neutral", economy.per_like(state), 0, state.streak, False
        # Accused a real person of something specific. Worse than a vague report.
        return "false_report", economy.GENUINE_REPORT_REWARD, 0, 0, False

    # It is a scam.
    if action == ACTION_CLEAR or not found:
        # Waved it through, or tapped only the innocent lines.
        return _catfish(state)

    if false_positives:
        # Caught it, but smeared someone innocent on the way. No streak.
        return "partial", 0, economy.partial_flag_award(state.streak), state.streak, False

    if found == flags:
        streak = state.streak + 1
        return "correct", 0, economy.flag_award(streak), streak, False

    # Spotted something, missed something. Credit, but no streak.
    return "partial", 0, economy.partial_flag_award(state.streak), state.streak, False


@transaction.atomic
def resolve(user, challenge_id, action, tapped=None):
    if action not in ACTIONS:
        raise DeckError("unknown action")

    try:
        challenge = (
            CardChallenge.objects.select_for_update()
            .select_related("profile")
            # Scoping by user turns "someone else's challenge" into "no such
            # challenge" — no oracle for whether an id exists.
            .get(pk=challenge_id, user=user)
        )
    except (CardChallenge.DoesNotExist, ValidationError, ValueError):
        # A malformed uuid raises ValidationError from the field, not DoesNotExist.
        raise DeckError("unknown challenge")

    if challenge.resolved_at is not None:
        raise DeckError("already resolved")
    if challenge.expires_at <= timezone.now():
        raise DeckError("expired")

    # An action legal on one tier is nonsense on the other. Rejecting rather than
    # coercing keeps a tier-2 card from being answered as a cheap swipe.
    if challenge.tier == economy.TIER_MODAL:
        if action not in (ACTION_REPORT, ACTION_CLEAR):
            raise DeckError("illegal action for tier")
    elif action == ACTION_CLEAR:
        raise DeckError("illegal action for tier")

    state = lock_state(user)
    accrue(state)

    is_scam = challenge.profile.is_scam

    if challenge.tier == economy.TIER_MODAL:
        # Judged from the server's issued_at. A client that reports "I answered
        # in 3 seconds" after ninety is describing a hope, not a measurement.
        elapsed = (timezone.now() - challenge.issued_at).total_seconds()
        timed_out = elapsed > economy.TIER2_SECONDS + economy.TIER2_GRACE_SECONDS

        valid_ids = {s.id for s in challenge.profile.segments.all()}
        # Silently drop ids from other cards rather than trusting them: an id
        # that is not on this card can be neither a flag nor a false positive.
        tapped_ids = {int(i) for i in (tapped or []) if str(i).isdigit()} & valid_ids

        outcome, points, flags, streak, debuff = _score_modal(
            state, challenge.profile, action, tapped_ids, timed_out
        )
        challenge.tapped_segments = sorted(tapped_ids)
    else:
        outcome, points, flags, streak, debuff = _score_swipe(state, is_scam, action)

    state.swipes += 1
    if action == ACTION_LIKE:
        state.likes += 1
    elif action == ACTION_NOPE:
        state.nopes += 1

    if points > 0:
        state.points += points
        state.total_earned += points
    elif points < 0:
        # Never below zero: a fresh player who is catfished should not owe money.
        state.points = max(0, state.points + points)

    state.flags += flags
    state.streak = streak
    state.best_streak = max(state.best_streak, streak)

    if debuff:
        now = timezone.now()
        until = now + timezone.timedelta(seconds=economy.DEBUFF_SECONDS)
        # Stack by extending, not by replacing: a second catfish during a debuff
        # should not reset the clock to a shorter remaining time.
        state.debuff_until = max(state.debuff_until or now, until)

    state.save()

    challenge.resolved_at = timezone.now()
    challenge.action = action
    challenge.outcome = outcome
    challenge.reward_points = points
    challenge.reward_flags = flags
    challenge.streak_after = streak
    challenge.save()

    return state, {
        "outcome": outcome,
        "correct": outcome in ("correct", "neutral"),
        "tier": challenge.tier,
        "points": points,
        "flags": flags,
        "streak": streak,
        "debuffed": debuff,
        # Revealed only now, in the same response that records the answer.
        "reveal": {
            "is_scam": is_scam,
            "flags": [
                {
                    "segment_id": s.id,
                    "text": s.text,
                    "flag_type": s.flag_type,
                    "explanation": s.explanation,
                }
                for s in challenge.profile.segments.all()
                if s.is_red_flag
            ],
            "tapped": challenge.tapped_segments,
        },
    }


@transaction.atomic
def clear_debuff(user):
    state = lock_state(user)
    accrue(state)

    if not state.is_debuffed:
        raise DeckError("not debuffed")
    if state.flags < economy.DEBUFF_CLEAR_COST_FLAGS:
        raise DeckError("insufficient")

    state.flags -= economy.DEBUFF_CLEAR_COST_FLAGS
    state.debuff_until = None
    state.save()
    return state
