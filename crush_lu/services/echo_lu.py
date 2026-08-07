"""echo.lu partner API client — publishes Crush.lu events to Luxembourg's
national events portal.

API reference: https://api.echo.lu/ (sandbox: https://test-api.echo.lu/).

Shape of the remote API, because it drives most of the decisions below:

* Auth is a single ``api-key`` request header, issued per organisation from the
  echo.lu organiser back office. There is no organisation id in the payload —
  the key *is* the identity.
* Events are "experiences": ``POST /experiences`` to create, ``PUT
  /experiences/{id}`` to replace, ``DELETE /experiences/{id}`` to remove, and
  ``PATCH /experiences/{id}/{action}`` for the lifecycle transitions (cancel,
  unpublish, complete, postpone).
* Ids are server-assigned and the create payload carries no external-reference
  field, so the returned id is the only link back to a MeetupEvent. It lives in
  ``EchoExperienceSync`` — see that model for why losing it is expensive.
* Dates are RFC 3339; echo.lu renders them in Europe/Luxembourg regardless of
  the offset sent, so we send UTC.

Everything here is gated on ``settings.ECHO_LU_SYNC_ENABLED``, which defaults
to False. A restored production database on staging therefore cannot mutate
live listings just by having inherited the key.
"""

import hashlib
import json
import logging
import re
import time
from datetime import timezone as dt_timezone
from decimal import Decimal

import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

# echo.lu does not document a rate limit. Retry only the statuses that are
# unambiguously "try again" — a 4xx other than 429 means the payload is wrong
# and replaying it just burns the same rejection three times.
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
# What a create may retry on. 429 is the only answer that proves echo.lu did
# not process the request; a 5xx or a lost socket may mean the listing exists
# already, and replaying that is how you get two. See create_experience.
CREATE_RETRY_STATUSES = frozenset({429})
MAX_RETRIES = 3
DEFAULT_RETRY_AFTER = 2.0
# Ceiling on total sleep across retries. The sync can run from a request-thread
# signal, so an unbounded server-supplied Retry-After must not hold it.
RETRY_BUDGET_SECONDS = 15.0

# Luxembourg postcodes are four digits, usually written with an "L-" prefix.
_POSTCODE_RE = re.compile(r"\bL?-?\s?(\d{4})\b")
# A house number is a digit run optionally followed by a letter or a range
# ("12", "12A", "12-14"), anchored to either end of the street line. Luxembourg
# writes "12, rue de la Gare" far more often than "rue de la Gare 12", but both
# occur in the venue addresses staff type in, so both ends are checked.
_LEADING_NUMBER_RE = re.compile(r"^(\d+[A-Za-z]?(?:\s*[-/]\s*\d+[A-Za-z]?)?)\s*,?\s+")
_TRAILING_NUMBER_RE = re.compile(r"\s+(\d+[A-Za-z]?(?:\s*[-/]\s*\d+[A-Za-z]?)?)$")


class EchoLuError(Exception):
    """An echo.lu API call failed.

    Carries the status code and response body because echo.lu returns
    field-level validation detail there — almost always an unknown
    category/audience/format slug — and that detail is what makes a failure
    actionable in the admin without going to the logs.
    """

    def __init__(self, message, status_code=None, body=None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body

    def __str__(self):
        base = super().__str__()
        if self.status_code is None:
            return base
        detail = f" (HTTP {self.status_code})"
        if self.body:
            detail += f": {str(self.body)[:800]}"
        return base + detail


class EchoLuNotConfigured(EchoLuError):
    """No API key. Raised instead of sending ``api-key: ``, which echo.lu
    answers with a bare 401 that looks exactly like a revoked key."""


class EchoLuOrphanedCreate(EchoLuError):
    """echo.lu accepted a create but did not return an id.

    Its own class because it is the one failure that must not be retried: the
    listing exists, we hold no handle on it, and another create only adds a
    second one. :func:`sync_event` reads the type to park the row in
    ``ORPHANED`` rather than the ordinary retryable ``FAILED``.
    """


def is_sync_enabled():
    """True when this environment is allowed to write to echo.lu."""
    return bool(
        getattr(settings, "ECHO_LU_SYNC_ENABLED", False)
        and getattr(settings, "ECHO_LU_API_KEY", "")
    )


def _setting_list(name, default=""):
    """Read a comma-separated settings value into a clean list."""
    raw = getattr(settings, name, default) or ""
    return [item.strip() for item in raw.split(",") if item.strip()]


class EchoLuClient:
    """Thin wrapper over the echo.lu experiences API."""

    def __init__(self, api_key=None, base_url=None, timeout=None, max_retries=None):
        self.api_key = (api_key or getattr(settings, "ECHO_LU_API_KEY", "")).strip()
        base = base_url or getattr(
            settings, "ECHO_LU_API_BASE_URL", "https://api.echo.lu/v1"
        )
        self.base_url = base.strip().rstrip("/")
        self.timeout = timeout or getattr(settings, "ECHO_LU_TIMEOUT_SECONDS", 20)
        # Callers running inside an HTTP request pass 0: retries are what turn
        # one slow call into a minute of held request, and the hourly sweep is
        # already the retry for anything the fast path drops.
        self.max_retries = MAX_RETRIES if max_retries is None else max_retries

    def _headers(self):
        if not self.api_key:
            raise EchoLuNotConfigured(
                "ECHO_LU_API_KEY is not configured — set it in the environment "
                "(App Service application settings) and restart."
            )
        return {
            "api-key": self.api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _request(self, method, path, json_body=None, params=None, retry_statuses=None):
        """Send one API call, retrying only transient failures.

        Returns the decoded JSON body (or ``{}`` for an empty 204), and raises
        ``EchoLuError`` for anything that is not a 2xx.

        ``retry_statuses`` narrows what counts as retryable, and passing a set
        that excludes the 5xx family also switches off transport-level retries
        — see :meth:`create_experience` for why that pairing is the point.
        """
        if retry_statuses is None:
            retry_statuses = RETRY_STATUSES
        # A dropped socket and a 502 are indistinguishable from here: either
        # can mean "echo.lu never saw it" or "echo.lu committed it and we lost
        # the answer". A caller that has narrowed the status set to the
        # unambiguous ones is telling us replays are unsafe, so a lost socket
        # must not be replayed either.
        retry_transport = bool(retry_statuses & {500, 502, 503, 504})
        url = f"{self.base_url}/{path.lstrip('/')}"
        headers = self._headers()
        slept = 0.0
        last_response = None

        for attempt in range(self.max_retries + 1):
            try:
                response = requests.request(
                    method,
                    url,
                    headers=headers,
                    json=json_body,
                    params=params,
                    timeout=self.timeout,
                )
            except requests.RequestException as exc:
                # A connection error is transient in the same way a 503 is, so
                # it gets the same budget rather than failing the whole sync on
                # one dropped socket.
                if (
                    not retry_transport
                    or attempt >= self.max_retries
                    or slept + DEFAULT_RETRY_AFTER > RETRY_BUDGET_SECONDS
                ):
                    raise EchoLuError(f"echo.lu {method} {path} failed: {exc}") from exc
                time.sleep(DEFAULT_RETRY_AFTER)
                slept += DEFAULT_RETRY_AFTER
                continue

            last_response = response
            if response.status_code not in retry_statuses:
                break

            retry_after = DEFAULT_RETRY_AFTER
            header = response.headers.get("Retry-After")
            if header:
                try:
                    retry_after = float(header)
                except (TypeError, ValueError):
                    pass

            if (
                attempt >= self.max_retries
                or slept + retry_after > RETRY_BUDGET_SECONDS
            ):
                break

            logger.warning(
                "echo.lu %s %s returned %s; retry %s/%s after %ss",
                method,
                path,
                response.status_code,
                attempt + 1,
                self.max_retries,
                retry_after,
            )
            time.sleep(retry_after)
            slept += retry_after

        response = last_response
        if response is None:  # pragma: no cover - defensive
            raise EchoLuError(f"echo.lu {method} {path} produced no response")

        if not 200 <= response.status_code < 300:
            raise EchoLuError(
                f"echo.lu {method} {path} rejected",
                status_code=response.status_code,
                body=_safe_body(response),
            )

        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError:
            # A 2xx with a non-JSON body is not an error we should abort on —
            # DELETE in particular answers with an empty or plain-text body.
            return {}

    # --- experiences -----------------------------------------------------

    def create_experience(self, payload):
        """Create a listing. The one call here that must never be replayed.

        Every other endpoint is idempotent — the same PUT, PATCH or DELETE
        twice leaves echo.lu in the state one of them would have. A create is
        not: there is no idempotency key and no external-reference field, so a
        replay after a timeout echo.lu had already committed produces a second
        listing whose id we never learn. Retrying is therefore restricted to
        429, the one answer that says outright the request was not processed.
        """
        return self._request(
            "POST",
            "/experiences",
            json_body=payload,
            retry_statuses=CREATE_RETRY_STATUSES,
        )

    def update_experience(self, experience_id, payload):
        return self._request("PUT", f"/experiences/{experience_id}", json_body=payload)

    def get_experience(self, experience_id):
        return self._request("GET", f"/experiences/{experience_id}")

    def list_experiences(self, **params):
        return self._request("GET", "/experiences", params=params or None)

    def delete_experience(self, experience_id):
        return self._request("DELETE", f"/experiences/{experience_id}")

    def cancel_experience(self, experience_id):
        return self._request("PATCH", f"/experiences/{experience_id}/cancel")

    def unpublish_experience(self, experience_id):
        return self._request("PATCH", f"/experiences/{experience_id}/unpublish")

    # --- vocabularies ----------------------------------------------------

    #: The facets echo.lu validates against its own vocabularies. An unknown
    #: value in any of them rejects the whole experience, which is why the
    #: defaults in settings ship empty and `manage.py echo_taxonomy` exists.
    TAXONOMIES = ("categories", "audiences", "formats", "environments")

    def list_taxonomy(self, kind):
        if kind not in self.TAXONOMIES:
            raise ValueError(f"Unknown echo.lu taxonomy: {kind}")
        return self._request("GET", f"/{kind}")


def _safe_body(response):
    """Best-effort decode of an error body for logging and admin display."""
    try:
        return response.json()
    except ValueError:
        return (response.text or "")[:800]


def extract_experience_id(payload):
    """Pull the experience id out of a create/update response.

    The API is not consistent about where the id lands across versions
    (v1.0.4 vs the v1.1.0 sandbox) and the response schema is not published in
    full, so every plausible spelling is tried rather than hard-coding one and
    silently storing ``None`` — which would make the next sync create a
    duplicate listing.
    """
    if not isinstance(payload, dict):
        return ""
    for key in ("id", "_id", "experienceId", "experience_id", "uuid"):
        value = payload.get(key)
        if value:
            return str(value)
    for container in ("data", "experience", "result"):
        nested = payload.get(container)
        if isinstance(nested, dict):
            found = extract_experience_id(nested)
            if found:
                return found
    return ""


# ---------------------------------------------------------------------------
# MeetupEvent -> experience payload
# ---------------------------------------------------------------------------


def should_publish(event):
    """Whether this event belongs on echo.lu at all.

    Deliberately strict — echo.lu is a public, indexed, national listing, so
    the cost of leaking an event that should not be there is much higher than
    the cost of missing one:

    * unpublished or cancelled events are not public on crush.lu either;
    * private invitation events are, by definition, not for a public portal —
      publishing one would hand the whole country a guest list it was never
      meant to see;
    * events that have already ended are noise, and echo.lu has its own
      lifecycle for finished experiences.
    """
    if not event.is_published or event.is_cancelled:
        return False
    if event.is_private_invitation:
        return False
    return event.end_time > timezone.now()


def _translated(event, field):
    """Read the English column of a modeltranslation field with fallback.

    The bare descriptor resolves to whichever language is active, and this runs
    from management commands and background tasks where that is whatever
    LANGUAGE_CODE happens to be. echo.lu takes one string per experience, so
    pick English explicitly and fall back through the configured languages
    rather than shipping an empty title when only `title_fr` was filled in.
    """
    for code in ("en",) + tuple(c for c, _label in settings.LANGUAGES):
        value = getattr(event, f"{field}_{code.replace('-', '_')}", None)
        if value:
            return value
    return getattr(event, field, "") or ""


def _rfc3339(value):
    """Format a datetime as RFC 3339 UTC, the format echo.lu's examples use.

    echo.lu renders every date in Europe/Luxembourg whatever offset it is
    given, so UTC is sent for an unambiguous instant rather than a local
    wall-clock time that would shift by an hour across the DST boundary.
    """
    return value.astimezone(dt_timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _absolute_media_url(url):
    """Make a storage URL absolute.

    Azure Blob storage already hands back absolute URLs; local development
    storage returns ``/media/...``. echo.lu fetches the picture server-side, so
    a relative path would 404 on their side and drop the image silently.
    """
    if not url:
        return ""
    if url.startswith("http://") or url.startswith("https://"):
        return url
    return f"https://crush.lu{url}"


def parse_address(address, canton=""):
    """Split a free-text venue address into echo.lu's address object.

    ``MeetupEvent.address`` is a single TextField that staff fill in by hand,
    while echo.lu wants street/number/postcode/town separately. Nothing here
    guesses: a component that cannot be identified with confidence is left
    blank, because echo.lu shows these fields verbatim and a wrong house number
    sends people to the wrong door. The full first line always survives in
    ``street`` so the listing is never less informative than what we hold.
    """
    lines = [line.strip(" ,\t") for line in (address or "").splitlines()]
    lines = [line for line in lines if line]

    postcode = ""
    town = ""
    street_line = lines[0] if lines else ""

    # The postcode line is whichever line contains a four-digit group; the town
    # is the rest of that line once the postcode is removed.
    for index, line in enumerate(lines):
        match = _POSTCODE_RE.search(line)
        if not match:
            continue
        postcode = match.group(1)
        if index == 0:
            # Single-line address: everything before the postcode is the
            # street, everything after it is the town.
            street_line = line[: match.start()].strip(" ,-")
            town = line[match.end() :].strip(" ,-")
        else:
            # A line of its own: the town is whatever sits either side of the
            # postcode, and line 0 stays the street.
            town = (
                line[: match.start()].strip(" ,-")
                + " "
                + line[match.end() :].strip(" ,-")
            ).strip(" ,-")
        break

    number = ""
    leading = _LEADING_NUMBER_RE.match(street_line)
    if leading:
        number = leading.group(1).strip()
        street_line = street_line[leading.end() :].strip(" ,")
    else:
        trailing = _TRAILING_NUMBER_RE.search(street_line)
        if trailing:
            number = trailing.group(1).strip()
            street_line = street_line[: trailing.start()].strip(" ,")

    return {
        "street": street_line,
        "number": number,
        "postcode": postcode,
        # `canton` is the public-facing region on crush.lu and is far more
        # reliably filled in than a town parsed out of free text, so it wins.
        "town": town or canton or "",
        "commune": canton or "",
        "country": "Luxembourg",
    }


def build_experience_payload(event):
    """Map a MeetupEvent onto the echo.lu experience schema.

    Only fields we can fill *correctly* are sent. In particular ``duration`` is
    omitted from the date entry even though the schema accepts it: its unit is
    not documented, ``from``/``to`` already pin the span exactly, and a
    duration in the wrong unit would contradict them on the public listing.
    """
    from ..utils.i18n import build_absolute_url

    title = _translated(event, "title")
    description = _translated(event, "description")
    event_url = build_absolute_url(
        "crush_lu:event_detail", lang="en", kwargs={"event_id": event.pk}
    )

    payload = {
        "title": title,
        "subtitle": event.get_event_type_display(),
        "description": description,
        "dates": [
            {
                "from": _rfc3339(event.date_time),
                "to": _rfc3339(event.end_time),
                "purchaseLink": event_url,
            }
        ],
        "venues": [event.location] if event.location else [],
        "location": {"address": parse_address(event.address, event.canton)},
        "contact": {
            "name": getattr(settings, "ECHO_LU_CONTACT_NAME", "Crush.lu"),
            "company": getattr(settings, "ECHO_LU_CONTACT_COMPANY", "Crush.lu"),
            "email": getattr(settings, "ECHO_LU_CONTACT_EMAIL", ""),
            "phone": getattr(settings, "ECHO_LU_CONTACT_PHONE", ""),
            # The organiser's site, which is what this field means alongside
            # name/email/phone/company. The event-specific link is already
            # `purchaseLink`. Falls back to the event page rather than sending
            # nothing, so an unconfigured deployment still gives readers
            # somewhere useful to go.
            "website": getattr(settings, "ECHO_LU_CONTACT_WEBSITE", "") or event_url,
        },
        "tickets": _build_tickets(event),
        "tags": _setting_list("ECHO_LU_DEFAULT_TAGS"),
    }

    # Coordinates are strings in the echo.lu schema, and only sent when we have
    # them — "None" as a string would be worse than an absent key.
    if event.latitude is not None and event.longitude is not None:
        payload["location"]["address"]["latitude"] = str(event.latitude)
        payload["location"]["address"]["longitude"] = str(event.longitude)

    if event.image:
        try:
            picture_url = _absolute_media_url(event.image.url)
        except ValueError:
            # An ImageField whose storage cannot build a URL (missing file in
            # local dev) must not fail the whole sync — the listing is still
            # worth publishing without a banner.
            picture_url = ""
        if picture_url:
            payload["pictures"] = [
                {"url": picture_url, "copy": "Crush.lu", "alt": title}
            ]

    languages = [code for code in (event.languages or []) if code]
    if languages:
        payload["languages"] = languages

    for facet, setting_name in (
        ("categories", "ECHO_LU_DEFAULT_CATEGORIES"),
        ("audiences", "ECHO_LU_DEFAULT_AUDIENCES"),
        ("formats", "ECHO_LU_DEFAULT_FORMATS"),
        ("environments", "ECHO_LU_DEFAULT_ENVIRONMENTS"),
    ):
        values = _setting_list(setting_name)
        if facet == "categories":
            values = _categories_for(event, values)
        if values:
            payload[facet] = values

    # Drop empty scalars rather than sending "" — echo.lu treats an empty
    # string as a supplied value and will render a blank contact line for it.
    payload["contact"] = {k: v for k, v in payload["contact"].items() if v}
    return payload


def _categories_for(event, defaults):
    """Per-event-type categories layered onto the configured defaults."""
    raw = getattr(settings, "ECHO_LU_CATEGORY_MAP", "") or ""
    mapped = []
    if raw:
        try:
            mapping = json.loads(raw)
        except ValueError:
            logger.warning(
                "ECHO_LU_CATEGORY_MAP is not valid JSON; ignoring it and using "
                "ECHO_LU_DEFAULT_CATEGORIES only"
            )
            mapping = {}
        if isinstance(mapping, dict):
            mapped = mapping.get(event.event_type) or []
            if isinstance(mapped, str):
                mapped = [mapped]

    seen = []
    for value in list(defaults) + list(mapped):
        if value and value not in seen:
            seen.append(value)
    return seen


def _build_tickets(event):
    """Describe the price. Free events get an explicit zero-price ticket.

    Omitting `tickets` entirely for a free event leaves echo.lu showing no
    price information at all, which reads as "unknown" rather than "free" and
    costs us signups.
    """
    fee = event.registration_fee or Decimal("0")
    if fee > 0:
        return [{"title": "Ticket", "price": float(fee), "currency": "EUR"}]
    return [{"title": "Free entry", "price": 0, "currency": "EUR"}]


def payload_fingerprint(payload):
    """Stable hash of a payload, used to skip no-op updates.

    ``sort_keys`` matters: dict ordering follows insertion order, and a payload
    assembled through a different branch (image present vs absent) would
    otherwise hash differently while describing exactly the same listing.
    """
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def _write_experience(sync, payload, fingerprint, client):
    """Send one write for `sync` and record the id it settles on.

    Raises :class:`EchoLuError` *without* recording the failure: on the create
    path this runs inside a transaction, and the raise would roll back the very
    row that says what went wrong. :func:`sync_event` records it after the
    transaction has unwound.
    """
    if sync.experience_id:
        response = client.update_experience(sync.experience_id, payload)
        outcome = "updated"
    else:
        response = client.create_experience(payload)
        outcome = "created"

    experience_id = extract_experience_id(response) or sync.experience_id
    if not experience_id:
        # A create that returns 2xx with no id leaves an orphan listing we can
        # never update or delete. Storing a blank id would make every later
        # sync create another one beside it, so the row goes to a state that
        # blocks automatic creates outright until somebody resolves it.
        raise EchoLuOrphanedCreate(
            "echo.lu accepted the experience but returned no id; the listing "
            "exists and is now untracked — run `sync_events_to_echo --audit` "
            "to find it, then adopt its id or delete it in the organiser back "
            "office. No further create will be attempted for this event."
        )

    sync.mark_success(experience_id, fingerprint)
    return outcome


def sync_event(event, client=None, force=False, dry_run=False):
    """Bring echo.lu in line with one event.

    Returns one of ``"created"``, ``"updated"``, ``"unchanged"``,
    ``"withdrawn"``, ``"suppressed"``, ``"blocked"``, ``"skipped"`` or
    ``"disabled"``. Raises ``EchoLuError`` when the API rejects the write —
    callers decide whether that is fatal (the management command reports it)
    or merely logged (the signal path).

    ``"blocked"`` is the one outcome that never resolves on its own; callers
    that report health should treat it like a failure, not like a no-op.
    """
    from ..models.echo_lu import EchoExperienceSync

    if not dry_run and not is_sync_enabled():
        return "disabled"

    sync = getattr(event, "echo_sync", None)

    if sync is not None and sync.status == EchoExperienceSync.Status.ORPHANED:
        # Checked before anything else, including the dry-run branch: a
        # listing exists that we hold no id for, so every automatic path is
        # closed until a human resolves it, and a dry run that answered "would
        # create" would be describing the very thing that must not happen.
        # `force` deliberately does not override this either.
        return "blocked"

    if not should_publish(event):
        # Nothing was ever published, so there is nothing to take down.
        if sync is None or not sync.experience_id:
            return "skipped"
        if sync.status == EchoExperienceSync.Status.WITHDRAWN:
            return "skipped"
        return withdraw_event(event, client=client, dry_run=dry_run)

    payload = build_experience_payload(event)
    fingerprint = payload_fingerprint(payload)

    if dry_run:
        return "created" if sync is None or not sync.experience_id else "updated"

    if sync is None:
        sync, _created = EchoExperienceSync.objects.get_or_create(event=event)

    if (
        not force
        and sync.status == EchoExperienceSync.Status.SUPPRESSED
        and sync.experience_id
    ):
        # Taken down by hand while still eligible. The event's own fields say
        # "publish me", so without this the next pass would put it straight
        # back and the removal would look like it never happened.
        return "suppressed"

    if (
        not force
        and sync.status == EchoExperienceSync.Status.SYNCED
        and sync.payload_hash == fingerprint
        and sync.experience_id
    ):
        return "unchanged"

    client = client or EchoLuClient()
    try:
        if sync.experience_id:
            # A PUT is idempotent: two of them for the same event settle on the
            # same listing, so the steady state takes no lock.
            outcome = _write_experience(sync, payload, fingerprint, client)
        else:
            # The POST is the one non-idempotent call in this module, and
            # echo.lu's create payload carries no external-reference field, so
            # a second POST for the same event yields a duplicate listing whose
            # id we never learn — the exact failure this module is built
            # around. Three entry points reach here (the save-signal task, the
            # hourly sweep, the admin action) and nothing stops two of them
            # landing on the same event at once, so the "no id yet" decision is
            # re-made against a locked row rather than trusted from the read
            # above, and the id is written back before the lock drops. A caller
            # that loses the race wakes up, sees the id, and updates instead.
            #
            # This holds the row lock across the HTTP call, which is the only
            # way check-then-POST is atomic. It is bounded by the client's
            # retry budget, and it is scoped to the create alone: every later
            # sync of this event takes the unlocked path above.
            with transaction.atomic():
                sync = EchoExperienceSync.objects.select_for_update().get(pk=sync.pk)
                # Re-point the caller's event at the row we actually wrote.
                # The locked read replaced the instance `event.echo_sync` still
                # holds, and that stale copy has no experience_id — so a caller
                # that syncs and then withdraws in the same breath would look
                # at it, conclude nothing was ever published, and skip.
                event.echo_sync = sync
                outcome = _write_experience(sync, payload, fingerprint, client)
    except EchoLuOrphanedCreate as exc:
        # Terminal, not retryable — park it where the create branch is closed.
        sync.mark_orphaned(exc)
        raise
    except EchoLuError as exc:
        # Out here rather than at the raise site: inside the atomic block the
        # rollback would discard the failure row along with the failed write.
        sync.mark_failure(exc)
        raise

    logger.info(
        "echo.lu experience %s for event %s (%s)",
        sync.experience_id,
        event.pk,
        outcome,
    )
    return outcome


def withdraw_event(event, client=None, dry_run=False, explicit=False):
    """Take an event's listing down from echo.lu.

    Cancelled events are *cancelled* on echo.lu rather than deleted: the portal
    shows a cancellation notice, which is what somebody who already saw the
    listing needs to see. Everything else (unpublished, gone private, past its
    end) is unpublished, which removes it from the public site while leaving
    the experience addressable so re-publishing reuses the same listing.

    Cancelling only applies while the event is otherwise still public, because
    a cancellation notice is a *published* thing — it keeps the title, venue
    and date on a national portal. An event that was cancelled and also pulled
    from public view has to come down properly: privacy is the stronger
    instruction, so it is unpublished instead and nobody is told why.

    `explicit` marks a take-down somebody asked for by hand, as opposed to one
    the event's own state implied — see ``EchoExperienceSync.mark_withdrawn``.
    """
    from ..models.echo_lu import EchoExperienceSync

    sync = getattr(event, "echo_sync", None)
    if sync is None or not sync.experience_id:
        return "skipped"
    if dry_run:
        return "withdrawn"
    if not is_sync_enabled():
        return "disabled"

    still_public = event.is_published and not event.is_private_invitation
    client = client or EchoLuClient()
    try:
        if event.is_cancelled and still_public:
            client.cancel_experience(sync.experience_id)
        else:
            client.unpublish_experience(sync.experience_id)
    except EchoLuError as exc:
        sync.mark_failure(exc)
        raise

    sync.mark_withdrawn(explicit=explicit)
    logger.info(
        "echo.lu experience %s withdrawn for event %s", sync.experience_id, event.pk
    )
    return "withdrawn"


def delete_event_listing(event, client=None):
    """Remove the listing outright and forget its id.

    Only for genuine mistakes — an event published to echo.lu that never should
    have been. Ordinary take-downs go through :func:`withdraw_event`, which
    keeps the id so the listing can come back.
    """
    from ..models.echo_lu import EchoExperienceSync

    sync = getattr(event, "echo_sync", None)
    if sync is None or not sync.experience_id:
        return "skipped"
    if not is_sync_enabled():
        return "disabled"

    client = client or EchoLuClient()
    try:
        client.delete_experience(sync.experience_id)
    except EchoLuError as exc:
        sync.mark_failure(exc)
        raise

    sync.experience_id = ""
    sync.payload_hash = ""
    sync.status = EchoExperienceSync.Status.PENDING
    sync.last_error = ""
    sync.last_attempted_at = timezone.now()
    sync.save(
        update_fields=[
            "experience_id",
            "payload_hash",
            "status",
            "last_error",
            "last_attempted_at",
            "updated_at",
        ]
    )
    return "deleted"


def events_needing_sync(queryset=None):
    """Events whose echo.lu listing may be out of date.

    Covers both directions: anything currently publishable, plus anything that
    has a live listing but no longer qualifies (just unpublished, just gone
    private, just cancelled) and therefore needs taking down.
    """
    from ..models.events import MeetupEvent
    from ..models.echo_lu import EchoExperienceSync

    if queryset is None:
        queryset = MeetupEvent.objects.all()

    now = timezone.now()
    publishable = queryset.filter(
        is_published=True,
        is_cancelled=False,
        is_private_invitation=False,
        date_time__gte=MeetupEvent.live_lookback_cutoff(now),
    ).exclude(
        # Both states describe a listing that must not be (re)created from the
        # event's fields alone: one was taken down by hand while the event
        # still qualified, the other has an untracked listing already live.
        # Everything else here keys off the event, which still says "publish
        # me", so only the sync row can rule them out.
        echo_sync__status__in=[
            EchoExperienceSync.Status.SUPPRESSED,
            EchoExperienceSync.Status.ORPHANED,
        ]
    )
    withdrawable = (
        queryset.filter(echo_sync__isnull=False)
        .exclude(echo_sync__experience_id="")
        .exclude(
            # Already down, by either route — taking it down again is a wasted
            # API call whose only effect is to reset the timestamps.
            echo_sync__status__in=[
                EchoExperienceSync.Status.WITHDRAWN,
                EchoExperienceSync.Status.SUPPRESSED,
            ]
        )
    )
    return (publishable | withdrawable).distinct().select_related("echo_sync")
