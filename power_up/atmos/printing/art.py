"""Curated ASCII art and event experience missions for printed tickets and mobile UI.

Every illustration is strictly valid ASCII (CP858/CP437 printable), width <= 24 cols,
so it centers cleanly on both 58mm (32 cols) and 80mm (48 cols) thermal rolls.
"""

from __future__ import annotations

import hashlib

# ---------------------------------------------------------------- ASCII Illustrations

COCKTAIL_COUPE = """\
   .-------.
   \\ ~ * ~ /
    \\  o  /
     \\   /
       |
      _|_"""

OLD_FASHIONED = """\
   .-------.
  |  .---.  |
  |  | # |  |
  |  `---'  |
  `---------'"""

BEER_STEIN = """\
    .-----.
   (  ~ ~  )
   |  | |  |__
   |  | |  |  )
   |  | |  |--'
   `-------'"""

WINE_CHALICE = """\
     .---.
    (  *  )
     \\ ~ /
      \\ /
       |
      _|_"""

PRETZEL_SNACK = """\
    .---.---.
   (  ( O )  )
    `---'---'"""

SPEAKEASY_FEDORA = """\
      .-----.
    .---------.
    |  _   _  |
   (  (O) (O)  )
    `---------'"""

ART_REGISTRY: dict[str, str] = {
    "coupe": COCKTAIL_COUPE,
    "tumbler": OLD_FASHIONED,
    "beer": BEER_STEIN,
    "wine": WINE_CHALICE,
    "snack": PRETZEL_SNACK,
    "fedora": SPEAKEASY_FEDORA,
}


def select_ascii_art(drink_names: tuple[str, ...] | list[str]) -> str:
    """Select the best matching ASCII illustration based on ordered items."""
    text = " ".join(drink_names).lower()

    if any(
        k in text
        for k in ("old fashioned", "mezcal", "sour", "whiskey", "bourbon", "rye")
    ):
        return OLD_FASHIONED
    if any(k in text for k in ("pilsner", "beer", "draft", "lager", "ipa", "cider")):
        return BEER_STEIN
    if any(
        k in text for k in ("wine", "red", "white", "rosé", "champagne", "prosecco")
    ):
        return WINE_CHALICE
    if any(
        k in text
        for k in (
            "cocktail",
            "fizz",
            "martini",
            "french 75",
            "spritz",
            "margarita",
            "gin",
        )
    ):
        return COCKTAIL_COUPE
    if any(
        k in text
        for k in ("nuts", "olive", "pretzel", "charcuterie", "snack", "bread", "cheese")
    ):
        return PRETZEL_SNACK

    return SPEAKEASY_FEDORA


# ---------------------------------------------------------------- Speakeasy Event Missions

SPEAKEASY_MISSIONS: tuple[str, ...] = (
    "Covert Toast: Raise your glass to the table across from yours without breaking character.",
    "The Informant: Share your persona's secret backstory with the person to your left.",
    "Password Protocol: Whisper 'The Velvet Falcon' when the bartender delivers your tray.",
    "Silent Detective: Try to deduce the alias of another guest in the room before your glass is empty.",
    "The Alibi: If anyone asks what you're doing here tonight, tell them you're waiting for 'The Professor'.",
    "High Society: Speak in your most dramatic 1920s voice for the next 5 minutes.",
    "The Secret Signal: Tap your glass twice when making eye contact with another table.",
    "The Secret Alliance: Propose a discreet joint toast with someone not seated at your table.",
)


def select_mission(seed_key: str) -> str:
    """Deterministically select a speakeasy mission for a given ticket/order key."""
    if not seed_key:
        return SPEAKEASY_MISSIONS[0]
    idx = int(hashlib.sha256(seed_key.encode("utf-8")).hexdigest(), 16) % len(
        SPEAKEASY_MISSIONS
    )
    return SPEAKEASY_MISSIONS[idx]
