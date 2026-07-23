"""
Client-side deck furniture.

Profiles now live in the database (GameProfile / BioSegment) and are dealt one at
a time by services/deck.py, because the server must never tell the client which
card is a scam. What remains here is the meta card: an advert, not a game card.
It has no right answer, nothing to hide, and never touches the API.
"""
from django.utils.translation import gettext_lazy as _

# The funnel, delivered as the punchline. Surfaces every SWIPES_BETWEEN_META
# cards; swiping either way just moves on.
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
    {
        "title": _("Spotted a few liars?"),
        "subtitle": _("On Crush.lu everyone is LuxID-verified. Nobody is pretending."),
        "cta": _("See how verification works →"),
    },
]

SWIPES_BETWEEN_META = 7


def deck_payload():
    """
    Resolve lazy translations for json_script.

    Deliberately carries no URL. The meta card's CTA target is a literal in
    empire.js: assigning DOM-derived text to an `href` is how a `javascript:`
    URL gets executed, and this target never needs to be dynamic. Everything
    here reaches the DOM through .textContent, which cannot execute.
    """
    return {
        "meta": [{k: str(v) for k, v in card.items()} for card in META_CARDS],
        "swipesBetweenMeta": SWIPES_BETWEEN_META,
    }
