"""Read-only analytics API behind the Crush Data MCP.

``GET /api/analytics/<tool>/`` with ``Authorization: Bearer <ANALYTICS_API_KEY>``.
The local ``crush-data-mcp`` stdio server (C:\\GitHub\\crush-data-mcp) exposes
each tool to AI agents. All data logic lives in
``crush_lu/services/analytics_readonly.py``.

Spec: ai-memory-hub/specs/2026-09-25-crush-data-mcp.md

The endpoint is dark (404) until the analytics keys and DB alias exist, which
is only ever on the production slot. It never writes, it takes only GET, and
every parameter is parsed into a bounded value before reaching a query.
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
from datetime import date, timedelta

from django.conf import settings
from django.core.cache import cache
from django.db import DatabaseError, OperationalError
from django.http import Http404, JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET

from crush_lu.decorators import ratelimit
from crush_lu.services import analytics_readonly as analytics

logger = logging.getLogger(__name__)

CACHE_SECONDS = 300
CACHE_PREFIX = "crush-analytics:v1:"


def authenticate_analytics_request(request) -> bool:
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return False
    expected = getattr(settings, "ANALYTICS_API_KEY", "")
    if not expected:
        return False
    return secrets.compare_digest(auth_header[len("Bearer ") :], expected)


# ---------------------------------------------------------------------------
# Parameter parsing: every value is validated and bounded here.
# ---------------------------------------------------------------------------


def _date(params, name, default=None):
    raw = params.get(name)
    if not raw:
        return default
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise analytics.ParamError(f"{name} must be YYYY-MM-DD")


def _range(params):
    today = timezone.localdate()
    end = _date(params, "to", today)
    start = _date(params, "from", end - timedelta(days=analytics.DEFAULT_RANGE_DAYS))
    if start > end:
        raise analytics.ParamError("from must be on or before to")
    if (end - start).days > analytics.MAX_RANGE_DAYS:
        raise analytics.ParamError(
            f"date range is limited to {analytics.MAX_RANGE_DAYS} days"
        )
    return start, end


def _int(params, name, default, low, high):
    raw = params.get(name)
    if raw in (None, ""):
        return default
    try:
        value = int(raw)
    except ValueError:
        raise analytics.ParamError(f"{name} must be an integer")
    if not low <= value <= high:
        raise analytics.ParamError(f"{name} must be between {low} and {high}")
    return value


def _choice(params, name, allowed):
    raw = params.get(name)
    if not raw:
        return None
    if raw not in allowed:
        raise analytics.ParamError(f"{name} must be one of {sorted(allowed)}")
    return raw


def _bool(params, name):
    raw = params.get(name)
    if raw in (None, ""):
        return None
    if raw.lower() in ("true", "1", "yes"):
        return True
    if raw.lower() in ("false", "0", "no"):
        return False
    raise analytics.ParamError(f"{name} must be true or false")


def _grain(params, default):
    return _choice(params, "grain", {"week", "month"}) or default


EVENT_TYPES = {value for value, _ in analytics.MeetupEvent.EVENT_TYPE_CHOICES}
CANTONS = {value for value, _ in analytics.MeetupEvent.CANTON_CHOICES}
VERIFICATION_STATUSES = {"incomplete", "pending", "verified", "rejected"}
GENDERS = {"M", "F", "NB", "O", "P"}
AGE_BAND_LABELS = {label for _, _, label in analytics.AGE_BANDS}
LOCATION_CODES = {
    "canton-capellen", "canton-clervaux", "canton-diekirch", "canton-echternach",
    "canton-esch", "canton-grevenmacher", "canton-luxembourg", "canton-mersch",
    "canton-redange", "canton-remich", "canton-vianden", "canton-wiltz",
    "border-belgium", "border-germany", "border-france",
}  # fmt: skip


def _parse_definitions(params):
    return {}


def _parse_kpi_weekly(params):
    return {"weeks": _int(params, "weeks", 12, 1, analytics.MAX_KPI_WEEKS)}


def _parse_funnel(params):
    start, end = _range(params)
    return {"start": start, "end": end, "grain": _grain(params, "week")}


def _parse_events(params):
    start, end = _range(params)
    return {
        "start": start,
        "end": end,
        "event_type": _choice(params, "event_type", EVENT_TYPES),
        "canton": _choice(params, "canton", CANTONS),
    }


def _parse_event_detail(params):
    if not params.get("event_id"):
        raise analytics.ParamError("event_id is required")
    return {"event_id": _int(params, "event_id", None, 1, 2**31 - 1)}


def _parse_payments(params):
    start, end = _range(params)
    return {"start": start, "end": end, "grain": _grain(params, "month")}


def _parse_window(params):
    start, end = _range(params)
    return {"start": start, "end": end}


def _parse_demographics(params):
    raw = params.get("group_by") or "gender,age_band"
    group_by = [part.strip() for part in raw.split(",") if part.strip()]
    unknown = [d for d in group_by if d not in analytics.GROUPABLE_DIMENSIONS]
    if not group_by or unknown or len(set(group_by)) != len(group_by):
        raise analytics.ParamError(
            f"group_by must be a comma list of distinct {list(analytics.GROUPABLE_DIMENSIONS)}"
        )
    return {
        "group_by": group_by,
        "verification_status": _choice(
            params, "verification_status", VERIFICATION_STATUSES
        ),
    }


def _parse_members(params):
    return {
        "signup_from": _date(params, "signup_from"),
        "signup_to": _date(params, "signup_to"),
        "verification_status": _choice(
            params, "verification_status", VERIFICATION_STATUSES
        ),
        "gender": _choice(params, "gender", GENDERS),
        "age_band_filter": _choice(params, "age_band", AGE_BAND_LABELS),
        "canton": _choice(params, "canton", LOCATION_CODES),
        "luxid": _bool(params, "luxid"),
        "attended": _bool(params, "attended"),
        "limit": _int(
            params, "limit", analytics.DEFAULT_MEMBER_ROWS, 1, analytics.MAX_MEMBER_ROWS
        ),
    }


TOOLS = {
    "definitions": (analytics.definitions, _parse_definitions),
    "kpi_weekly": (analytics.kpi_weekly, _parse_kpi_weekly),
    "funnel": (analytics.funnel, _parse_funnel),
    "events": (analytics.events, _parse_events),
    "event_detail": (analytics.event_detail, _parse_event_detail),
    "payments": (analytics.payments, _parse_payments),
    "connect": (analytics.connect, _parse_window),
    "retention": (analytics.retention, _parse_window),
    "demographics": (analytics.demographics, _parse_demographics),
    "members": (analytics.members, _parse_members),
}


def _error(message, status):
    response = JsonResponse({"error": message}, status=status)
    response["Cache-Control"] = "no-store"
    return response


@require_GET
@ratelimit(key="ip", rate="60/m", method="GET", block=True)
def analytics_tool(request, tool):
    if not analytics.is_configured():
        raise Http404
    if not authenticate_analytics_request(request):
        logger.warning("Unauthorized analytics API call for tool=%s", tool)
        return _error("Unauthorized", 401)
    if tool not in TOOLS:
        return JsonResponse(
            {"error": "unknown tool", "tools": sorted(TOOLS)}, status=404
        )

    handler, parse = TOOLS[tool]
    try:
        kwargs = parse(request.GET)
    except analytics.ParamError as exc:
        return _error(str(exc), 400)

    params = json.loads(json.dumps(kwargs, default=str, sort_keys=True))
    cache_key = (
        CACHE_PREFIX
        + hashlib.sha256(
            f"{tool}:{json.dumps(params, sort_keys=True)}".encode()
        ).hexdigest()
    )
    payload = cache.get(cache_key)
    if payload is None:
        try:
            data = handler(**kwargs)
        except analytics.NotFound as exc:
            return _error(str(exc), 404)
        except OperationalError as exc:
            if "statement timeout" in str(exc) or "canceling statement" in str(exc):
                return _error(
                    "query exceeded the 10 s limit; narrow the date range", 504
                )
            logger.exception("Analytics tool %s failed", tool)
            return _error("database unavailable", 503)
        except DatabaseError:
            logger.exception("Analytics tool %s failed", tool)
            return _error("database error", 500)
        payload = {
            "tool": tool,
            "params": params,
            "generated_at": timezone.now().isoformat(),
            "data": data,
        }
        cache.set(cache_key, payload, CACHE_SECONDS)
        logger.info("Analytics tool %s served", tool)

    response = JsonResponse(payload)
    response["Cache-Control"] = "no-store"
    return response
