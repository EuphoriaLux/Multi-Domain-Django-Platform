"""
Deck content.

TEMPORARY HOME. Once the scam tiers land, profiles move into admin-authored,
translatable models (ScamProfile / BioSegment) and cards are dealt by the server
one at a time, because the server must never tell the client which card is a
scam. Today no card has a right answer, so there is nothing to hide and the deck
is shipped to the page in one go.

Emoji avatars, not photographs: no likeness rights, no GDPR exposure, and no
temptation to build a dating game out of real faces. None of these people exist.
"""
from django.utils.translation import gettext_lazy as _

PROFILES = [
    {"emoji": "🧔",  "name": "Marc",   "age": 29, "bio": _("Loves long walks… to the fridge.")},
    {"emoji": "👩‍🦰", "name": "Sofia",  "age": 26, "bio": _("Fluent in sarcasm and three dead languages.")},
    {"emoji": "🧑‍🌾", "name": "Luc",    "age": 34, "bio": _("Owns 14 plants. Two are still alive.")},
    {"emoji": "💃",  "name": "Elena",  "age": 31, "bio": _("Will judge your Spotify Wrapped.")},
    {"emoji": "🧑‍💻", "name": "Ben",    "age": 28, "bio": _("Emotionally available 9am–9:03am.")},
    {"emoji": "🦸",  "name": "Nadia",  "age": 27, "bio": _("Red flags, but make it aesthetic.")},
    {"emoji": "🧑‍🍳", "name": "Tom",    "age": 33, "bio": _("Makes one (1) very good pasta.")},
    {"emoji": "👸",  "name": "Lea",    "age": 24, "bio": _("Looking for someone to ignore together.")},
    {"emoji": "🧑‍🎤", "name": "Rico",   "age": 30, "bio": _("Plays guitar. Only 'Wonderwall'.")},
    {"emoji": "🕵️",  "name": "Mira",   "age": 29, "bio": _("Will find your ex's new haircut in 4 minutes.")},
    {"emoji": "🧑‍🚀", "name": "Jonas",  "age": 32, "bio": _("Ambitious. Also asleep by 9:30.")},
    {"emoji": "🧜",  "name": "Chloé",  "age": 25, "bio": _("Water sign. Emotionally, a monsoon.")},
    {"emoji": "🐶",  "name": "Bruno",  "age": 35, "bio": _("It's the dog's profile. He's a catch.")},
    {"emoji": "🧑‍⚖️", "name": "Anouk",  "age": 28, "bio": _("Wins every argument, including this one.")},
    {"emoji": "🧑‍🔧", "name": "Pit",    "age": 37, "bio": _("Can fix your car, not your feelings.")},
]

# The funnel, delivered as the punchline. Surfaces every SWIPES_BETWEEN_META
# swipes; swiping either way just moves on.
META_CARDS = [
    {
        "title": _("Real people don't swipe."),
        "subtitle": _("That's kind of our whole thing."),
        "cta": _("See how Crush.lu works →"),
    },
    {
        "title": _("Still swiping? 😅"),
        "subtitle": _("On Crush.lu you'd have met someone by now."),
        "cta": _("Meet people for real →"),
    },
    {
        "title": _("Plot twist:"),
        "subtitle": _("the best match isn't in this deck. It's on Crush.lu."),
        "cta": _("Find yours →"),
    },
]

SWIPES_BETWEEN_META = 7

CRUSH_LU_URL = "https://crush.lu?src=game"


def deck_payload():
    """Resolve lazy translations for json_script."""
    return {
        "profiles": [
            {**p, "bio": str(p["bio"])} for p in PROFILES
        ],
        "meta": [
            {k: str(v) for k, v in card.items()} for card in META_CARDS
        ],
        "swipesBetweenMeta": SWIPES_BETWEEN_META,
        "crushLuUrl": CRUSH_LU_URL,
    }
