"""Deterministic procedural vignettes — the story engine's floor.

This is not filler. On a busy Friday with a flaky uplink this is what prints,
so it is written to be good enough that nobody at the table can tell which
engine produced their ticket.

Two properties matter and both come from seeding:

* **Deterministic.** The same ticket always yields the same vignette, so a
  reprint matches the original. A guest comparing a reprint against the first
  copy and finding a different story would break the fiction immediately.
* **Instant.** No network, no model, no lock. Bounded by a few string joins.
"""

from __future__ import annotations

import random
from typing import Sequence

from .chronicle import Chronicle, ChronicleEvent

_OPENERS: tuple[str, ...] = (
    "{persona} took the corner of table {table} and let the room forget them.",
    "Nobody saw {persona} arrive. Table {table} simply had one more shadow.",
    "{persona} sat at table {table} like someone waiting to be recognised.",
    "The lamp over table {table} flickered once for {persona}. Only once.",
    "{persona} arrived at table {table} owed money by at least three people.",
    "They gave the name {persona} at the door. Nobody asked for a second one.",
    "Table {table} had been empty an hour. Then {persona} made it look occupied.",
    "{persona} chose table {table} for its view of both exits.",
    "The coat came off at table {table}. {persona} kept the gloves on.",
    "{persona} crossed the floor to table {table} without disturbing the dust.",
)

_DRINKS: tuple[str, ...] = (
    "The order was {drink}. The bar had been expecting it since Tuesday.",
    "{drink}, they said. The bartender wrote it down twice.",
    "A {drink} for the table. Nobody asked what it was for.",
    "{drink} arrives the way news does here - quietly, and slightly late.",
    "The house knows what {drink} means. The house says nothing.",
    "They asked for {drink} by name, which is more than most manage.",
    "{drink}. An honest order in a room short on honest anything.",
    "The glass came back as {drink}. So did the silence.",
)

_LINKS: tuple[str, ...] = (
    "Two tables over, {other} pretended not to notice.",
    "{other} had ordered the same thing an hour ago. Nobody calls that chance.",
    "{other} looked up, then looked away. That counts as a conversation here.",
    "Word reached {other} before the drink reached the table.",
    "{other} has been watching the door since nine. Still is.",
    "Somewhere behind the pillar, {other} settled an old account.",
    "{other} raised a glass to nobody in particular. Possibly to this.",
)

_SOLOS: tuple[str, ...] = (
    "The room is still filling. For now the story is a short one.",
    "No witnesses yet. That will change by the second round.",
    "The piano player has the place to himself. He is not complaining.",
    "First of the night. Someone has to start the record.",
    "The chairs are all still cold. Give it an hour.",
)

_CLOSERS: tuple[str, ...] = (
    "The piano changed key. Nobody applauded.",
    "Somewhere a door closed twice.",
    "Outside, the rain took the night shift.",
    "The clock behind the bar is wrong, and everyone prefers it that way.",
    "Nothing else happened. That is the official version.",
    "The record skipped once and carried on, as we all do.",
    "The bill, like the truth, comes later.",
    "A cab idled at the kerb for no one.",
)


def _seed_from(key: str) -> int:
    """Stable across processes and Python runs, unlike `hash()`."""
    import hashlib

    return int.from_bytes(hashlib.sha256(key.encode("utf-8")).digest()[:8], "big")


def generate_fallback_vignette(
    event: ChronicleEvent,
    chronicle: Chronicle | None = None,
    *,
    seed_key: str | None = None,
    others: Sequence[str] | None = None,
) -> str:
    """Build a 3–4 line noir vignette with no network call.

    `seed_key` should be something stable and unique per order — the ticket
    code is ideal. It defaults to one derived from the event.
    """
    if others is None:
        others = (
            chronicle.active_personas(exclude=event.persona) if chronicle else ()
        )

    key = seed_key or f"{event.ticket_code}|{event.persona}|{event.table_label}"
    rng = random.Random(_seed_from(key))

    lines = [
        rng.choice(_OPENERS).format(persona=event.persona, table=event.table_label),
        rng.choice(_DRINKS).format(drink=event.headline_drink),
    ]

    if others:
        lines.append(rng.choice(_LINKS).format(other=rng.choice(list(others))))
    else:
        lines.append(rng.choice(_SOLOS))

    # A fourth line lands about half the time, so consecutive tickets do not
    # read as the same shape stamped repeatedly.
    if rng.random() < 0.5:
        lines.append(rng.choice(_CLOSERS))

    return "\n".join(lines)
