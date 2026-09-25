"""Read-only analytics API behind the Crush Data MCP.

``GET /api/analytics/<tool>/`` with ``Authorization: Bearer <ANALYTICS_API_KEY>``.
The local ``crush-data-mcp`` stdio server (C:\\GitHub\\crush-data-mcp) exposes
each tool to AI agents. All data logic lives in
``crush_lu/services/analytics_readonly.py``.

Spec: ai-memory-hub/specs/2026-09-25-crush-data-mcp.md

The endpoint is dark (404, whatever the method or request rate) until the
analytics keys and DB alias exist, which is only ever on the production slot.
It never writes, it takes only GET, and every parameter is parsed into a
bounded value before reaching a query.
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
from django.utils import timezone, translation
from django.views.decorators.http import require_GET

from crush_lu.decorators import ratelimit
from crush_lu.services import analytics_readonly as analytics

logger = logging.getLogger(__name__)

CACHE_SECONDS = 300
CACHE_PREFIX = "crush-analytics:v1:"
# modeltranslation reads the active language's column (title_<lang>); the API
# pins one language so a cached payload is identical for every caller.
API_LANGUAGE = "en"


def authenticate_analytics_request(request) -> bool:
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return False
    expected = getattr(settings, "ANALYTICS_API_KEY", "")
    if not expected:
        return False
    return secrets.compare_digest(auth_header[len("Bearer ") :], expected)


# ---------------------------------------------------------------------------
# Parameter parsing: every value is validated and bounded here. Problems are
# collected as plain messages (no exceptions), so nothing but these fixed
# strings can ever reach a response body.
# ---------------------------------------------------------------------------


class Params:
    def __init__(self, query):
        self.query = query
        self.errors: list[str] = []

    def date(self, name, default=None):
        raw = self.query.get(name)
        if not raw:
            return default
        try:
            return date.fromisoformat(raw)
        except ValueError:
            self.errors.append(f"{name} must be YYYY-MM-DD")
            return default

    def range(self):
        # Both endpoints are inclusive (the service's window runs to the end of
        # `to`), so a window spans (end - start).days + 1 calendar days.
        today = timezone.localdate()
        end = self.date("to", today)
        start = self.date(
            "from", end - timedelta(days=analytics.DEFAULT_RANGE_DAYS - 1)
        )
        if start > end:
            self.errors.append("from must be on or before to")
        elif (end - start).days + 1 > analytics.MAX_RANGE_DAYS:
            self.errors.append(
                f"date range is limited to {analytics.MAX_RANGE_DAYS} days"
            )
        return start, end

    def int(self, name, default, low, high):
        raw = self.query.get(name)
        if raw in (None, ""):
            return default
        try:
            value = int(raw)
        except ValueError:
            self.errors.append(f"{name} must be an integer")
            return default
        if not low <= value <= high:
            self.errors.append(f"{name} must be between {low} and {high}")
            return default
        return value

    def choice(self, name, allowed):
        raw = self.query.get(name)
        if not raw:
            return None
        if raw not in allowed:
            self.errors.append(f"{name} must be one of {sorted(allowed)}")
            return None
        return raw

    def bool(self, name):
        raw = self.query.get(name)
        if raw in (None, ""):
            return None
        if raw.lower() in ("true", "1", "yes"):
            return True
        if raw.lower() in ("false", "0", "no"):
            return False
        self.errors.append(f"{name} must be true or false")
        return None

    def grain(self, default):
        return self.choice("grain", {"week", "month"}) or default


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


def _parse_definitions(p):
    return {}


def _parse_kpi_weekly(p):
    return {"weeks": p.int("weeks", 12, 1, analytics.MAX_KPI_WEEKS)}


def _parse_funnel(p):
    start, end = p.range()
    return {"start": start, "end": end, "grain": p.grain("week")}


def _parse_events(p):
    start, end = p.range()
    return {
        "start": start,
        "end": end,
        "event_type": p.choice("event_type", EVENT_TYPES),
        "canton": p.choice("canton", CANTONS),
    }


def _parse_event_detail(p):
    event_id = p.int("event_id", None, 1, 2**31 - 1)
    if event_id is None and not p.errors:
        p.errors.append("event_id is required")
    return {"event_id": event_id}


def _parse_payments(p):
    start, end = p.range()
    return {"start": start, "end": end, "grain": p.grain("month")}


def _parse_window(p):
    start, end = p.range()
    return {"start": start, "end": end}


def _parse_demographics(p):
    raw = p.query.get("group_by") or "gender,age_band"
    group_by = [part.strip() for part in raw.split(",") if part.strip()]
    unknown = [d for d in group_by if d not in analytics.GROUPABLE_DIMENSIONS]
    if not group_by or unknown or len(set(group_by)) != len(group_by):
        p.errors.append(
            "group_by must be a comma list of distinct "
            f"{list(analytics.GROUPABLE_DIMENSIONS)}"
        )
    return {
        "group_by": group_by,
        "verification_status": p.choice("verification_status", VERIFICATION_STATUSES),
    }


def _parse_members(p):
    return {
        "signup_from": p.date("signup_from"),
        "signup_to": p.date("signup_to"),
        "verification_status": p.choice("verification_status", VERIFICATION_STATUSES),
        "gender": p.choice("gender", GENDERS),
        "age_band_filter": p.choice("age_band", AGE_BAND_LABELS),
        "canton": p.choice("canton", LOCATION_CODES),
        "luxid": p.bool("luxid"),
        "attended": p.bool("attended"),
        "limit": p.int(
            "limit", analytics.DEFAULT_MEMBER_ROWS, 1, analytics.MAX_MEMBER_ROWS
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


def analytics_tool(request, tool):
    """Dark gate first: an unconfigured host answers 404 before the method
    check or the rate limiter could reveal that the route exists."""
    if not analytics.is_configured():
        raise Http404
    return _serve(request, tool)


@require_GET
@ratelimit(key="ip", rate="60/m", method="GET", block=True)
def _serve(request, tool):
    if not authenticate_analytics_request(request):
        logger.warning("Unauthorized analytics API call for tool=%s", tool)
        return _error("Unauthorized", 401)
    if tool not in TOOLS:
        return JsonResponse(
            {"error": "unknown tool", "tools": sorted(TOOLS)}, status=404
        )

    handler, parse = TOOLS[tool]
    params = Params(request.GET)
    kwargs = parse(params)
    if params.errors:
        return _error("; ".join(params.errors), 400)

    public_params = json.loads(json.dumps(kwargs, default=str, sort_keys=True))
    cache_key = (
        CACHE_PREFIX
        + hashlib.sha256(
            f"{tool}:{json.dumps(public_params, sort_keys=True)}".encode()
        ).hexdigest()
    )
    payload = cache.get(cache_key)
    if payload is None:
        try:
            with translation.override(API_LANGUAGE):
                data = handler(**kwargs)
        except analytics.NotFound:
            return _error("not found", 404)
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
            "params": public_params,
            "generated_at": timezone.now().isoformat(),
            "language": API_LANGUAGE,
            "data": data,
        }
        cache.set(cache_key, payload, CACHE_SECONDS)
        logger.info("Analytics tool %s served", tool)

    response = JsonResponse(payload)
    response["Cache-Control"] = "no-store"
    return response
