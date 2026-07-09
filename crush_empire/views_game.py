from django.shortcuts import render
from django.utils.translation import gettext as _

from . import content
from .decorators import crush_empire_enabled, empire_login_required
from .services import state as state_service


def ui_strings():
    """
    Strings for empire.js.

    Emitted through `json_script`, not a data-* attribute. The quiz pages use
    `data-i18n='{"k":"{% trans %}"}'`, and that pattern silently truncates the
    moment a translation contains an apostrophe — it closes the single-quoted
    attribute. English got away with it; French would not survive "c'est" or
    "l'application". json_script escapes properly and is the built-in for this.

    "%s" placeholders are substituted client-side.
    """
    return {
        "perSec": _("/ sec"),
        "swipes": _("swipes"),
        "owned": _("owned"),
        "locked": _("keep swiping to unlock"),
        "allBought": _("All upgrades bought — you absolute charmer 😏"),
        "unlocked": _("unlocked!"),
        "prestigeConfirm": _(
            "Delete your account for %s Hearts? This resets your progress. "
            "Each Heart permanently boosts production."
        ),
        "prestigeDone": _("💍 You fell in love (with yourself). +%s Hearts."),
        "offline": _("While you were away: +%s 💘"),
        "tooPoor": _("Not enough crushes yet."),
        "signedOut": _("You have been signed out. Reload to keep playing."),
        "error": _("Something went wrong. Your progress is safe on the server."),
        # Scam layer
        "niceCatch": _("Nice catch. That one was a scam."),
        "falseReport": _("That was a real person. You cried wolf — streak lost."),
        "missed": _("That was a scam. You dodged it, but you reported nothing."),
        "catfished": _("You got catfished."),
        "compromised": _("ACCOUNT COMPROMISED — production halved"),
        "clearDebuff": _("Spend %s 🚩 to recover now"),
        "debuffCleared": _("Account secured. Production restored."),
        "gotIt": _("Got it"),
        # Tier 2
        "spotTitle": _("Spot the red flags"),
        "spotHint": _(
            "Tap every line that looks wrong — or clear it if the profile is fine."
        ),
        "itsFine": _("It's fine ✓"),
        "reportTapped": _("Report %s flag(s)"),
        "partial": _("You caught it, but you missed something."),
        "falseTap": _("You flagged an innocent line."),
        "timeoutGenuine": _("Out of time. No harm done, no reward either."),
        "clearedRight": _("Correct — nothing wrong with that one."),
        # Stamps slammed across the unmasked fish. Keep them short — they are set
        # at 18px in a 128px box and must not wrap.
        "stampGotcha": _("Gotcha"),
        "stampPartial": _("Half caught"),
        "stampAway": _("Swam away"),
        "stampCatfished": _("Catfished"),
    }


def teaser(request):
    """Public landing page. Shown when the flag is off, and to logged-out visitors."""
    return render(request, "crush_empire/teaser.html")


@crush_empire_enabled
@empire_login_required
def play(request):
    """
    The game. Flag-gated (staff bypass), session required.

    The initial state is embedded so the first paint has real numbers rather
    than zeros that snap a moment later. It is the same payload /api/game/sync/
    returns, and the client treats both as authoritative.
    """
    state, _offline = state_service.sync(request.user)
    return render(
        request,
        "crush_empire/play.html",
        {
            "initial_state": state_service.serialize(state),
            "deck": content.deck_payload(),
            "ui_strings": ui_strings(),
        },
    )
