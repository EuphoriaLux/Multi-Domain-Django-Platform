"""
The Crush Empire economy.

Balance lives in code, not the database, so it can be retuned without a data
migration — EmpireState stores only a player's progress. Numbers are ported
unchanged from the prototype (crush-empire/crush-empire-swipe.html), which was
play-tested to ~90 minutes for the first million crushes.

Every function here takes state and returns a number. Nothing mutates. The
callers in services/ do the mutating, inside a transaction.
"""
import math

from django.utils.translation import gettext_lazy as _

# Cost of the n-th copy of a generator = base * GROWTH**owned. 1.15 is the
# genre standard: cheap enough that the next tier always feels reachable,
# steep enough that you must keep buying downward.
GROWTH = 1.15

# Each Heart permanently adds 2% to everything.
HEART_BONUS_PER_HEART = 0.02

# Prestige yield. Quadratic, so the tenth Heart costs 100x the first.
PRESTIGE_DIVISOR = 1_000_000

BASE_PER_LIKE = 2
BASE_PER_NOPE = 1

CLICK = "click"
GLOBAL = "global"

GENERATORS = [
    {"id": 0, "emoji": "👆", "base": 15,          "cps": 0.1,   "name": _("Ghost Account"),     "desc": _("Swipes on its own, replies never")},
    {"id": 1, "emoji": "🤳", "base": 100,         "cps": 1,     "name": _("Selfie Bot"),        "desc": _("Same pose, 40 times")},
    {"id": 2, "emoji": "💌", "base": 1_100,       "cps": 8,     "name": _("Auto-Opener"),       "desc": _("'hey :)' at industrial scale")},
    {"id": 3, "emoji": "🍸", "base": 12_000,      "cps": 47,    "name": _("Speed-Swipe Night"), "desc": _("Ten thumbs an hour")},
    {"id": 4, "emoji": "💎", "base": 130_000,     "cps": 260,   "name": _("Premium Boost"),     "desc": _("Pay to be seen ignoring people")},
    {"id": 5, "emoji": "🤖", "base": 1_400_000,   "cps": 1_400, "name": _("AI Wingman"),        "desc": _("Never runs out of openers")},
    {"id": 6, "emoji": "🏛️", "base": 20_000_000,  "cps": 7_800, "name": _("Swipe Factory"),     "desc": _("Romance, but make it a spreadsheet")},
    {"id": 7, "emoji": "🚀", "base": 330_000_000, "cps": 44_000, "name": _("Love Rocket"),      "desc": _("Swipes across the galaxy")},
]

# `req` gates visibility: {"swipes": n} or {"generator": tier, "count": n}.
UPGRADES = [
    {"id": 0, "emoji": "👍", "cost": 120,       "kind": CLICK,  "mult": 2, "name": _("Confident Thumb"),   "desc": _("Double points per swipe"), "req": {"swipes": 10}},
    {"id": 1, "emoji": "💬", "cost": 1_500,     "kind": CLICK,  "mult": 2, "name": _("Better Openers"),    "desc": _("Double points per swipe"), "req": {"swipes": 50}},
    {"id": 2, "emoji": "🌹", "cost": 40_000,    "kind": CLICK,  "mult": 3, "name": _("Grand Gestures"),    "desc": _("Triple points per swipe"), "req": {"swipes": 150}},
    {"id": 3, "emoji": "🔥", "cost": 5_000,     "kind": GLOBAL, "mult": 2, "name": _("Viral Profiles"),    "desc": _("All auto-swipers x2"),     "req": {"generator": 1, "count": 10}},
    {"id": 4, "emoji": "🌟", "cost": 250_000,   "kind": GLOBAL, "mult": 2, "name": _("Golden Hour"),       "desc": _("All auto-swipers x2"),     "req": {"generator": 3, "count": 5}},
    {"id": 5, "emoji": "💞", "cost": 9_000_000, "kind": GLOBAL, "mult": 3, "name": _("Perfect Chemistry"), "desc": _("All auto-swipers x3"),     "req": {"generator": 4, "count": 10}},
]

GENERATORS_BY_ID = {g["id"]: g for g in GENERATORS}
UPGRADES_BY_ID = {u["id"]: u for u in UPGRADES}

# A human cannot swipe faster than this. Used to bound how much a burst of
# swipe requests can be worth; see services.state.credit_swipe.
MAX_SWIPES_PER_SECOND = 8


def generator_cost(state, tier):
    """Price of the next copy. Server-side only — the client never sends a cost."""
    gen = GENERATORS_BY_ID[tier]
    return math.ceil(gen["base"] * GROWTH ** state.generator_count(tier))


def heart_bonus(state):
    return 1 + state.hearts * HEART_BONUS_PER_HEART


def _product(mults):
    total = 1
    for m in mults:
        total *= m
    return total


def global_multiplier(state):
    owned = set(state.upgrades or [])
    return _product(
        u["mult"] for u in UPGRADES if u["kind"] == GLOBAL and u["id"] in owned
    ) * heart_bonus(state)


def click_multiplier(state):
    owned = set(state.upgrades or [])
    return _product(
        u["mult"] for u in UPGRADES if u["kind"] == CLICK and u["id"] in owned
    )


def per_like(state):
    return max(1, round(BASE_PER_LIKE * click_multiplier(state) * heart_bonus(state)))


def per_nope(state):
    return max(1, round(BASE_PER_NOPE * heart_bonus(state)))


def crushes_per_second(state):
    base = sum(
        state.generator_count(g["id"]) * g["cps"] for g in GENERATORS
    )
    return base * global_multiplier(state)


def pending_hearts(state):
    """Hearts a prestige would pay out right now."""
    return int(math.sqrt(state.total_earned / PRESTIGE_DIVISOR))


def generator_unlocked(state, tier):
    """Tier 0 always; thereafter, once you own the tier below (or already own this one)."""
    return (
        tier == 0
        or state.generator_count(tier - 1) > 0
        or state.generator_count(tier) > 0
    )


def upgrade_unlocked(state, upgrade):
    req = upgrade["req"]
    if "swipes" in req:
        return state.swipes >= req["swipes"]
    if "generator" in req:
        return state.generator_count(req["generator"]) >= req["count"]
    return True
