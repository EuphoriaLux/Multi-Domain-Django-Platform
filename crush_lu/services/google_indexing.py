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
    return getattr(settings, "GOOGLE_INDEXING_ENABLED", True)


def should_index_event(event) -> bool:
    """
    Determine if an event is eligible for public Google Search indexing.

    Requires:
    - is_published = True
    - is_cancelled = False
    - is_private_invitation = False (mirrors echo.lu policy)
    """
    if not getattr(event, "is_published", False):
        return False
    if getattr(event, "is_cancelled", False):
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
        logger.debug("Google Indexing API is disabled by settings.GOOGLE_INDEXING_ENABLED")
        return None

    try:
        from google.auth.transport.requests import AuthorizedSession

        creds = get_google_indexing_credentials()
        if not creds:
            logger.debug("No Google indexing credentials available. Skipping indexing ping.")
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


def notify_url_indexing(
    url: str,
    action: str = "URL_UPDATED",
    session: Optional[Any] = None,
    timeout: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """
    Notify Googlebot of a URL change (URL_UPDATED or URL_DELETED) with strict HTTP timeout.

    Safe execution: catches errors, logs diagnostic information, and never raises.
    """
    if not is_indexing_enabled():
        return None

    sess = session or get_google_indexing_session()
    if not sess:
        return None

    timeout_seconds = timeout or getattr(settings, "GOOGLE_INDEXING_TIMEOUT_SECONDS", 3)
    body = {
        "url": url,
        "type": action,
    }

    try:
        response = sess.post(
            "https://indexing.googleapis.com/v3/urlNotifications:publish",
            json=body,
            timeout=timeout_seconds,
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


def notify_event_indexing(
    event,
    action: str = "URL_UPDATED",
    max_budget_seconds: float = 8.0,
) -> List[Dict[str, Any]]:
    """
    Notify Googlebot of an event creation, update, or deletion across all languages.

    Only submits specific event detail URLs (/en/events/<id>/, etc.).
    Uses a strict per-request socket timeout and overall wall-clock budget.
    """
    if not is_indexing_enabled():
        return []

    session = get_google_indexing_session()
    if not session:
        return []

    urls_to_ping = build_event_indexing_urls(event)
    deadline = time.monotonic() + max_budget_seconds

    results = []
    for url in urls_to_ping:
        if time.monotonic() > deadline:
            logger.warning(
                "Google Indexing budget exhausted (%ss). Skipping remaining URLs for event %s.",
                max_budget_seconds,
                getattr(event, "pk", None),
            )
            break

        res = notify_url_indexing(url, action=action, session=session)
        if res:
            results.append(res)

    return results
