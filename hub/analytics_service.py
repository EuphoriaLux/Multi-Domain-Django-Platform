"""Read-only aggregate analytics for the staff Hub.

External credentials remain server-side.  Every provider returns aggregate rows and
is isolated so a temporary upstream failure cannot hide the Django activation data.
"""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from urllib.parse import urlsplit

import requests
from azure.identity import DefaultAzureCredential
from django.conf import settings
from django.db.models import Count, Q
from django.utils import timezone
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import (
    DateRange,
    Dimension,
    Metric,
    RunReportRequest,
)
from google.oauth2 import service_account
from googleapiclient.discovery import build

from crush_lu.models import CrushProfile, WeeklyMetricsSnapshot

logger = logging.getLogger(__name__)

GOOGLE_SCOPES = (
    "https://www.googleapis.com/auth/webmasters.readonly",
    "https://www.googleapis.com/auth/analytics.readonly",
)
APP_INSIGHTS_SCOPE = "https://api.applicationinsights.io/.default"


class AnalyticsConfigurationError(RuntimeError):
    """Raised when a provider is not configured for read-only analytics."""


@dataclass(frozen=True)
class ProviderResult:
    status: str
    data: dict
    message: str | None = None


def _normalise_path(value: str) -> str:
    parsed = urlsplit(value)
    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/")
    return path


def _google_credentials():
    raw = getattr(settings, "GOOGLE_INDEXING_KEY_JSON", "")
    if not raw:
        raise AnalyticsConfigurationError(
            "Google analytics credentials are unavailable."
        )
    try:
        info = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise AnalyticsConfigurationError(
            "Google analytics credentials are invalid."
        ) from exc
    return service_account.Credentials.from_service_account_info(
        info,
        scopes=list(GOOGLE_SCOPES),
    )


def fetch_search_console(start_date: date, end_date: date) -> dict:
    """Return Search Console totals, daily history and page aggregates."""
    site_url = getattr(settings, "HUB_ANALYTICS_GSC_SITE_URL", "sc-domain:crush.lu")
    service = build(
        "searchconsole",
        "v1",
        credentials=_google_credentials(),
        cache_discovery=False,
    )

    def query(dimensions: list[str], row_limit: int) -> list[dict]:
        response = (
            service.searchanalytics()
            .query(
                siteUrl=site_url,
                body={
                    "startDate": start_date.isoformat(),
                    "endDate": end_date.isoformat(),
                    "dimensions": dimensions,
                    "rowLimit": row_limit,
                    "dataState": "final",
                },
            )
            .execute()
        )
        return response.get("rows", [])

    summary_rows = query([], 1)
    summary_row = summary_rows[0] if summary_rows else {}
    daily = [
        {
            "date": row["keys"][0],
            "clicks": int(row.get("clicks", 0)),
            "impressions": int(row.get("impressions", 0)),
            "ctr": float(row.get("ctr", 0)),
            "position": float(row.get("position", 0)),
        }
        for row in query(["date"], 25_000)
    ]
    pages = [
        {
            "path": _normalise_path(row["keys"][0]),
            "clicks": int(row.get("clicks", 0)),
            "impressions": int(row.get("impressions", 0)),
            "ctr": float(row.get("ctr", 0)),
            "position": float(row.get("position", 0)),
        }
        for row in query(["page"], 25_000)
    ]
    return {
        "summary": {
            "clicks": int(summary_row.get("clicks", 0)),
            "impressions": int(summary_row.get("impressions", 0)),
            "ctr": float(summary_row.get("ctr", 0)),
            "position": float(summary_row.get("position", 0)),
        },
        "daily": sorted(daily, key=lambda row: row["date"]),
        "pages": pages,
    }


def _ga4_report(
    client,
    property_id: str,
    start_date: date,
    end_date: date,
    *,
    dimensions,
    metrics,
    limit=100_000,
):
    return client.run_report(
        RunReportRequest(
            property=f"properties/{property_id}",
            date_ranges=[
                DateRange(
                    start_date=start_date.isoformat(), end_date=end_date.isoformat()
                )
            ],
            dimensions=[Dimension(name=name) for name in dimensions],
            metrics=[Metric(name=name) for name in metrics],
            limit=limit,
        )
    )


def fetch_ga4(start_date: date, end_date: date) -> dict:
    """Return GA4 aggregate sessions and landing-page engagement."""
    property_id = getattr(settings, "HUB_ANALYTICS_GA4_PROPERTY_ID", "")
    if not property_id:
        raise AnalyticsConfigurationError("GA4 property access is unavailable.")
    client = BetaAnalyticsDataClient(credentials=_google_credentials())
    summary_response = _ga4_report(
        client,
        property_id,
        start_date,
        end_date,
        dimensions=[],
        metrics=["sessions", "totalUsers", "engagementRate"],
        limit=1,
    )
    summary_values = (
        summary_response.rows[0].metric_values if summary_response.rows else []
    )
    summary = {
        "sessions": int(summary_values[0].value or 0) if summary_values else 0,
        "users": int(summary_values[1].value or 0) if summary_values else 0,
        "engagementRate": (
            float(summary_values[2].value or 0) if summary_values else 0.0
        ),
    }

    daily_response = _ga4_report(
        client,
        property_id,
        start_date,
        end_date,
        dimensions=["date"],
        metrics=["sessions", "totalUsers", "engagementRate"],
    )
    daily = []
    for row in daily_response.rows:
        raw_date = row.dimension_values[0].value
        formatted_date = datetime.strptime(raw_date, "%Y%m%d").date().isoformat()
        daily.append(
            {
                "date": formatted_date,
                "sessions": int(row.metric_values[0].value or 0),
                "users": int(row.metric_values[1].value or 0),
                "engagementRate": float(row.metric_values[2].value or 0),
            }
        )

    page_response = _ga4_report(
        client,
        property_id,
        start_date,
        end_date,
        dimensions=["landingPagePlusQueryString"],
        metrics=["sessions", "totalUsers", "engagementRate"],
    )
    page_accumulator: dict[str, dict] = {}
    for row in page_response.rows:
        path = _normalise_path(row.dimension_values[0].value)
        if path == "(not set)":
            continue
        sessions = int(row.metric_values[0].value or 0)
        users = int(row.metric_values[1].value or 0)
        engagement_rate = float(row.metric_values[2].value or 0)
        aggregate = page_accumulator.setdefault(
            path,
            {"path": path, "sessions": 0, "users": 0, "engagedSessions": 0.0},
        )
        aggregate["sessions"] += sessions
        aggregate["users"] += users
        aggregate["engagedSessions"] += engagement_rate * sessions
    pages = []
    for aggregate in page_accumulator.values():
        sessions = aggregate["sessions"]
        pages.append(
            {
                "path": aggregate["path"],
                "sessions": sessions,
                "users": aggregate["users"],
                "engagementRate": (
                    aggregate["engagedSessions"] / sessions if sessions else 0.0
                ),
            }
        )
    return {
        "summary": summary,
        "daily": sorted(daily, key=lambda row: row["date"]),
        "pages": pages,
    }


def _application_insights_app_id() -> str:
    explicit = getattr(settings, "HUB_ANALYTICS_APP_INSIGHTS_APP_ID", "")
    if explicit:
        return explicit
    connection_string = getattr(settings, "APPLICATIONINSIGHTS_CONNECTION_STRING", "")
    parts = dict(
        item.split("=", 1) for item in connection_string.split(";") if "=" in item
    )
    return parts.get("ApplicationId", "")


def _app_insights_query(app_id: str, token: str, query: str) -> list[dict]:
    timeout = getattr(settings, "HUB_ANALYTICS_HTTP_TIMEOUT_SECONDS", 15)
    response = requests.get(
        f"https://api.applicationinsights.io/v1/apps/{app_id}/query",
        headers={"Authorization": f"Bearer {token}"},
        params={"query": query},
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    tables = payload.get("tables", [])
    if not tables:
        return []
    table = tables[0]
    columns = [column["name"] for column in table.get("columns", [])]
    return [dict(zip(columns, row)) for row in table.get("rows", [])]


def fetch_application_insights(days: int) -> dict:
    """Return fixed, aggregate KQL results through managed identity."""
    app_id = _application_insights_app_id()
    if not app_id:
        raise AnalyticsConfigurationError(
            "Application Insights query access is unavailable."
        )
    credential = DefaultAzureCredential(exclude_interactive_browser_credential=True)
    token = credential.get_token(APP_INSIGHTS_SCOPE).token
    # Inclusive calendar window: today plus the preceding ``days - 1`` days.
    lookback_days = days - 1
    summary_query = f"""
let start = startofday(ago({lookback_days}d));
let pv = pageViews | where timestamp >= start;
let ex = exceptions | where timestamp >= start;
print page_views=toscalar(pv | count),
      sessions=toscalar(pv | summarize value=dcount(session_Id) | project value),
      users=toscalar(pv | summarize value=dcount(user_Id) | project value),
      exceptions=toscalar(ex | count)
""".strip()
    daily_query = f"""
let start = startofday(ago({lookback_days}d));
let pv = pageViews
    | where timestamp >= start
    | summarize page_views=count(), sessions=dcount(session_Id), users=dcount(user_Id)
      by day=startofday(timestamp);
let ex = exceptions
    | where timestamp >= start
    | summarize exceptions=count() by day=startofday(timestamp);
pv | join kind=fullouter ex on day
   | project day_label=format_datetime(coalesce(day, day1), 'yyyy-MM-dd'),
             page_views=coalesce(page_views, 0),
             sessions=coalesce(sessions, 0),
             users=coalesce(users, 0),
             exceptions=coalesce(exceptions, 0)
   | order by day_label asc
""".strip()
    event_query = f"""
customEvents
| where timestamp >= startofday(ago({lookback_days}d))
| summarize event_count=count() by name
| top 20 by event_count desc
""".strip()
    summary_rows = _app_insights_query(app_id, token, summary_query)
    summary = summary_rows[0] if summary_rows else {}
    daily_rows = _app_insights_query(app_id, token, daily_query)
    return {
        "summary": {
            "pageViews": int(summary.get("page_views", 0)),
            "sessions": int(summary.get("sessions", 0)),
            "users": int(summary.get("users", 0)),
            "exceptions": int(summary.get("exceptions", 0)),
        },
        "daily": [
            {
                "date": row.get("day_label", ""),
                "page_views": int(row.get("page_views", 0)),
                "sessions": int(row.get("sessions", 0)),
                "users": int(row.get("users", 0)),
                "exceptions": int(row.get("exceptions", 0)),
            }
            for row in daily_rows
        ],
        "events": [
            {"name": row.get("name", ""), "count": int(row.get("event_count", 0))}
            for row in _app_insights_query(app_id, token, event_query)
            if row.get("name")
        ],
    }


def fetch_activation(days: int, today: date) -> dict:
    """Return current verification state and persisted weekly KPI history."""
    counts = CrushProfile.objects.aggregate(
        total=Count("id"),
        incomplete=Count("id", filter=Q(verification_status="incomplete")),
        pending=Count("id", filter=Q(verification_status="pending")),
        verified=Count("id", filter=Q(verification_status="verified")),
        rejected=Count("id", filter=Q(verification_status="rejected")),
    )
    history_start = today - timedelta(days=days + 7)
    snapshots = WeeklyMetricsSnapshot.objects.filter(
        week_start__gte=history_start
    ).order_by("week_start")
    weekly = []
    for snapshot in snapshots:
        acquisition = snapshot.metrics.get("acquisition", {})
        revenue = snapshot.metrics.get("revenue", {})
        weekly.append(
            {
                "weekStart": snapshot.week_start.isoformat(),
                "weekEnd": snapshot.week_end.isoformat(),
                "newSignups": acquisition.get("new_signups"),
                "profilesSubmitted": acquisition.get("profiles_submitted"),
                "profilesVerified": acquisition.get("profiles_verified"),
                "cumulativeVerifiedMembers": acquisition.get(
                    "cumulative_verified_members"
                ),
                "waitlistNew": revenue.get("waitlist_new"),
                "waitlistTotal": revenue.get("waitlist_total"),
            }
        )
    return {"current": counts, "weekly": weekly}


def _provider(label: str, callback) -> ProviderResult:
    try:
        return ProviderResult(status="ready", data=callback())
    except AnalyticsConfigurationError:
        return ProviderResult(
            status="unavailable",
            data={},
            message=f"{label} n’est pas configuré pour le Hub.",
        )
    except Exception:
        logger.exception("Hub analytics provider failed: %s", label)
        return ProviderResult(
            status="error",
            data={},
            message=f"{label} est temporairement indisponible.",
        )


def _merge_landing_pages(gsc: ProviderResult, ga4: ProviderResult) -> list[dict]:
    if gsc.status != "ready" and ga4.status != "ready":
        return []
    rows: dict[str, dict] = {}
    for page in gsc.data.get("pages", []):
        rows[page["path"]] = {
            "path": page["path"],
            "clicks": page["clicks"],
            "impressions": page["impressions"],
            "ctr": page["ctr"],
            "position": page["position"],
            "sessions": None,
            "users": None,
            "engagementRate": None,
        }
    for page in ga4.data.get("pages", []):
        target = rows.setdefault(
            page["path"],
            {
                "path": page["path"],
                "clicks": None,
                "impressions": None,
                "ctr": None,
                "position": None,
                "sessions": None,
                "users": None,
                "engagementRate": None,
            },
        )
        target.update(
            sessions=page["sessions"],
            users=page["users"],
            engagementRate=page["engagementRate"],
        )
    return sorted(
        rows.values(),
        key=lambda row: (row["impressions"] or 0, row["sessions"] or 0),
        reverse=True,
    )[:50]


def build_analytics_overview(days: int) -> dict:
    """Build the bounded, aggregate response consumed by hub.crush.lu."""
    today = timezone.localdate()
    external_end = today - timedelta(days=3)
    external_start = external_end - timedelta(days=days - 1)
    # Upstream HTTP calls are independent. Running them together keeps a cold
    # cache request bounded by the slowest provider rather than their sum.
    with ThreadPoolExecutor(max_workers=3, thread_name_prefix="hub-analytics") as pool:
        gsc_future = pool.submit(
            _provider,
            "Google Search Console",
            lambda: fetch_search_console(external_start, external_end),
        )
        ga4_future = pool.submit(
            _provider,
            "Google Analytics",
            lambda: fetch_ga4(external_start, external_end),
        )
        app_insights_future = pool.submit(
            _provider,
            "Application Insights",
            lambda: fetch_application_insights(days),
        )
        activation = fetch_activation(days, today)
        gsc = gsc_future.result()
        ga4 = ga4_future.result()
        app_insights = app_insights_future.result()

    source_rows = [
        {
            "id": "search-console",
            "label": "Google Search Console",
            "status": gsc.status,
            "observedThrough": (
                external_end.isoformat() if gsc.status == "ready" else None
            ),
            "message": gsc.message,
        },
        {
            "id": "ga4",
            "label": "Google Analytics",
            "status": ga4.status,
            "observedThrough": (
                external_end.isoformat() if ga4.status == "ready" else None
            ),
            "message": ga4.message,
        },
        {
            "id": "application-insights",
            "label": "Application Insights",
            "status": app_insights.status,
            "observedThrough": (
                today.isoformat() if app_insights.status == "ready" else None
            ),
            "message": app_insights.message,
        },
        {
            "id": "crush",
            "label": "Crush.lu activation",
            "status": "ready",
            "observedThrough": today.isoformat(),
            "message": None,
        },
    ]
    current = activation["current"]
    return {
        "generatedAt": timezone.now().isoformat(),
        "period": {
            "days": days,
            "startDate": external_start.isoformat(),
            "endDate": external_end.isoformat(),
            "timezone": "Europe/Luxembourg",
        },
        "sources": source_rows,
        "overview": {
            "searchImpressions": (
                gsc.data.get("summary", {}).get("impressions")
                if gsc.status == "ready"
                else None
            ),
            "searchClicks": (
                gsc.data.get("summary", {}).get("clicks")
                if gsc.status == "ready"
                else None
            ),
            "webSessions": (
                ga4.data.get("summary", {}).get("sessions")
                if ga4.status == "ready"
                else None
            ),
            "authenticatedUsers": (
                app_insights.data.get("summary", {}).get("users")
                if app_insights.status == "ready"
                else None
            ),
            "pendingProfiles": current["pending"],
            "verifiedProfiles": current["verified"],
        },
        "search": {
            "summary": gsc.data.get("summary") if gsc.status == "ready" else None,
            "daily": gsc.data.get("daily", []) if gsc.status == "ready" else [],
            "landingPages": _merge_landing_pages(gsc, ga4),
        },
        "audience": {
            "summary": ga4.data.get("summary") if ga4.status == "ready" else None,
            "daily": ga4.data.get("daily", []) if ga4.status == "ready" else [],
        },
        "product": (
            app_insights.data
            if app_insights.status == "ready"
            else {
                "summary": None,
                "daily": [],
                "events": [],
            }
        ),
        "activation": activation,
        "caveats": [
            "Search Console et Google Analytics sont rapprochés uniquement par date et chemin de page normalisé, jamais par personne.",
            "La télémétrie Application Insights couvre les pages Crush.lu authentifiées et ne remplace pas la mesure de l’acquisition anonyme.",
            "Les ratios hebdomadaires entre inscription et vérification indiquent une tendance, pas une cohorte individuelle stricte.",
        ],
    }
