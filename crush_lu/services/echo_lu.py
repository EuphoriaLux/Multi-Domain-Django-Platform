"""echo.lu partner API client — publishes Crush.lu events to Luxembourg's
national events portal.

API reference: https://api.echo.lu/ (Echo API v1.1.0). There is no sandbox —
every call here reaches the live national portal.

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
* Responses are enveloped: ``{"stats": {...}, "record": {...}}`` for a single
  object and ``{"stats": {...}, "records": [...]}`` for a list. Lists page with
  ``pagination[page]`` / ``pagination[perPage]``.
* Dates are RFC 3339; echo.lu renders them in Europe/Luxembourg regardless of
  the offset sent, so we send UTC.
* ``venues`` is a list of ids from echo.lu's own venue registry, not names.
* ``title``, ``description``, ``pictures``, ``dates``, ``venues``,
  ``categories``, ``audiences``, ``languages``, ``environments`` and
  ``formats`` are all required — see ``REQUIRED_EXPERIENCE_FIELDS``.
* Listings are **moderated**. A create carries ``status``: ``draft`` parks it in
  the organiser back office, ``pending`` submits it for validation.

Everything here is gated on ``settings.ECHO_LU_SYNC_ENABLED``, which defaults
to False. That switch is the only thing standing between a shell and the live
portal, because there is no second environment to practise on.
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
from django.db import models, transaction
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


class EchoLuNotSent(EchoLuError):
    """We refused to send the request; echo.lu never heard of it.

    Its own class because "nothing was created" has to be *provable*, and the
    only other proof available is a 4xx status code. A locally-raised error
    carries no status, so without this type an unsendable payload — a blanked
    ECHO_LU_DEFAULT_* leaving a required facet empty, say — looked exactly like
    a create whose answer was lost, and every affected event was parked in
    ``ORPHANED``: blocked from syncing, and pointing its operator at an
    ``--audit`` that cannot possibly find anything, because nothing was sent.
    """


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
        """One page of the organisation's experiences.

        A 404 here means "none", not "broken": echo.lu documents
        ListExperienceNotFoundResponse and answers it for an organisation with
        no experiences yet — which is exactly the state `--audit` is most
        likely to be run in. Swallowing it only for the *list* call is
        deliberate; a 404 from GetExperience means the listing is gone and must
        stay an error.
        """
        try:
            return self._request("GET", "/experiences", params=params or None)
        except EchoLuError as exc:
            if exc.status_code == 404:
                return {"records": []}
            raise

    def iter_experiences(self, per_page=100):
        """Every experience, page by page.

        echo.lu pages with ``pagination[page]`` / ``pagination[perPage]`` and
        documents that a page past the end "returns nothing". The audit used to
        make one unpaged call and treat the answer as the whole portal, which
        is how it could report a listing as deleted upstream while it was live
        — and `--forget` on that advice creates the duplicate the whole design
        is built to avoid.
        """
        page = 0
        seen = 0
        while True:
            response = self.list_experiences(
                **{"pagination[page]": page, "pagination[perPage]": per_page}
            )
            records = response_records(response)
            if not records:
                return
            for record in records:
                yield record
            seen += len(records)
            if len(records) < per_page:
                return
            page += 1
            if page > 1000:  # pragma: no cover - runaway guard
                logger.error(
                    "[ECHO] Stopping experience paging at %s records; the API "
                    "kept returning full pages",
                    seen,
                )
                return

    def delete_experience(self, experience_id):
        return self._request("DELETE", f"/experiences/{experience_id}")

    def cancel_experience(self, experience_id):
        return self._request("PATCH", f"/experiences/{experience_id}/cancel")

    def unpublish_experience(self, experience_id):
        return self._request("PATCH", f"/experiences/{experience_id}/unpublish")

    # --- venues ----------------------------------------------------------

    def list_venues(self, **params):
        return self._request("GET", "/venues", params=params or None)

    def create_venue(self, payload):
        """Register a venue and get its id back.

        Not retried on anything but 429, for the same reason creating an
        experience is not: venues are a shared national registry with
        server-assigned ids, so a replay after a lost response leaves a
        duplicate venue nobody can tell from the original.
        """
        return self._request(
            "POST", "/venues", json_body=payload, retry_statuses=CREATE_RETRY_STATUSES
        )

    # --- vocabularies ----------------------------------------------------

    #: The facets echo.lu validates against its own vocabularies. An unknown
    #: value in any of them rejects the whole experience, which is why
    #: `manage.py echo_taxonomy` exists.
    TAXONOMIES = ("categories", "audiences", "formats", "environments")

    def list_taxonomy(self, kind):
        """One vocabulary.

        ``/categories`` alone is a 400 — it requires ``?type=`` and takes
        ``experience`` or ``venue``. The original client omitted it, so the one
        facet with 190 values and the highest chance of a typo was also the one
        the taxonomy command could never read.
        """
        if kind not in self.TAXONOMIES:
            raise ValueError(f"Unknown echo.lu taxonomy: {kind}")
        params = {"type": "experience"} if kind == "categories" else None
        return self._request("GET", f"/{kind}", params=params)


def _safe_body(response):
    """Best-effort decode of an error body for logging and admin display."""
    try:
        return response.json()
    except ValueError:
        return (response.text or "")[:800]


def extract_experience_id(payload):
    """Pull the experience id out of a create/update response.

    The documented v1.1.0 shape is ``{"stats": {...}, "record": {"id": ...}}``
    — the id is inside ``record``, which is why that container comes first.
    This was the single defect that would have broken every first publish: the
    original spelling list was reconstructed without access to the API and did
    not include ``record``, so `_write_experience` saw no id, raised
    :class:`EchoLuOrphanedCreate`, and parked every newly created listing in a
    state that blocks all further creates until somebody resolves it by hand.

    The other spellings are kept as fallbacks — they cost nothing and the docs
    still carry a "data models subject to change" warning.
    """
    if not isinstance(payload, dict):
        return ""
    for key in ("id", "_id", "experienceId", "experience_id", "uuid"):
        value = payload.get(key)
        if value:
            return str(value)
    for container in ("record", "data", "experience", "result"):
        nested = payload.get(container)
        if isinstance(nested, dict):
            found = extract_experience_id(nested)
            if found:
                return found
    return ""


def response_records(payload):
    """The list of records out of a list response.

    Documented shape is ``{"stats": {...}, "records": [...]}``. ``records`` was
    missing from the original reconstruction, so every list call parsed as
    empty — which made `--audit` report an empty portal and label every tracked
    listing as deleted upstream. Returns ``[]`` for anything unrecognisable
    rather than guessing.
    """
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in ("records", "data", "items", "results", "experiences"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
    return []


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


def _cancellation_notice_wanted(event):
    """Whether a cancellation notice still belongs on echo.lu.

    Everything :func:`should_publish` asks except the cancellation itself. A
    notice is a *published* thing — title, venue and date, with "cancelled" on
    it — so it belongs there exactly as long as the listing would have, had the
    event not been cancelled. Once the event is unpublished, made private, or
    simply finishes, the notice comes down like any other listing.
    """
    if not event.is_published or event.is_private_invitation:
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

    # Which line holds the postcode, and where in it. An "L-" prefixed match
    # wins outright, and otherwise the LAST four-digit group does — because a
    # venue whose name begins with digits ("1535 Creative Hub, 45 rue Emile
    # Mark, L-4620 Differdange") otherwise donates its name to the postcode
    # field and leaves the street empty.
    best = None
    for index, line in enumerate(lines):
        for match in _POSTCODE_RE.finditer(line):
            prefixed = match.group(0).strip().lower().startswith("l")
            if best is None or prefixed or not best[2]:
                best = (index, match, prefixed)
            if prefixed:
                break
        if best is not None and best[2]:
            break

    if best is not None:
        index, match, _prefixed = best
        postcode = match.group(1)
        if index == 0:
            # Single-line address: everything before the postcode is the
            # street, everything after it is the town.
            street_line = lines[0][: match.start()].strip(" ,-")
            town = lines[0][match.end() :].strip(" ,-")
        else:
            # A line of its own: the town is whatever sits either side of the
            # postcode.
            town = (
                lines[index][: match.start()].strip(" ,-")
                + " "
                + lines[index][match.end() :].strip(" ,-")
            ).strip(" ,-")
            # The street is chosen from the lines ABOVE the postcode, not
            # blindly from line 0. Staff routinely put the venue name first
            # ("Café Konrad" / "7, rue du Nord" / "L-2229 Luxembourg"), and
            # taking line 0 then dropped the actual street from a national
            # listing entirely. Prefer the last line that carries a house
            # number; failing that, the line nearest the postcode.
            above = lines[:index]
            numbered = [
                line
                for line in above
                if _LEADING_NUMBER_RE.match(line) or _TRAILING_NUMBER_RE.search(line)
            ]
            street_line = (numbered or above)[-1] if above else ""

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

    # The promise this function makes: the listing is never LESS informative
    # than what we hold. Splitting can empty the street — an address that is
    # only a house number, or a first line the postcode logic consumed — and a
    # blank street on a national listing is worse than an unsplit one, so put
    # the original back and let it travel whole.
    if not street_line:
        street_line = (lines[0] if lines else "").strip(" ,-")
        number = number if street_line != (lines[0] if lines else "") else ""

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


def venue_key(name, postcode=""):
    """Cache key for a venue: lowercased name plus postcode.

    Casing and spacing drift in a hand-typed `location` must not register a
    second venue for the same place, and the postcode keeps two venues that
    share a common name ("Brasserie du Centre") apart.
    """
    cleaned = re.sub(r"\s+", " ", (name or "").strip().lower())
    return f"{cleaned}|{postcode}".strip("|")


def cached_venue_ids(event):
    """Venue ids already known locally — no API calls, no writes.

    The read-only half of :func:`resolve_venue_ids`, for the paths that must
    not touch echo.lu: the dry run, and the pre-lock fingerprint that decides
    whether an unchanged event needs a write at all.
    """
    from ..models.echo_lu import EchoVenue

    if not event.location:
        return []
    address = parse_address(event.address, event.canton)
    key = venue_key(event.location, address.get("postcode", ""))
    row = EchoVenue.objects.filter(key=key).first()
    return [row.venue_id] if row else []


def fallback_picture_url():
    """The picture to send for an event that has no image of its own.

    ``pictures`` is required, and echo.lu fetches them **server-side**, so a URL
    that 404s is a rejected experience rather than a listing without a banner.
    The default is therefore the same file the ``og:image`` tag serves.

    Resolved **here, at call time**, not bound to a settings constant. Binding
    it in ``settings.py`` snapshots whatever ``SOCIAL_PREVIEW_IMAGE_URL`` is at
    that line — and it is reassigned twice afterwards, once for Azure blob
    storage and again in ``production.py`` for the CDN domain. So the snapshot
    silently kept the wrong URL on every real deployment, which is exactly the
    drift this fallback was introduced to stop. Reading both settings when the
    payload is built means whichever settings module won has the final say.
    """
    return getattr(settings, "ECHO_LU_FALLBACK_IMAGE", "") or getattr(
        settings, "SOCIAL_PREVIEW_IMAGE_URL", ""
    )


def _venue_payload(event, address):
    """A CreateVenue body for this event's location."""
    return {
        "title": event.location,
        "location": {"address": address},
    }


def resolve_venue_ids(event, client, address=None):
    """The echo.lu venue ids for this event, registering one if needed.

    Returns a list because the field is a list; in practice it is one venue.

    Three steps, cheapest first: the local cache, then echo.lu's registry, then
    registering a new venue. The middle step matters because that registry is
    shared with every other organiser in the country — a venue we create beside
    an identical one that already exists is litter in a public dataset, not
    just a wasted call.

    Matching is deliberately strict: normalised equality on the venue name, not
    a fuzzy or substring match. Attaching our event to somebody else's venue
    because the names looked similar is a worse failure than registering a
    duplicate, and it is the kind that is invisible from our side.
    """
    from ..models.echo_lu import EchoVenue

    if not event.location:
        return []

    address = (
        address if address is not None else parse_address(event.address, event.canton)
    )
    key = venue_key(event.location, address.get("postcode", ""))

    cached = EchoVenue.objects.filter(key=key).first()
    if cached is not None:
        return [cached.venue_id]

    target = venue_key(event.location)
    match_id, match_title = "", ""
    try:
        for record in _iter_venue_records(client, address):
            title = record.get("title") or ""
            if isinstance(title, dict):
                title = title.get("en") or next(iter(title.values()), "")
            if venue_key(str(title)) == target:
                match_id = extract_experience_id(record)
                match_title = str(title)
                if match_id:
                    break
    except EchoLuError as exc:
        # A registry we could not read is not a reason to register a duplicate
        # into it. Fail the sync instead; the sweep retries in an hour.
        raise EchoLuError(
            f"could not search echo.lu venues for {event.location!r}: {exc}"
        ) from exc

    created_by_us = False
    if not match_id:
        response = client.create_venue(_venue_payload(event, address))
        match_id = extract_experience_id(response)
        match_title = event.location
        created_by_us = True
        if not match_id:
            # Same shape of problem as an experience create with no id back,
            # and the same answer: do not try again automatically. Without the
            # id we cannot reference the venue, and a second attempt registers
            # a second one into a shared national table.
            raise EchoLuOrphanedCreate(
                f"echo.lu accepted a venue for {event.location!r} but returned "
                f"no id; it is now registered and untracked. Find it in the "
                f"organiser back office and record its id on an EchoVenue row "
                f"before syncing this event again."
            )

    EchoVenue.objects.update_or_create(
        key=key,
        defaults={
            "venue_id": match_id,
            "title": match_title,
            "created_by_us": created_by_us,
        },
    )
    logger.info(
        "echo.lu venue %s for %r (%s)",
        match_id,
        event.location,
        "registered" if created_by_us else "matched",
    )
    return [match_id]


def _iter_venue_records(client, address):
    """Every venue worth checking, cheapest first.

    The registry is thousands of rows and ListVenues offers no text search, so
    a commune filter goes first to put a likely match in the first page or two.
    The nationwide pass then follows unconditionally.

    "Unconditionally" is the point, and an earlier version got it wrong: it
    skipped the wide pass whenever the narrowed one returned *any* rows, which
    says nothing about whether the venue we want was among them. Our `canton`
    is free text while echo.lu's commune filter is a controlled value, so the
    narrow pass can easily return a handful of unrelated venues and hide a real
    match sitting in a different bucket — and the caller then registers a
    duplicate into a shared national dataset. Only the *caller* knows when it
    has found its venue, and it stops iterating the moment it does, so the wide
    pass costs nothing in the case that matters.

    Ids already yielded by the narrow pass are not yielded twice.
    """
    per_page = 200
    commune = (address or {}).get("commune") or ""
    passes = [{"communes[]": commune}] if commune else []
    passes.append({})

    seen_ids = set()
    for extra in passes:
        page = 0
        while True:
            params = dict(extra)
            params["pagination[page]"] = page
            params["pagination[perPage]"] = per_page
            records = response_records(client.list_venues(**params))
            if not records:
                break
            for record in records:
                identifier = extract_experience_id(record)
                if identifier and identifier in seen_ids:
                    continue
                if identifier:
                    seen_ids.add(identifier)
                yield record
            if len(records) < per_page:
                break
            page += 1
            if page > 200:  # pragma: no cover - runaway guard
                return


# Exactly what a create must carry, read off the API rather than the schema
# table: POSTing `{}` to /experiences answers 400 with one entry per missing
# field, and this is that list. Worth stating as a constant because the
# original mapping was reconstructed without API access and shipped SEVEN of
# these either empty or absent — echo.lu would have rejected every event.
#
# `tags` is NOT here. The published property table marks it required; the API
# does not ask for it. Empirical wins.
REQUIRED_EXPERIENCE_FIELDS = (
    "title",
    "description",
    "pictures",
    "dates",
    "venues",
    "categories",
    "audiences",
    "languages",
    "environments",
    "formats",
)


def missing_required_fields(payload):
    """Which required keys this payload would be rejected for.

    Lets the sync fail with an actionable local message instead of spending a
    round trip to be told the same thing, and gives `--dry-run` something real
    to report.
    """
    return [field for field in REQUIRED_EXPERIENCE_FIELDS if not payload.get(field)]


def build_experience_payload(event, venue_ids=None):
    """Map a MeetupEvent onto the echo.lu experience schema.

    ``duration`` is omitted from the date entry even though the schema accepts
    it: its unit is not documented, ``from``/``to`` already pin the span
    exactly, and a duration in the wrong unit would contradict them publicly.

    ``status`` is deliberately NOT built here. It is a create-time instruction
    ("save as draft" vs "submit for validation"), not content, so putting it in
    the payload would fold it into the fingerprint and send it on every PUT —
    and what a status field does to an already-validated experience is
    undocumented. :func:`_write_experience` adds it to the create call only.

    ``venue_ids`` are resolved by the caller (see :func:`resolve_venue_ids`),
    because resolving them costs API calls and this function must stay pure —
    the fingerprint is computed from what it returns.
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
        # Venue IDs from echo.lu's own registry, never names. `venues` is a
        # first-class resource there (5k+ rows shared across all organisers)
        # and ListExperience documents this parameter as "Venue IDs", so the
        # original `[event.location]` sent a display name into an id field.
        "venues": list(venue_ids or []),
        "location": {"address": parse_address(event.address, event.canton)},
        "tickets": _build_tickets(event),
    }

    tags = _setting_list("ECHO_LU_DEFAULT_TAGS")
    if tags:
        payload["tags"] = tags

    # All three of name/email/phone are required *within* contact, while
    # contact itself is optional — so a partial one is worse than none at all:
    # it turns an optional block into a guaranteed 400. All or nothing.
    contact = {
        "name": getattr(settings, "ECHO_LU_CONTACT_NAME", ""),
        "email": getattr(settings, "ECHO_LU_CONTACT_EMAIL", ""),
        "phone": getattr(settings, "ECHO_LU_CONTACT_PHONE", ""),
    }
    if all(contact.values()):
        company = getattr(settings, "ECHO_LU_CONTACT_COMPANY", "")
        # The organiser's site, which is what this field means alongside
        # name/email/phone/company. The event-specific link is already
        # `purchaseLink`.
        website = getattr(settings, "ECHO_LU_CONTACT_WEBSITE", "") or event_url
        if company:
            contact["company"] = company
        contact["website"] = website
        payload["contact"] = contact

    # Coordinates are strings in the echo.lu schema, and only sent when we have
    # them — "None" as a string would be worse than an absent key.
    if event.latitude is not None and event.longitude is not None:
        payload["location"]["address"]["latitude"] = str(event.latitude)
        payload["location"]["address"]["longitude"] = str(event.longitude)

    # `pictures` is required, so an event without an image cannot simply omit
    # it — that is a rejection, not a listing without a banner. The configured
    # fallback stands in; only if there is no fallback either does the payload
    # go out incomplete, and `missing_required_fields` names it before it costs
    # a round trip.
    picture_url = ""
    if event.image:
        try:
            picture_url = _absolute_media_url(event.image.url)
        except ValueError:
            # An ImageField whose storage cannot build a URL (a missing file in
            # local dev) falls through to the same fallback.
            picture_url = ""
    picture_url = picture_url or fallback_picture_url()
    if picture_url:
        payload["pictures"] = [{"url": picture_url, "copy": "Crush.lu", "alt": title}]

    # `languages` is required too, so an event with none configured falls back
    # rather than omitting the key.
    languages = [code for code in (event.languages or []) if code]
    payload["languages"] = languages or _setting_list("ECHO_LU_DEFAULT_LANGUAGES", "en")

    for facet, setting_name in (
        ("categories", "ECHO_LU_DEFAULT_CATEGORIES"),
        ("audiences", "ECHO_LU_DEFAULT_AUDIENCES"),
        ("formats", "ECHO_LU_DEFAULT_FORMATS"),
        ("environments", "ECHO_LU_DEFAULT_ENVIRONMENTS"),
    ):
        values = _setting_list(setting_name)
        if facet == "categories":
            values = _categories_for(event, values)
        # Set unconditionally: all four are required, so an empty list has to
        # travel and be reported as missing rather than silently vanish.
        payload[facet] = values

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
            elif not isinstance(mapped, (list, tuple)):
                # A scalar here ({"speed_dating": 123}) would reach list() and
                # raise TypeError — not an EchoLuError, so the save-signal task
                # would not swallow it and an ordinary event save would error
                # *after* its transaction committed. Bad config must never do
                # that; drop it and say so. `echo_taxonomy --check` reports it.
                logger.warning(
                    "ECHO_LU_CATEGORY_MAP[%r] is %s, expected a string or a "
                    "list of slugs — ignoring it",
                    event.event_type,
                    type(mapped).__name__,
                )
                mapped = []

    seen = []
    for value in list(defaults) + list(mapped):
        # Only strings. A list element that is an object or a number would go
        # out as a category — echo.lu rejects the whole experience on one bad
        # facet — and an unhashable one crashes the taxonomy check downstream.
        if not isinstance(value, str) or not value:
            continue
        if value not in seen:
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


def _proves_nothing_was_created(error):
    """True when `error` rules out echo.lu having created the experience.

    Any 4xx is a considered answer: echo.lu read the request and refused it,
    so nothing exists and retrying costs only the same refusal. That includes
    429 — the transport treats it as the one status proving the request was
    not processed, which is why a create may retry on it, and the same fact
    has to hold here or a rate-limited create would be parked as an orphan
    needing a manual audit that has nothing to find. A missing key never
    reached the network at all.

    Everything else — a 5xx, a timeout, a socket closed mid-response — leaves
    it genuinely unknown whether the listing was committed before the answer
    was lost, and unknown has to be treated as "it exists".

    :class:`EchoLuNotSent` is the strongest case of all: we declined to make
    the call. It needs naming explicitly because a locally-raised error has no
    status code, and "no status code" otherwise reads as "the answer was lost".
    """
    if isinstance(error, (EchoLuNotConfigured, EchoLuNotSent)):
        return True
    status = getattr(error, "status_code", None)
    return status is not None and 400 <= status < 500


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
        # `status` rides the create call, never the payload the fingerprint is
        # taken from. It is an instruction rather than content — "park this as
        # a draft" vs "submit it for validation" — so folding it in would make
        # every unchanged event look changed, and would send a status on every
        # PUT. The docs only say what a PUT status does to a *draft*; what it
        # does to an already-validated listing is unspecified, and a national
        # portal is the wrong place to find out.
        status = getattr(settings, "ECHO_LU_CREATE_STATUS", "pending")
        response = client.create_experience({**payload, "status": status})
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

    if sync is not None and sync.removal_requested and not force:
        # A take-down was asked for and echo.lu has not confirmed it. Until it
        # does, this event has one job — come down — regardless of what its own
        # fields say. `force` is the deliberate override: it is what the admin
        # sync action and --force use to put a removed listing back.
        if not sync.experience_id:
            return "suppressed"
        return withdraw_event(event, client=client, dry_run=dry_run, explicit=True)

    if not should_publish(event):
        # Nothing was ever published, so there is nothing to take down.
        if sync is None or not sync.experience_id:
            return "skipped"
        # Already down, so ordinarily nothing to do — with one exception. A
        # WITHDRAWN row whose event is now public, upcoming and *cancelled*
        # falls through to the take-down below, which sends the cancellation
        # notice it is owed. That combination can only be reached by coming
        # back: the listing was pulled while the event was unpublished or
        # private, and the event has since been republished with the
        # cancellation still on it. Nothing else would ever send that notice —
        # `publishable` drops the event for being cancelled and `withdrawable`
        # drops it for being withdrawn, so this is the only path back. See the
        # matching term in `events_needing_sync`.
        if sync.status == EchoExperienceSync.Status.WITHDRAWN and not (
            _cancellation_notice_wanted(event)
        ):
            return "skipped"
        if sync.status == EchoExperienceSync.Status.SUPPRESSED:
            # Already down, and the state has to survive: withdrawing again
            # would rewrite it to WITHDRAWN, and the sweep re-publishes one of
            # those as soon as the event qualifies again — quietly undoing a
            # removal somebody asked for, one eligibility change later.
            return "suppressed"
        if sync.status == EchoExperienceSync.Status.CANCELLED and (
            _cancellation_notice_wanted(event)
        ):
            # Cancelled and the notice is still wanted, so there is nothing
            # left to do. Anything that would have ended an ordinary listing —
            # privacy tightening, or just the event's end time passing — ends
            # the notice too, and falls through to the take-down below.
            return "skipped"
        return withdraw_event(event, client=client, dry_run=dry_run)

    # Cached venue ids only out here — resolving for real can register a venue
    # on echo.lu, and nothing before the lock is allowed to write anything. In
    # the steady state the cache is warm and this fingerprint matches the one
    # computed under the lock, so an unchanged event still costs no API call.
    payload = build_experience_payload(event, venue_ids=cached_venue_ids(event))
    fingerprint = payload_fingerprint(payload)

    def _no_op_outcome(row):
        """The outcome for a row that needs no API call, or None if it does.

        Shared by the dry run and the real one on purpose. A preview that
        answered "would update" for every already-synced event would be
        describing a sweep nobody runs — the real one makes no call at all.
        """
        if row is None or not row.experience_id or force:
            return None
        if row.status == EchoExperienceSync.Status.SUPPRESSED:
            # Taken down by hand while still eligible. The event's own fields
            # say "publish me", so without this the next pass would put it
            # straight back and the removal would look like it never happened.
            return "suppressed"
        if (
            row.status == EchoExperienceSync.Status.SYNCED
            and row.payload_hash == fingerprint
        ):
            return "unchanged"
        return None

    if dry_run:
        # No get_or_create here: a preview must not leave rows behind.
        return _no_op_outcome(sync) or (
            "created" if sync is None or not sync.experience_id else "updated"
        )

    if sync is None:
        sync, _created = EchoExperienceSync.objects.get_or_create(event=event)

    settled = _no_op_outcome(sync)
    if settled:
        return settled

    client = client or EchoLuClient()
    failure = None
    # Every write to a listing is serialised on its own row, create and update
    # alike. The create has to be, because echo.lu's payload has no external
    # reference and a second POST means a duplicate listing whose id we never
    # learn — and three entry points (the save-signal task, the hourly sweep,
    # the admin action) can land on one event at once.
    #
    # An update looked exempt, since two PUTs settle on the same listing. They
    # do; the problem is not another PUT but a concurrent *lifecycle* change.
    # An update that passed the suppression check, then overtook a coach's
    # take-down, would republish the listing and write SYNCED over the
    # SUPPRESSED the take-down had just recorded — the removal undone, and no
    # state left saying it was ever asked for.
    #
    # So the lock is held across the HTTP call, and the state is both re-read
    # and written back inside it. It blocks only other writers of the same
    # event, and the client's timeout bounds how long.
    with transaction.atomic():
        sync = EchoExperienceSync.objects.select_for_update().get(pk=sync.pk)
        # The *event* is re-read too, not just its sync row. Eligibility, the
        # payload and the fingerprint were all decided before the wait on this
        # lock, and the writer we were queued behind may well have been a save
        # that made the event private and took the listing down. Resuming on
        # that stale read would PUT the old public payload back onto a national
        # portal — the one outcome this module exists to prevent.
        event.refresh_from_db()
        # Re-point the caller's event at the row we actually wrote. The locked
        # read replaced the instance `event.echo_sync` still holds, and that
        # stale copy may have no experience_id — so a caller that syncs and
        # then withdraws in the same breath would look at it, conclude nothing
        # was ever published, and skip.
        event.echo_sync = sync

        # Decided again under the lock: whoever we were queued behind may have
        # suppressed the listing, orphaned it, or synced this very payload,
        # while we waited on the read above. The orphan check especially — a
        # caller queued behind a create that came back ambiguous would
        # otherwise take the empty experience_id at face value and POST again,
        # which is the duplicate this whole lock exists to prevent.
        if sync.status == EchoExperienceSync.Status.ORPHANED:
            return "blocked"
        if not should_publish(event) or (sync.removal_requested and not force):
            # No longer ours to publish. Whoever changed it ran their own sync
            # for the new state, and the next sweep reconciles anything they
            # missed; what must not happen is this call publishing over it.
            return "skipped"

        try:
            # Recomputed from the event as it is now, for the same reason.
            # Venue resolution lives in here too: it can register a venue on
            # echo.lu, so it belongs inside the lock with every other write —
            # and inside this try, so a registry we cannot read is recorded on
            # the row like any other failure instead of escaping unrecorded.
            address = parse_address(event.address, event.canton)
            venue_ids = resolve_venue_ids(event, client, address=address)
            payload = build_experience_payload(event, venue_ids=venue_ids)
            fingerprint = payload_fingerprint(payload)

            settled = _no_op_outcome(sync)
            if settled:
                return settled

            # Checked before spending a round trip. echo.lu rejects the whole
            # experience if any of these is missing, and answers with a field
            # list that only ever reaches the logs — whereas this names the
            # setting to fix and lands on the sync row the admin renders.
            missing = missing_required_fields(payload)
            if missing:
                raise EchoLuNotSent(
                    "not sent — echo.lu requires "
                    + ", ".join(missing)
                    + ". Set the matching ECHO_LU_DEFAULT_* values (run "
                    "`manage.py echo_taxonomy` for the accepted ids). A "
                    "missing picture means SOCIAL_PREVIEW_IMAGE_URL resolved "
                    "empty — that is the one to set, or ECHO_LU_FALLBACK_IMAGE "
                    "to override it just for echo.lu."
                )

            outcome = _write_experience(sync, payload, fingerprint, client)
        except EchoLuOrphanedCreate as exc:
            # Recorded here, inside the lock, and deliberately NOT re-raised
            # here: raising would roll the block back and discard the very row
            # that says not to try again. Leaving the block normally commits
            # the state before the lock drops, so a caller waiting on it wakes
            # to a row that already reads ORPHANED. The error is re-raised
            # below, once that is durable.
            sync.mark_orphaned(exc)
            failure = exc
        except EchoLuError as exc:
            # A failed create needs the same question asked of it that the
            # transport asks before replaying one: does this error prove
            # nothing was created? Not retrying inside the call is only half
            # the fix — the sweep would come back an hour later, find no id,
            # and POST again. Only a rejection is safe to leave retryable;
            # anything ambiguous parks the row where no further create can
            # happen.
            if not sync.experience_id and not _proves_nothing_was_created(exc):
                sync.mark_orphaned(
                    f"{exc} — the create may have been committed before this "
                    f"failed, so no further create will be attempted. Run "
                    f"`sync_events_to_echo --audit` to see whether a listing "
                    f"exists, then adopt or delete it."
                )
            else:
                sync.mark_failure(exc)
            failure = exc

    if failure is not None:
        raise failure

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
    if dry_run:
        return "withdrawn" if sync is not None and sync.experience_id else "skipped"

    if explicit and not is_sync_enabled():
        # Checked before the intent is recorded so a disabled environment
        # cannot leave a removal pending that nothing will ever act on.
        return "disabled"

    if explicit:
        # Recorded even when there is nothing to take down yet. An event whose
        # first sync is still pending, or whose last create was refused, has no
        # experience_id — but the coach has said it must not be on echo.lu, and
        # without a row saying so the next sync would cheerfully create the
        # listing they just removed. `sync_event` honours this ahead of the
        # create path.
        if sync is None:
            sync, _created = EchoExperienceSync.objects.get_or_create(event=event)
            event.echo_sync = sync
        if not sync.removal_requested:
            sync.removal_requested = True
            sync.save(update_fields=["removal_requested", "updated_at"])

    if sync is None or not sync.experience_id:
        return "skipped"
    if not is_sync_enabled():
        return "disabled"

    if explicit and not sync.removal_requested:
        # Written before the call, not after it. The event's own fields still
        # say "publish me", so a take-down that fails and records only FAILED
        # is indistinguishable from a failed *publish* — and the next sweep
        # would answer it with a PUT, republishing the listing somebody just
        # asked to remove. The flag is what survives the failure and keeps
        # reconciliation pointed at the take-down.
        sync.removal_requested = True
        sync.save(update_fields=["removal_requested", "updated_at"])

    client = client or EchoLuClient()
    failure = None
    # Same row lock as the publish path, for the same reason in reverse: a
    # take-down that overtakes a concurrent update would be undone by that
    # update's mark_success writing SYNCED over the withdrawal. The failure is
    # recorded inside the lock too — released early, it would land on top of
    # whatever the next writer had already decided.
    with transaction.atomic():
        sync = EchoExperienceSync.objects.select_for_update().get(pk=sync.pk)
        # And the event is re-read, exactly as on the publish path. Which
        # action this sends is decided from the event's own fields, so
        # deciding it before the wait means deciding it from state somebody
        # else has since replaced. The case that matters: an automatic
        # cancellation queues behind a privacy change, that change unpublishes
        # the listing, and this caller then wakes and sends `cancel` — putting
        # the title, venue and date of a now-private event back on public
        # display, and recording CANCELLED as though that were intended.
        event.refresh_from_db()
        event.echo_sync = sync

        # The re-read can also say the take-down is not wanted at all any
        # more. An automatic withdrawal decided from an older save can queue
        # behind a sync that republished the listing and left it correct;
        # going ahead unpublishes what that newer save just put up and writes
        # WITHDRAWN over its SYNCED, so the listing stays missing until the
        # next hourly sweep notices. This is the mirror of the publish path's
        # own "no longer ours to publish" check, which this side lacked.
        #
        # Only automatic take-downs abort. An `explicit` one is a person
        # removing a listing whose event still qualifies — that is precisely
        # what it is for — and `removal_requested` is that same instruction
        # surviving a failed attempt, so neither may be talked out of it by
        # eligibility coming back.
        if not explicit and not sync.removal_requested and should_publish(event):
            return "skipped"

        # `explicit` overrides the cancellation notice as well. Somebody asking
        # to remove a listing means remove it — leaving a public notice up
        # because the event also happens to be cancelled ignores the
        # instruction, and the SUPPRESSED state recorded below then stops the
        # sweep ever correcting it.
        #
        # Not just "still public" either: a notice for an event that has
        # already happened is as stale as any finished listing, and
        # re-cancelling it does nothing. One predicate decides both whether a
        # notice is still wanted and whether to leave one.
        notice_wanted = _cancellation_notice_wanted(event)
        try:
            if event.is_cancelled and notice_wanted and not explicit:
                client.cancel_experience(sync.experience_id)
                sync.mark_cancelled()
            else:
                client.unpublish_experience(sync.experience_id)
                sync.mark_withdrawn(explicit=explicit)
        except EchoLuError as exc:
            # Not re-raised here — see sync_event. `removal_requested` was
            # committed before the call, so it survives regardless and the
            # sweep keeps retrying the removal.
            sync.mark_failure(exc)
            failure = exc

    if failure is not None:
        raise failure
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
    failure = None
    # The same row lock the publish and take-down paths take, for the same
    # reason: this is a third writer to the listing, and unserialised it can
    # interleave badly in both directions — a concurrent PUT finishing after
    # this DELETE re-creates the listing while we clear the id, or a
    # mark_success landing after it writes SYNCED over a listing that is gone.
    # Either way the row stops describing echo.lu.
    with transaction.atomic():
        sync = EchoExperienceSync.objects.select_for_update().get(pk=sync.pk)
        event.echo_sync = sync
        if not sync.experience_id:
            # Somebody else got here first while we waited.
            return "skipped"
        try:
            client.delete_experience(sync.experience_id)
        except EchoLuError as exc:
            # Recorded inside the lock and re-raised after, as elsewhere.
            sync.mark_failure(exc)
            failure = exc
        else:
            sync.experience_id = ""
            sync.payload_hash = ""
            sync.status = EchoExperienceSync.Status.PENDING
            sync.removal_requested = False
            sync.last_error = ""
            sync.last_attempted_at = timezone.now()
            sync.save(
                update_fields=[
                    "experience_id",
                    "payload_hash",
                    "status",
                    "removal_requested",
                    "last_error",
                    "last_attempted_at",
                    "updated_at",
                ]
            )

    if failure is not None:
        raise failure
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
        # Taken down by hand while the event still qualified. Everything else
        # in this filter keys off the event, which still says "publish me", so
        # only the sync row can rule it out.
        echo_sync__status=EchoExperienceSync.Status.SUPPRESSED
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
    # Orphans, on their own terms. Both filters above ask the event whether it
    # deserves attention, and an orphan fails whichever one you check: it has
    # no experience_id, so `withdrawable` drops it, and its event eventually
    # stops being publishable — if not by being unpublished or cancelled, then
    # simply by happening, since the date falls behind the cutoff. Every orphan
    # therefore ages out of an eligibility-gated queryset, and the sweep goes
    # quiet over a live listing nobody can reach.
    #
    # So this row is selected because of what the *row* says, not the event.
    # No write can result: `sync_event` checks ORPHANED before it looks at
    # eligibility and answers "blocked", which is what keeps the hourly job
    # red until a person resolves it.
    orphaned = queryset.filter(echo_sync__status=EchoExperienceSync.Status.ORPHANED)
    # A withdrawn listing owed a cancellation notice. The event was pulled
    # while unpublished or private, then republished with the cancellation
    # still on it — so it is public, upcoming and cancelled, which `publishable`
    # rejects for being cancelled and `withdrawable` rejects for being
    # withdrawn. Without a term of its own the transition reaches no sweep at
    # all and the notice is never sent. Coarse `date_time` pre-filter as above;
    # `sync_event` applies the precise `end_time` test.
    notice_pending = queryset.filter(
        echo_sync__status=EchoExperienceSync.Status.WITHDRAWN,
        is_published=True,
        is_cancelled=True,
        is_private_invitation=False,
        date_time__gte=MeetupEvent.live_lookback_cutoff(now),
    ).exclude(echo_sync__experience_id="")
    # Same reasoning again: a take-down that failed leaves an event whose own
    # fields still say "publish me", so no eligibility-based filter would pick
    # it up to retry the removal — it would pick it up to republish.
    removal_pending = queryset.filter(echo_sync__removal_requested=True)
    return (
        (publishable | withdrawable | orphaned | removal_pending | notice_pending)
        .distinct()
        .select_related("echo_sync")
        # Least-recently-attempted first, nulls (never attempted) ahead of
        # everything. MeetupEvent's own ordering is by date, which a *bounded*
        # sweep turns into starvation: the same early events consume the budget
        # every hour and the tail is deferred forever — including take-downs of
        # events that have gone private, which would stay public indefinitely.
        .order_by(models.F("echo_sync__last_attempted_at").asc(nulls_first=True))
    )
