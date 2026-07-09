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
from ..models.challenge import ACTION_LIKE, ACTION_NOPE, ACTION_REPORT
from .state import accrue, lock_state

ACTIONS = (ACTION_LIKE, ACTION_NOPE, ACTION_REPORT)


class DeckError(Exception):
    """Bad challenge id, wrong owner, replayed, or expired."""


def _pick_profile(rng):
    """Weighted pick within a kind, kind chosen at SCAM_CARD_RATE."""
    want_scam = rng.random() < economy.SCAM_CARD_RATE
    pool = list(GameProfile.objects.filter(is_active=True, is_scam=want_scam))
    if not pool:
        # A deck with no scam cards is still playable; a deck with no genuine
        # cards is not. Fall back rather than 500.
        pool = list(GameProfile.objects.filter(is_active=True, is_scam=not want_scam))
    if not pool:
        raise DeckError("deck is empty")
    return rng.choices(pool, weights=[p.weight for p in pool], k=1)[0]


def _card_payload(challenge):
    """
    What the client is allowed to see.

    Note what is absent: is_scam, is_red_flag, flag_type, explanation. Adding any
    of them here silently defeats the whole mechanic, which is why the test
    asserts on the key set rather than on individual keys.
    """
    profile = challenge.profile
    return {
        "challenge_id": str(challenge.id),
        "emoji": profile.emoji,
        "name": profile.display_name,
        "age": profile.age,
        "segments": [
            {"id": s.id, "text": s.text}
            for s in profile.segments.all()
        ],
    }


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
        return _card_payload(open_challenge)

    challenge = CardChallenge.objects.create(user=user, profile=_pick_profile(rng))
    return _card_payload(challenge)


def _score(state, is_scam, action):
    """
    Pure. Returns (outcome, points_delta, flags_delta, streak, debuff).

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

    # Liked a scam.
    if economy.is_late_game(state):
        return "catfished", 0, 0, 0, True
    loss = int(state.points * economy.CATFISH_POINT_LOSS)
    return "catfished", -loss, 0, 0, False


@transaction.atomic
def resolve(user, challenge_id, action):
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

    state = lock_state(user)
    accrue(state)

    is_scam = challenge.profile.is_scam
    outcome, points, flags, streak, debuff = _score(state, is_scam, action)

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
                    "flag_type": s.flag_type,
                    "explanation": s.explanation,
                }
                for s in challenge.profile.segments.all()
                if s.is_red_flag
            ],
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
