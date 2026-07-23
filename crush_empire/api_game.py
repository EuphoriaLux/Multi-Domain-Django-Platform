"""
JSON endpoints for Crush Empire.

Plain Django JsonResponse with the {"success": bool, "error": str} envelope, the
same shape as crush_lu/api_pwa.py. Decorator stack is @empire_login_required +
@csrf_protect + @require_POST, and CSRF is enforced, not exempted.

Every endpoint POSTs, including sync/: they all mutate (idle production is
banked on the server clock on each call), so none of them are safe to GET.
"""
import json

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_POST

from crush_lu.decorators import ratelimit

from .decorators import empire_api_required
from .services import deck as deck_service
from .services import state as state_service
from .services.deck import DeckError

# A human tops out around economy.MAX_SWIPES_PER_SECOND. The server prices every
# card, so the worst a spammer gains is a few crushes — but bound it anyway.
SWIPE_RATE = "480/m"
ACTION_RATE = "120/m"


# The complete set of error codes the client may ever see. Services raise
# ValueError/DeckError carrying exactly these strings; anything else is a bug and
# must not reach the response body. Echoing str(exc) straight back would put
# internals — file paths, SQL, a stray f-string — in front of a player, which is
# what CodeQL's py/stack-trace-exposure warns about. Failing closed also keeps
# these codes a stable contract the front-end can branch on.
CLIENT_ERRORS = frozenset(
    {
        "insufficient",
        "insufficient flags",
        "locked",
        "already owned",
        "unknown generator",
        "unknown upgrade",
        "unknown challenge",
        "unknown action",
        "already resolved",
        "expired",
        "illegal action for tier",
        "not debuffed",
        "deck is empty",
    }
)

GENERIC_ERROR = "error"


def _ok(state, **extra):
    return JsonResponse({"success": True, "state": state_service.serialize(state), **extra})


def _err(message, status=400):
    return JsonResponse({"success": False, "error": message}, status=status)


def _err_from(exc, status=400):
    """Translate an exception into an allowlisted code, never its message."""
    code = str(exc)
    return _err(code if code in CLIENT_ERRORS else GENERIC_ERROR, status=status)


def _body(request):
    try:
        return json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return None


@empire_api_required
@csrf_protect
@require_POST
@ratelimit(key="user", rate=ACTION_RATE, method="POST")
def sync(request):
    """Heartbeat. Banks idle production since last_tick and returns the truth."""
    state, offline = state_service.sync(request.user)
    return _ok(state, offline_earned=offline)


@empire_api_required
@csrf_protect
@require_POST
@ratelimit(key="user", rate=SWIPE_RATE, method="POST")
def draw(request):
    """
    Deal the player's card.

    Idempotent while a card is open: calling this repeatedly returns the same
    card, so a player cannot re-roll until they get one they like, and cannot
    probe which ids come back.
    """
    try:
        card = deck_service.draw(request.user)
    except DeckError as exc:
        # An empty deck is a server-side content problem, not the player's fault.
        return _err_from(exc, status=503 if str(exc) == "deck is empty" else 400)

    return JsonResponse({"success": True, "card": card})


@empire_api_required
@csrf_protect
@require_POST
@ratelimit(key="user", rate=SWIPE_RATE, method="POST")
def resolve(request):
    """
    Answer the open card.

    This is the only response in the whole API that reveals whether a profile was
    a scam, and it only does so after the action has been written down.
    """
    data = _body(request)
    if data is None:
        return _err("Invalid JSON")

    tapped = data.get("tapped") or []
    if not isinstance(tapped, list):
        return _err("Invalid tapped")

    try:
        state, result = deck_service.resolve(
            request.user, data.get("challenge_id"), data.get("action"), tapped=tapped
        )
    except DeckError as exc:
        return _err_from(exc)

    return _ok(state, result=result)


@empire_api_required
@csrf_protect
@require_POST
@ratelimit(key="user", rate=ACTION_RATE, method="POST")
def clear_debuff(request):
    """Spend 🚩 to end ACCOUNT COMPROMISED early — the panic button."""
    try:
        state = deck_service.clear_debuff(request.user)
    except DeckError as exc:
        return _err_from(exc)

    return _ok(state)


@empire_api_required
@csrf_protect
@require_POST
@ratelimit(key="user", rate=ACTION_RATE, method="POST")
def buy(request):
    data = _body(request)
    if data is None:
        return _err("Invalid JSON")

    kind = data.get("kind")
    try:
        item_id = int(data.get("id"))
    except (TypeError, ValueError):
        return _err("Invalid id")

    try:
        if kind == "generator":
            state = state_service.buy_generator(request.user, item_id)
        elif kind == "upgrade":
            state = state_service.buy_upgrade(request.user, item_id)
        elif kind == "safety":
            state = state_service.buy_safety(request.user, item_id)
        else:
            return _err("Unknown kind")
    except ValueError as exc:
        # ValueError is broad — an unexpected one must not echo its message.
        # Note the client is never told a price here either; it re-reads the state.
        return _err_from(exc)

    return _ok(state)


@empire_api_required
@csrf_protect
@require_POST
@ratelimit(key="user", rate=ACTION_RATE, method="POST")
def prestige(request):
    try:
        state, gained = state_service.prestige(request.user)
    except ValueError:
        return _err("Not enough hearts to fall in love yet")

    return _ok(state, hearts_gained=gained)
