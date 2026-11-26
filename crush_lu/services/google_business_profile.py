"""
Google Business Profile helpers for Crush.lu.

Crush.lu's listing (`locations/10954543438099561082`, a verified
`CUSTOMER_LOCATION_ONLY` service-area business) is reachable through the
Business Profile APIs — Google approved allowlist access for Cloud project
297130634516 on 2026-09-11.

Two pieces live here:

1. The public review deep link (:func:`get_review_url`), which needs no API
   call at all and is used by the post-event emails.
2. The event-post sync: every upcoming, public crush.lu event is mirrored as
   one English ``EVENT`` post on the listing through the legacy v4.9
   ``mybusiness.googleapis.com`` ``localPosts`` API. Driven hourly by
   ``manage.py sync_events_to_gbp``.

Auth is **not** a service account (that is the Indexing API's model and cannot
manage a Business Profile listing). The API acts as the listing owner through
OAuth with the ``business.manage`` scope; production stores that user's
authorized-user JSON (``client_id``, ``client_secret``, ``refresh_token``) in
``GOOGLE_BUSINESS_PROFILE_TOKEN_JSON``. ``scripts/gbp_discover.py`` mints it.

Every write is refused unless all three hold: ``GOOGLE_BUSINESS_PROFILE_ENABLED``
is true, ``GOOGLE_BUSINESS_PROFILE_LOCATION`` is set (production.py derives it
from the slot-sticky ``DJANGO_ENV``, so a staging slot never has it) and the
token is present. There is no sandbox listing: the first successful call writes
to the public profile.

References:
- https://developers.google.com/my-business/content/posts-data
- https://developers.google.com/my-business/reference/rest/v4/accounts.locations.localPosts
"""

import hashlib
import json
import logging
from urllib.parse import parse_qs, urlencode, urlsplit
from zoneinfo import ZoneInfo

from django.conf import settings
from django.utils import timezone
from django.utils.html import strip_tags

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/business.manage"]
API_BASE = "https://mybusiness.googleapis.com/v4"
LISTING_TZ = ZoneInfo("Europe/Luxembourg")

# Google's limits for an EVENT post. Truncated here rather than letting Google
# reject the whole post.
MAX_TITLE_CHARS = 58
MAX_SUMMARY_CHARS = 1500
# Google accepts JPG/PNG photos between 10 KB and 5 MB.
MAX_PHOTO_BYTES = 5 * 1024 * 1024
PHOTO_EXTENSIONS = (".jpg", ".jpeg", ".png")

# Fields a PATCH may change. createTime/state/name are server-owned.
UPDATE_MASK = "languageCode,summary,callToAction,event,media"

UTM_PARAMS = {
    "utm_source": "google",
    "utm_medium": "gbp",
    "utm_campaign": "event_post",
}


def get_review_url() -> str:
    """The listing's "write a review" deep link, or "" when asking is not allowed.

    Mirrors ``google_indexing.get_indexing_domain()``: an unset value is not a
    misconfiguration to warn about, it is the deployment saying "not from here".
    Every caller must treat "" as *render nothing* rather than falling back to a
    hardcoded default — a staging slot must not be able to point members at the
    production listing.
    """
    return (getattr(settings, "CRUSH_GOOGLE_REVIEW_URL", "") or "").strip()


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class GbpError(Exception):
    """A Business Profile call failed."""

    #: True when the failure breaks the whole sweep (auth, quota, permission)
    #: rather than one event's post.
    fatal = False

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class GbpAuthError(GbpError):
    """The stored OAuth token no longer works (revoked, expired, malformed)."""

    fatal = True


def _api_error(response):
    try:
        detail = response.json().get("error", {}).get("message", "")
    except ValueError:
        detail = ""
    detail = detail or (response.text or "")[:500]
    error = GbpError(f"HTTP {response.status_code}: {detail}", response.status_code)
    # A refused token, a missing permission or an exhausted quota fails every
    # event the same way — say so once instead of recording it on each row.
    error.fatal = response.status_code in (401, 403, 429)
    return error


# ---------------------------------------------------------------------------
# Configuration and session
# ---------------------------------------------------------------------------


def get_location_name() -> str:
    """``accounts/…/locations/…`` this deployment may post to, or ""."""
    return (getattr(settings, "GOOGLE_BUSINESS_PROFILE_LOCATION", "") or "").strip()


def _token_info():
    raw = getattr(settings, "GOOGLE_BUSINESS_PROFILE_TOKEN_JSON", "") or ""
    if isinstance(raw, dict):
        return raw
    raw = raw.strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        logger.warning("GOOGLE_BUSINESS_PROFILE_TOKEN_JSON is not valid JSON")
        return None


def disabled_reason() -> str:
    """Why event posts may not be written here, or "" when they may."""
    if not getattr(settings, "GOOGLE_BUSINESS_PROFILE_ENABLED", False):
        return "GOOGLE_BUSINESS_PROFILE_ENABLED is not true"
    if not get_location_name():
        logger.warning(
            "GOOGLE_BUSINESS_PROFILE_ENABLED is set but "
            "GOOGLE_BUSINESS_PROFILE_LOCATION is empty; refusing every write. "
            "Only production may post to the real listing."
        )
        return "GOOGLE_BUSINESS_PROFILE_LOCATION is empty on this deployment"
    if not _token_info():
        return "GOOGLE_BUSINESS_PROFILE_TOKEN_JSON is not set"
    return ""


def is_sync_enabled() -> bool:
    return not disabled_reason()


def get_session():
    """An authorized ``requests`` session acting as the listing owner."""
    from google.auth.transport.requests import AuthorizedSession
    from google.oauth2.credentials import Credentials

    info = _token_info()
    if not info:
        raise GbpAuthError("GOOGLE_BUSINESS_PROFILE_TOKEN_JSON is not set")
    try:
        creds = Credentials.from_authorized_user_info(info, SCOPES)
    except (ValueError, KeyError) as exc:
        raise GbpAuthError(f"Token JSON is incomplete: {exc}") from exc
    return AuthorizedSession(creds)


def _request(session, method, url, **kwargs):
    """One API call. Raises GbpError; never returns an error response."""
    import requests
    from google.auth.exceptions import RefreshError

    kwargs.setdefault(
        "timeout", getattr(settings, "GOOGLE_BUSINESS_PROFILE_TIMEOUT_SECONDS", 15)
    )
    try:
        response = session.request(method, url, **kwargs)
    except RefreshError as exc:
        # Greppable on purpose: this is the one failure that needs Tom to
        # re-run scripts/gbp_discover.py and paste a new token.
        logger.warning("GBP_AUTH_INVALID_GRANT: token refresh failed: %s", exc)
        raise GbpAuthError(f"Token refresh failed: {exc}") from exc
    except requests.RequestException as exc:
        raise GbpError(f"Request failed: {exc}") from exc
    if response.status_code >= 400:
        raise _api_error(response)
    return response


# ---------------------------------------------------------------------------
# MeetupEvent -> local post payload
# ---------------------------------------------------------------------------


def _is_test_event(event):
    from .cache_rehearsal import is_rehearsal_cache_event
    from .playground_events import is_practice_mixer

    return is_practice_mixer(event) or is_rehearsal_cache_event(event)


def must_remove(event) -> bool:
    """True when a post for this event has to come down now.

    A booking button for a cancelled, unpublished or invitation-only event is
    worse than no post. An event that simply started or ended is left alone:
    an event post stops showing on its own after its end date.
    """
    return (
        not event.is_published
        or event.is_cancelled
        or event.is_private_invitation
        or _is_test_event(event)
    )


def should_post(event) -> bool:
    """True when this event belongs on the listing as a (new or updated) post."""
    return not must_remove(event) and event.date_time > timezone.now()


def _translated(event, field):
    """English column first (the listing's language), then any other filled one."""
    for code in ("en",) + tuple(c for c, _label in settings.LANGUAGES):
        value = getattr(event, f"{field}_{code.replace('-', '_')}", None)
        if value:
            return value
    return getattr(event, field, "") or ""


def _truncate(text, limit):
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _clean_summary(text):
    # Keep paragraph breaks, drop markup and runs of blank lines.
    lines = [" ".join(line.split()) for line in strip_tags(text or "").splitlines()]
    cleaned = "\n".join(lines)
    while "\n\n\n" in cleaned:
        cleaned = cleaned.replace("\n\n\n", "\n\n")
    return _truncate(cleaned.strip(), MAX_SUMMARY_CHARS)


def _date(value):
    return {"year": value.year, "month": value.month, "day": value.day}


def _time(value):
    return {"hours": value.hour, "minutes": value.minute, "seconds": 0, "nanos": 0}


def event_marker(event) -> str:
    """The ``utm_content`` value that ties a post back to its event."""
    return f"event-{event.pk}"


def booking_url(event) -> str:
    from ..utils.i18n import build_absolute_url

    url = build_absolute_url(
        "crush_lu:event_detail", lang="en", kwargs={"event_id": event.pk}
    )
    params = dict(UTM_PARAMS, utm_content=event_marker(event))
    return f"{url}{'&' if '?' in url else '?'}{urlencode(params)}"


def _photo_url(event):
    """Absolute URL of the event banner if Google can take it, else ""."""
    from django.core.cache import cache

    image = getattr(event, "image", None)
    name = getattr(image, "name", "") or ""
    if not name or not name.lower().endswith(PHOTO_EXTENSIONS):
        return ""
    # `.size` is a network call on Azure Blob; cache it per stored file name
    # (content-hashed), as the echo.lu sync does, so a no-op sweep stays free.
    cache_key = f"gbp:photo_size:{name}"
    size = cache.get(cache_key)
    if size is None:
        try:
            size = image.size
        except Exception:  # noqa: BLE001 - storage errors must not fail the sync
            size = 0
        if size:
            cache.set(cache_key, size, 7 * 24 * 3600)
    if size and size > MAX_PHOTO_BYTES:
        return ""
    try:
        url = image.url
    except Exception:  # noqa: BLE001
        return ""
    if url.startswith(("http://", "https://")):
        return url
    return f"https://crush.lu{url}"


def build_post_payload(event) -> dict:
    """The ``LocalPost`` body for an event. Pure: the fingerprint hashes it.

    Times are the event's wall-clock time in Luxembourg: ``event.schedule`` has
    separate date and time parts read in the listing's local time, while
    ``date_time`` is stored in UTC.
    """
    start = timezone.localtime(event.date_time, LISTING_TZ)
    end = timezone.localtime(event.end_time, LISTING_TZ)
    payload = {
        "languageCode": "en",
        "topicType": "EVENT",
        "summary": _clean_summary(_translated(event, "description")),
        "event": {
            "title": _truncate(
                " ".join(strip_tags(_translated(event, "title")).split()),
                MAX_TITLE_CHARS,
            ),
            "schedule": {
                "startDate": _date(start),
                "startTime": _time(start),
                "endDate": _date(end),
                "endTime": _time(end),
            },
        },
        "callToAction": {"actionType": "BOOK", "url": booking_url(event)},
    }
    photo = _photo_url(event)
    if photo:
        payload["media"] = [{"mediaFormat": "PHOTO", "sourceUrl": photo}]
    return payload


def payload_fingerprint(payload) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


# ---------------------------------------------------------------------------
# API calls
# ---------------------------------------------------------------------------


def _posts_url(location=None):
    return f"{API_BASE}/{location or get_location_name()}/localPosts"


def _marker_of(post):
    url = (post.get("callToAction") or {}).get("url") or ""
    values = parse_qs(urlsplit(url).query).get("utm_content") or [""]
    return values[0]


def find_existing_post(session, event, max_pages=5):
    """The live post already carrying this event's marker, or None.

    Run before every create, so a create whose response was lost (timeout,
    5xx after commit) is adopted on the next sweep rather than duplicated.
    """
    marker = event_marker(event)
    params = {"pageSize": 100}
    for _page in range(max_pages):
        data = _request(session, "GET", _posts_url(), params=params).json()
        for post in data.get("localPosts", []):
            if _marker_of(post) == marker:
                return post
        token = data.get("nextPageToken")
        if not token:
            return None
        params["pageToken"] = token
    return None


def _sync_row(event):
    from crush_lu.models import GoogleLocalPostSync

    sync, _created = GoogleLocalPostSync.objects.get_or_create(event=event)
    return sync


def sync_event(event, session=None, force=False, dry_run=False):
    """Bring the event's Google post in line with the event.

    Returns one of ``created``, ``updated``, ``adopted``, ``unchanged``,
    ``removed`` or ``skipped``. On an API failure the error is recorded on the
    sync row and re-raised as :class:`GbpError`.
    """
    from crush_lu.models import GoogleLocalPostSync

    existing = GoogleLocalPostSync.objects.filter(event=event).first()
    has_post = bool(existing and existing.post_name)

    if must_remove(event):
        if not has_post:
            return "skipped"
        return remove_event_post(event, session=session, dry_run=dry_run)
    if not should_post(event):
        # Started or finished: Google retires the post after its end date.
        return "skipped"

    payload = build_post_payload(event)
    fingerprint = payload_fingerprint(payload)
    if (
        has_post
        and not force
        and existing.status == GoogleLocalPostSync.Status.SYNCED
        and existing.payload_hash == fingerprint
    ):
        return "unchanged"
    if dry_run:
        return "updated" if has_post else "created"

    sync = existing or _sync_row(event)
    try:
        if has_post:
            response = _request(
                session,
                "PATCH",
                f"{API_BASE}/{sync.post_name}",
                params={"updateMask": UPDATE_MASK},
                json=payload,
            )
            action = "updated"
        else:
            found = find_existing_post(session, event)
            if found:
                response = _request(
                    session,
                    "PATCH",
                    f"{API_BASE}/{found['name']}",
                    params={"updateMask": UPDATE_MASK},
                    json=payload,
                )
                action = "adopted"
            else:
                response = _request(session, "POST", _posts_url(), json=payload)
                action = "created"
    except GbpError as exc:
        sync.mark_failure(exc)
        raise
    body = response.json() if response.content else {}
    sync.mark_success(
        body.get("name") or sync.post_name, fingerprint, body.get("searchUrl", "")
    )
    logger.info("[GBP] event %s: %s %s", event.pk, action, sync.post_name)
    return action


def remove_event_post(event, session=None, dry_run=False):
    """Delete the event's Google post. A post Google no longer has counts as gone."""
    from crush_lu.models import GoogleLocalPostSync

    sync = GoogleLocalPostSync.objects.filter(event=event).first()
    if not sync or not sync.post_name:
        return "skipped"
    if dry_run:
        return "removed"
    try:
        _request(session, "DELETE", f"{API_BASE}/{sync.post_name}")
    except GbpError as exc:
        if exc.status != 404:
            sync.mark_failure(exc)
            raise
    logger.info("[GBP] event %s: removed %s", event.pk, sync.post_name)
    sync.mark_removed()
    return "removed"


def remove_orphaned_posts(session=None, dry_run=False):
    """Delete the posts of events that were deleted from the database.

    The sync row outlives its event (``on_delete=SET_NULL``) precisely so this
    can run: without it a deleted event would keep a public post with a dead
    booking link. Returns ``(removed, failures)``; a fatal error is raised.
    """
    from crush_lu.models import GoogleLocalPostSync

    removed, failures = 0, []
    orphans = GoogleLocalPostSync.objects.filter(event__isnull=True).exclude(
        post_name=""
    )
    for sync in orphans:
        if dry_run:
            removed += 1
            continue
        try:
            _request(session, "DELETE", f"{API_BASE}/{sync.post_name}")
        except GbpError as exc:
            if exc.status != 404:
                sync.mark_failure(exc)
                if exc.fatal:
                    raise
                failures.append(f"{sync.post_name}: {exc}")
                continue
        logger.info("[GBP] removed post %s of a deleted event", sync.post_name)
        sync.delete()
        removed += 1
    return removed, failures


def events_needing_sync():
    """Upcoming public events, plus every event that still holds a post."""
    from django.db.models import Q

    from crush_lu.models import MeetupEvent

    upcoming = Q(
        date_time__gt=timezone.now(),
        is_published=True,
        is_cancelled=False,
        is_private_invitation=False,
    )
    posted = Q(gbp_post_sync__post_name__gt="")
    return (
        MeetupEvent.objects.filter(upcoming | posted).distinct().order_by("date_time")
    )
