"""
Every mutation of an EmpireState happens here, inside a transaction, under
select_for_update.

The client never sends a balance, a cost, or an elapsed time. It sends an
*intent* ("I swiped right", "buy generator 3") and the server prices it. That
removes the whole class of "edit a JS variable, get infinite points" cheats
rather than trying to detect them after the fact, which is why there is no
clamp-on-save here: there is no save to clamp.
"""
from django.db import transaction
from django.utils import timezone

from .. import economy
from ..models import EmpireState

# Idle earnings while away are real but bounded. Eight hours means you wake up
# to a satisfying pile; it also caps what a server-clock anomaly could mint.
OFFLINE_CAP_SECONDS = 8 * 60 * 60


def accrue(state):
    """
    Credit idle production since last_tick, using the *server's* clock.

    Called at the top of every mutating action, so the player's balance is
    always current before anything is priced against it.

    A debuff can expire part-way through the interval, so the elapsed time is
    split: seconds under ACCOUNT COMPROMISED earn at half rate, the rest at
    full. Charging the whole window at one rate would either forgive the
    penalty or over-punish someone who came back an hour later.
    """
    now = timezone.now()
    elapsed = (now - state.last_tick).total_seconds()
    state.last_tick = now

    if elapsed <= 0:  # clock skew or a double-submit within the same instant
        return 0

    elapsed = min(elapsed, OFFLINE_CAP_SECONDS)
    window_start = now - timezone.timedelta(seconds=elapsed)

    debuffed = 0.0
    if state.debuff_until and state.debuff_until > window_start:
        debuffed = (min(state.debuff_until, now) - window_start).total_seconds()

    rate = economy.crushes_per_second(state)
    earned = int(
        rate * ((elapsed - debuffed) + debuffed * economy.DEBUFF_MULTIPLIER)
    )
    if earned > 0:
        state.points += earned
        state.total_earned += earned
    return earned


def lock_state(user):
    state, _created = EmpireState.objects.get_or_create(user=user)
    return EmpireState.objects.select_for_update().get(pk=state.pk)


@transaction.atomic
def sync(user):
    """Heartbeat: bank idle production and hand back the authoritative state."""
    state = lock_state(user)
    offline = accrue(state)
    state.save()
    return state, offline


@transaction.atomic
def buy_generator(user, tier):
    state = lock_state(user)
    accrue(state)

    if tier not in economy.GENERATORS_BY_ID:
        raise ValueError("unknown generator")
    if not economy.generator_unlocked(state, tier):
        raise ValueError("locked")

    cost = economy.generator_cost(state, tier)
    if state.points < cost:
        raise ValueError("insufficient")

    state.points -= cost
    # JSON object keys are strings. Reading them back as ints is a bug magnet,
    # so we only ever write the string form.
    state.generators[str(tier)] = state.generator_count(tier) + 1
    state.save()
    return state


@transaction.atomic
def buy_upgrade(user, upgrade_id):
    state = lock_state(user)
    accrue(state)

    upgrade = economy.UPGRADES_BY_ID.get(upgrade_id)
    if upgrade is None:
        raise ValueError("unknown upgrade")
    if upgrade_id in (state.upgrades or []):
        raise ValueError("already owned")
    if not economy.upgrade_unlocked(state, upgrade):
        raise ValueError("locked")
    if state.points < upgrade["cost"]:
        raise ValueError("insufficient")

    state.points -= upgrade["cost"]
    state.upgrades = sorted([*(state.upgrades or []), upgrade_id])
    state.save()
    return state


@transaction.atomic
def prestige(user):
    """
    "Fall in Love": trade all progress for permanent Hearts.

    Hearts and flags survive; everything else resets. total_earned resets too,
    which is what makes each successive Heart cost quadratically more.
    """
    state = lock_state(user)
    accrue(state)

    gained = economy.pending_hearts(state)
    if gained < 1:
        raise ValueError("not enough")

    state.hearts += gained
    state.points = 0
    state.total_earned = 0
    state.generators = {}
    state.upgrades = []
    state.swipes = 0
    state.likes = 0
    state.nopes = 0
    state.save()
    return state, gained


def serialize(state):
    """
    The client's whole view of the world.

    Costs are computed here, so the price shown is the price the server will
    charge. The client has no cost formula to disagree with.
    """
    return {
        "points": state.points,
        "total_earned": state.total_earned,
        "hearts": state.hearts,
        "flags": state.flags,
        "swipes": state.swipes,
        "likes": state.likes,
        "nopes": state.nopes,
        # The rate the player is actually earning at, debuff included, so the
        # counter visibly halves when they get catfished.
        "cps": economy.effective_crushes_per_second(state),
        "per_like": economy.per_like(state),
        "per_nope": economy.per_nope(state),
        "heart_bonus_pct": round(state.hearts * economy.HEART_BONUS_PER_HEART * 100),
        "pending_hearts": economy.pending_hearts(state),
        # Scam layer
        "streak": state.streak,
        "best_streak": state.best_streak,
        "debuffed": state.is_debuffed,
        "debuff_until": state.debuff_until.isoformat() if state.debuff_until else None,
        "debuff_clear_cost": economy.DEBUFF_CLEAR_COST_FLAGS,
        "generators": [
            {
                "id": g["id"],
                "emoji": g["emoji"],
                "name": str(g["name"]),
                "desc": str(g["desc"]),
                "cps": g["cps"],
                "owned": state.generator_count(g["id"]),
                "cost": economy.generator_cost(state, g["id"]),
                "unlocked": economy.generator_unlocked(state, g["id"]),
            }
            for g in economy.GENERATORS
        ],
        "upgrades": [
            {
                "id": u["id"],
                "emoji": u["emoji"],
                "name": str(u["name"]),
                "desc": str(u["desc"]),
                "cost": u["cost"],
                "owned": u["id"] in (state.upgrades or []),
                "unlocked": economy.upgrade_unlocked(state, u),
            }
            for u in economy.UPGRADES
        ],
    }
