"""
Google Search Indexing API Service for Crush.lu

Provides instant real-time Googlebot crawling notifications (URL_UPDATED / URL_DELETED)
for Crush.lu events across all supported languages (EN, FR, DE).

References:
- https://developers.google.com/search/apis/indexing-api/
"""

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from django.conf import settings
from django.urls import reverse
from django.utils.translation import override

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/indexing"]
DEFAULT_LANGUAGES = ["en", "fr", "de"]


def is_indexing_enabled() -> bool:
    """Check whether Google Indexing API integration is enabled."""
    return getattr(settings, "GOOGLE_INDEXING_ENABLED", False)


# Filters selecting events whose detail page Googlebot can actually fetch.
# Keep in sync with should_index_event().
INDEXABLE_EVENT_FILTERS = {"is_published": True, "is_private_invitation": False}


def should_index_event(event) -> bool:
    """
    Determine if an event is eligible for public Google Search indexing.

    Mirrors what `event_detail` actually serves an anonymous Googlebot:
    - is_published = False -> 404 (get_object_or_404 filters on is_published)
    - is_private_invitation = True -> 302 redirect to the event list

    A *cancelled* event stays eligible: the page still returns 200 and emits
    `https://schema.org/EventCancelled` structured data, so Google must be asked
    to refresh it (URL_UPDATED). Sending URL_DELETED for a live 200 page is
    misuse of the API — Google re-crawls, finds the page, and the cancellation
    markup never reaches the SERP.
    """
    if not getattr(event, "is_published", False):
        return False
    if getattr(event, "is_private_invitation", False):
        return False
    return True


def get_google_indexing_credentials() -> Optional[Any]:
    """
    Load Google Service Account credentials for the Indexing API.

    Searches in order:
    1. settings.GOOGLE_INDEXING_KEY_JSON (JSON string or dict from Azure App Service / env)
    2. os.environ.get("GOOGLE_INDEXING_KEY_JSON")
    3. settings.GOOGLE_INDEXING_KEY_FILE (file path)
    4. os.environ.get("GOOGLE_INDEXING_KEY_PATH")
    5. settings.BASE_DIR / "scripts" / "google_indexing_key.json"
    """
    from google.oauth2 import service_account

    # 1. Direct JSON string or dict in settings
    key_json = getattr(settings, "GOOGLE_INDEXING_KEY_JSON", None) or os.environ.get(
        "GOOGLE_INDEXING_KEY_JSON"
    )
    if key_json:
        try:
            if isinstance(key_json, str):
                info = json.loads(key_json)
            else:
                info = key_json
            return service_account.Credentials.from_service_account_info(
                info, scopes=SCOPES
            )
        except Exception as e:
            logger.warning(
                "Failed to parse GOOGLE_INDEXING_KEY_JSON from settings/env: %s", e
            )

    # 2. File path lookup
    key_file = getattr(settings, "GOOGLE_INDEXING_KEY_FILE", None) or os.environ.get(
        "GOOGLE_INDEXING_KEY_PATH"
    )
    if not key_file and hasattr(settings, "BASE_DIR"):
        default_path = Path(settings.BASE_DIR) / "scripts" / "google_indexing_key.json"
        if default_path.exists():
            key_file = str(default_path)

    if key_file and Path(key_file).exists():
        try:
            return service_account.Credentials.from_service_account_file(
                str(key_file), scopes=SCOPES
            )
        except Exception as e:
            logger.warning(
                "Failed to load Google indexing credentials from %s: %s", key_file, e
            )

    return None


def get_google_indexing_session() -> Optional[Any]:
    """Build an authorized HTTP session for Google Indexing API."""
    if not is_indexing_enabled():
        logger.debug(
            "Google Indexing API is disabled by settings.GOOGLE_INDEXING_ENABLED"
        )
        return None

    try:
        from google.auth.transport.requests import AuthorizedSession

        creds = get_google_indexing_credentials()
        if not creds:
            logger.debug(
                "No Google indexing credentials available. Skipping indexing ping."
            )
            return None

        return AuthorizedSession(creds)
    except Exception as e:
        logger.warning("Failed to build Google Indexing authorized session: %s", e)
        return None


def build_event_indexing_urls(event, domain: str = "crush.lu") -> List[str]:
    """
    Build canonical absolute URLs for an event across all active site languages.

    Returns:
        List of absolute URLs:
        - https://crush.lu/en/events/<id>/
        - https://crush.lu/fr/events/<id>/
        - https://crush.lu/de/events/<id>/
    """
    event_id = getattr(event, "id", None) or getattr(event, "pk", None)
    if not event_id:
        return []

    languages = getattr(settings, "LANGUAGES", None)
    lang_codes = [code for code, _ in languages] if languages else DEFAULT_LANGUAGES

    urls = []
    for lang in lang_codes:
        try:
            with override(lang):
                path = reverse(
                    "crush_lu:event_detail",
                    urlconf="azureproject.urls_crush",
                    kwargs={"event_id": event_id},
                )
                urls.append(f"https://{domain}{path}")
        except Exception as e:
            logger.warning(
                "Failed to reverse event_detail for event %s lang %s: %s",
                event_id,
                lang,
                e,
            )

    return urls


def build_event_list_urls(domain: str = "crush.lu") -> List[str]:
    """Build canonical URLs for the events directory page (/en/events/, /fr/events/, /de/events/)."""
    languages = getattr(settings, "LANGUAGES", None)
    lang_codes = [code for code, _ in languages] if languages else DEFAULT_LANGUAGES

    urls = []
    for lang in lang_codes:
        try:
            with override(lang):
                path = reverse(
                    "crush_lu:event_list",
                    urlconf="azureproject.urls_crush",
                )
                urls.append(f"https://{domain}{path}")
        except Exception as e:
            logger.warning("Failed to reverse event_list for lang %s: %s", lang, e)

    return urls


def notify_url_indexing(
    url: str,
    action: str = "URL_UPDATED",
    session: Optional[Any] = None,
    timeout: Optional[float] = None,
    max_allowed_time: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """
    Notify Googlebot of a URL change (URL_UPDATED or URL_DELETED) with strict HTTP timeout
    and optional total allowed execution time (covering retries/auth token refresh).

    Safe execution: catches errors, logs diagnostic information, and never raises.
    """
    if not is_indexing_enabled():
        return None

    sess = session or get_google_indexing_session()
    if not sess:
        return None

    default_timeout = getattr(settings, "GOOGLE_INDEXING_TIMEOUT_SECONDS", 3)
    socket_timeout = timeout if timeout is not None else default_timeout
    if max_allowed_time is not None:
        socket_timeout = min(socket_timeout, max_allowed_time)

    body = {
        "url": url,
        "type": action,
    }

    kwargs: Dict[str, Any] = {
        "json": body,
        "timeout": socket_timeout,
    }
    if max_allowed_time is not None:
        kwargs["max_allowed_time"] = max_allowed_time

    try:
        response = sess.post(
            "https://indexing.googleapis.com/v3/urlNotifications:publish",
            **kwargs,
        )
        if response.status_code == 200:
            logger.info(
                "Google Indexing notification sent: %s (%s) -> 200 OK",
                url,
                action,
            )
            return response.json()
        else:
            logger.warning(
                "Google Indexing notification returned %s for %s (%s): %s",
                response.status_code,
                url,
                action,
                response.text,
            )
            return None
    except Exception as e:
        logger.warning(
            "Failed to send Google Indexing notification for %s (%s): %s",
            url,
            action,
            e,
        )
        return None


def _dispatch_event_urls(
    event,
    resolved_action: str,
    session: Any,
    end_time: float,
) -> Dict[str, Any]:
    """Ping every language URL of one event, reporting how many were attempted.

    The attempted count is what lets callers distinguish a *failed* URL from one
    the wall-clock budget never reached.
    """
    urls_to_ping = build_event_indexing_urls(event)
    results: List[Dict[str, Any]] = []
    attempted = 0

    for url in urls_to_ping:
        remaining = end_time - time.monotonic()
        if remaining <= 0:
            logger.warning(
                "Google Indexing budget exhausted. Skipping remaining URLs for event %s.",
                getattr(event, "pk", None),
            )
            break

        attempted += 1
        res = notify_url_indexing(
            url,
            action=resolved_action,
            session=session,
            max_allowed_time=remaining,
        )
        if res:
            results.append(res)

    return {"results": results, "attempted": attempted, "total": len(urls_to_ping)}


def notify_event_indexing(
    event,
    action: Optional[str] = None,
    session: Optional[Any] = None,
    deadline: Optional[float] = None,
    max_budget_seconds: float = 8.0,
) -> List[Dict[str, Any]]:
    """
    Notify Googlebot of an event creation, update, or deletion across all languages.

    Only submits specific event detail URLs (/en/events/<id>/, etc.).
    Uses a strict per-request socket timeout and overall wall-clock budget.
    """
    if not is_indexing_enabled():
        return []

    sess = session or get_google_indexing_session()
    if not sess:
        return []

    resolved_action = action or (
        "URL_UPDATED" if should_index_event(event) else "URL_DELETED"
    )
    end_time = (
        deadline if deadline is not None else (time.monotonic() + max_budget_seconds)
    )

    return _dispatch_event_urls(event, resolved_action, sess, end_time)["results"]


def notify_events_indexing(
    events,
    action: Optional[str] = None,
    max_budget_seconds: float = 12.0,
) -> Dict[str, Any]:
    """
    Notify Googlebot for a collection/queryset of events within a single request budget.

    Uses a single shared AuthorizedSession and one global wall-clock deadline across
    all selected events, preventing N*8s timeout explosion on bulk admin operations.
    """
    if not is_indexing_enabled():
        return {"success_count": 0, "total_expected": 0, "deferred_count": 0}

    sess = get_google_indexing_session()
    if not sess:
        return {"success_count": 0, "total_expected": 0, "deferred_count": 0}

    event_list = list(events)
    deadline = time.monotonic() + max_budget_seconds
    total_expected = sum(len(build_event_indexing_urls(ev)) for ev in event_list)
    successful_urls = 0
    attempted_urls = 0

    for i, event in enumerate(event_list):
        if time.monotonic() > deadline:
            logger.warning(
                "Google Indexing batch budget exhausted (%ss). Deferred %d events.",
                max_budget_seconds,
                len(event_list) - i,
            )
            break

        resolved_action = action or (
            "URL_UPDATED" if should_index_event(event) else "URL_DELETED"
        )
        outcome = _dispatch_event_urls(event, resolved_action, sess, deadline)
        attempted_urls += outcome["attempted"]
        successful_urls += len(outcome["results"])

    # Derived from what was *attempted*, not from the events loop: when the
    # budget expires part-way through the last event's language URLs, those
    # skipped URLs are deferrals too and must show up in the admin message.
    deferred_count = max(0, total_expected - attempted_urls)

    return {
        "success_count": successful_urls,
        "total_expected": total_expected,
        "deferred_count": deferred_count,
    }


def notify_urls_indexing(
    urls: List[str],
    action: str = "URL_DELETED",
    session: Optional[Any] = None,
    deadline: Optional[float] = None,
    max_budget_seconds: float = 5.0,
) -> List[Dict[str, Any]]:
    """Notify Googlebot for a list of raw URLs (e.g. deleted event URLs) within a single budget."""
    if not is_indexing_enabled() or not urls:
        return []

    sess = session or get_google_indexing_session()
    if not sess:
        return []

    end_time = (
        deadline if deadline is not None else (time.monotonic() + max_budget_seconds)
    )
    results = []
    for url in urls:
        remaining = end_time - time.monotonic()
        if remaining <= 0:
            logger.warning(
                "Google Indexing URL budget exhausted (%ss).", max_budget_seconds
            )
            break
        res = notify_url_indexing(
            url,
            action=action,
            session=sess,
            max_allowed_time=remaining,
        )
        if res:
            results.append(res)
    return results
