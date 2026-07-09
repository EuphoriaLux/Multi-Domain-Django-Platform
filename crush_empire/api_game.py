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
from .services import state as state_service

# A human tops out around economy.MAX_SWIPES_PER_SECOND. The server prices every
# swipe, so the worst a spammer gains is a few crushes — but bound it anyway.
SWIPE_RATE = "480/m"
ACTION_RATE = "120/m"


def _ok(state, **extra):
    return JsonResponse({"success": True, "state": state_service.serialize(state), **extra})


def _err(message, status=400):
    return JsonResponse({"success": False, "error": message}, status=status)


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
def swipe(request):
    data = _body(request)
    if data is None:
        return _err("Invalid JSON")

    try:
        state, gained = state_service.credit_swipe(request.user, data.get("direction"))
    except ValueError:
        return _err("Unknown direction")

    return _ok(state, gained=gained)


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
        else:
            return _err("Unknown kind")
    except ValueError as exc:
        # Note the client is never told a price here — it re-reads the state.
        return _err(str(exc))

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
