"""Curated Speakeasy Noir personas for the guest randomizer.

A fixed, human-reviewed catalog rather than a free-form generator. Combining
unreviewed adjectives with unreviewed nouns eventually produces something that
gets the venue in trouble; a list this size can be read once and signed off.

Constraints every entry obeys, all of which come from the ticket and the room:

* <= 26 characters, so it centres on a 58mm ticket (32 columns) without wrapping.
* Pronounceable by an EN, DE and FR speaker — Luxembourg.
* Encodable in CP858 (see `printing.escpos`), so no typographic curiosities.
* Absurd or mysterious, never a comment on anyone's appearance or drinking.
"""

from __future__ import annotations

import random
from typing import Sequence

#: Human-reviewed noir personas. Order is irrelevant; the randomizer shuffles.
NOIR_PERSONAS: tuple[str, ...] = (
    "The Whispering Gambler",
    "The Velvet Silhouette",
    "The Midnight Chemist",
    "The Silent Alchemist",
    "The Paper Lantern",
    "The Quiet Accountant",
    "The Last Honest Cabbie",
    "The Understudy",
    "The Tuesday Widow",
    "The Borrowed Tuxedo",
    "The Locksmith",
    "The Understairs Tenor",
    "The Second Telegram",
    "The Unlit Cigarette",
    "The Cloakroom Oracle",
    "The Bookkeeper",
    "The Faded Postcard",
    "The Rooftop Botanist",
    "The Counterfeit Duchess",
    "The Night Porter",
    "The Slow Waltz",
    "The Missing Umbrella",
    "The Pawnbroker",
    "The Unsigned Letter",
    "The Velvet Rope",
    "The Third Witness",
    "The Piano Tuner",
    "The Orchid Smuggler",
    "The Broken Metronome",
    "The Quiet Cartographer",
    "The Empty Coat",
    "The Sunday Alibi",
    "The Hatcheck Ghost",
    "The Ledger Keeper",
    "The Amber Detective",
    "The Vanishing Guest",
    "The Foghorn",
    "The Patient Forger",
    "The Wrong Address",
    "The Marble Statue",
    "The Unclaimed Suitcase",
    "The Rain Collector",
    "The Backstage Diplomat",
    "The Silver Thimble",
    "The Late Arrival",
    "The Dockside Poet",
    "The Cold Reading",
    "The Last Tram Home",
)


def random_persona(
    *,
    exclude: Sequence[str] = (),
    rng: random.Random | None = None,
) -> str:
    """Return a persona, avoiding any currently in use at the venue.

    Two identical personas in one room is a delivery failure — the bartender
    cannot tell which ticket belongs to which table. `exclude` is normally the
    set of personas active tonight.

    If the venue somehow exhausts the catalog, this appends a roman numeral
    rather than raising: a guest must never see an error because the bar is
    busy. Rolling a name is the first thing they ever do here.
    """
    picker = rng or random.Random()
    taken = {p.casefold() for p in exclude}

    available = [p for p in NOIR_PERSONAS if p.casefold() not in taken]
    if available:
        return picker.choice(available)

    base = picker.choice(NOIR_PERSONAS)
    for numeral in ("II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X"):
        candidate = f"{base} {numeral}"
        if candidate.casefold() not in taken:
            return candidate
    return f"{base} {picker.randrange(11, 99)}"
