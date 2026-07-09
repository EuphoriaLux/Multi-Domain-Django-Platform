from django.shortcuts import render

from . import content
from .decorators import crush_empire_enabled, empire_login_required
from .services import state as state_service


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
        },
    )
