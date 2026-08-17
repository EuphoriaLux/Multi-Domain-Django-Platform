"""Guest ordering flow + staff KDS — the live-clickable pilot slice.

Deliberately trimmed for a fast, reliable demo rather than the full spec:

- **No HTMX.** `power_up`'s existing HTMX tag (`crm/base_crm.html`) carries
  no CSP nonce today, and this repo's CSP is enforce-capable (Django 6's
  native middleware is in MIDDLEWARE — see `azureproject/settings.py`)
  even though only report-only policies are populated right now. Rather
  than depend on that staying true, every view here is a plain form POST +
  redirect. The guest status page and staff KDS use a `<meta refresh>`
  instead of HTMX polling for "live" updates. Swap to HTMX once a nonce
  convention exists for `power_up`.
- **Chronicle is a process-global dict below.** This is the exact
  production gap flagged in review: it will not survive multiple WSGI
  workers. Fine for `runserver`, wrong for App Service — a DB- or
  cache-backed chronicle is a prerequisite for shipping this for real.
- **No 2-hour window, no Settlement.** See `models.py`'s module docstring.
"""

from __future__ import annotations

import hashlib
import os
import threading
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from enum import Enum

from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import permission_required
from django.core import signing
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from .lore.chronicle import Chronicle, ChronicleEvent, DrinkLine
from .lore.engine import generate_vignette
from .lore.personas import NOIR_PERSONAS, random_persona
from .lore.providers import GeminiProvider, OpenAIProvider
from .lore.safety import PersonaRejected, sanitize_persona
from .models import Guest, MenuItem, Order, OrderItem, Tab, Table, Venue
from .printing.art import select_ascii_art, select_mission
from .printing.escpos import render_plain_text
from .printing.layout import Paper, TicketData, TicketLine, render_ticket

GUEST_COOKIE = "atmos_guest"
GUEST_COOKIE_MAX_AGE = 60 * 60 * 6  # 6h, spec §7.1
CART_SESSION_KEY = "atmos_cart"
TAB_SESSION_KEY = "atmos_tab_id"
MAX_ITEM_QTY = 9  # matches the menu form's advertised max — enforced here too

# sanitize_persona()'s own default cap (28) is looser than this field, so a
# 21-28 char typed name would pass sanitization and then fail the DB insert
# (a data-truncation error on Postgres). Read from the field instead of
# hand-copying "20" so the two can't drift out of sync again.
_DISPLAY_NAME_MAX_LENGTH = Guest._meta.get_field("display_name").max_length

# Per spec §7 (docs/specs/atmos-bar-ordering.md): the guest join page must
# be rate-limited per-IP and per-tab. Before this, a single photographed QR
# was enough to obtain the scan handoff once and then hit guest_join()
# indefinitely — each hit runs a fresh active-alias query and full template
# render (a new persona reroll), with nothing else in this app throttling
# unauthenticated traffic. 20 requests/minute comfortably covers a real
# guest mashing "reroll" a few times while still bounding sustained abuse.
_JOIN_RATE_LIMIT = 20
_JOIN_RATE_WINDOW_SECONDS = 60


def _client_ip(request) -> str:
    """Best-effort client IP for rate-limit bucketing only — unlike
    `azureproject.adapters`' allauth adapter (which raises PermissionDenied
    on anything unparseable, appropriate for its auth context), a
    malformed/missing value here just degrades to a shared "unknown" bucket
    rather than blocking a legitimate request over an IP-parsing edge case.
    """
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        # Azure App Service includes the port ("1.2.3.4:5678") — same format
        # azureproject.adapters.get_client_ip() strips; a stray port would
        # otherwise fragment one visitor's requests across many cache keys.
        ip = forwarded.split(",")[0].strip()
        if ip.count(":") == 1:
            ip = ip.split(":")[0]
        return ip or "unknown"
    return request.META.get("REMOTE_ADDR") or "unknown"


def _join_rate_limited(request, tab_id) -> bool:
    """Fixed-window counter keyed on (client IP, tab) — both dimensions the
    spec calls for. `cache` is Django's default cache (Redis in production
    per `azureproject/settings.py`, LocMemCache otherwise), so this works
    without any Atmos-specific configuration."""
    key = f"atmos:join_rl:{_client_ip(request)}:{tab_id}"
    try:
        count = cache.incr(key)
    except ValueError:
        # incr() raises when the key doesn't exist yet (first hit this
        # window) — set it directly instead of racing a separate has-key
        # check against a concurrent request doing the same thing.
        cache.set(key, 1, timeout=_JOIN_RATE_WINDOW_SECONDS)
        count = 1
    return count > _JOIN_RATE_LIMIT


class PlacementOutcome(Enum):
    """Every way `_create_order_atomic()` can end. Replaces an earlier
    version that returned six different bare-string/tuple/object shapes
    accreted one at a time across six rounds of review fixes — callers had
    to type/arity-sniff each result (`result == "closed"`,
    `result[0] == "unavailable"`, a bare 3-tuple unpack for success), which
    made adding a *seventh* outcome error-prone. `order_place()` below
    switches on `.outcome` instead."""

    EMPTY_CART = "empty_cart"
    VENUE_CLOSED = "venue_closed"
    TAB_CLOSED = "tab_closed"
    GUEST_INACTIVE = "guest_inactive"
    UNAVAILABLE = "unavailable"
    PRICE_CHANGED = "price_changed"
    PLACED = "placed"


@dataclass
class PlacementResult:
    """What `_create_order_atomic()` returns. Only the fields relevant to
    `.outcome` are populated — e.g. `order`/`venue`/`lines` only for
    `PLACED`, `unavailable_names` only for `UNAVAILABLE`, `expected_total`/
    `actual_total` only for `PRICE_CHANGED`."""

    outcome: PlacementOutcome
    order: Order | None = None
    venue: Venue | None = None
    lines: list[tuple[MenuItem, int]] = field(default_factory=list)
    unavailable_names: list[str] = field(default_factory=list)
    expected_total: Decimal | None = None
    actual_total: Decimal | None = None


# Dev-only in-process chronicle store — see module docstring.
_CHRONICLES: dict[str, Chronicle] = {}
# Guards the check-then-act below: on a threaded server (runserver), two
# near-simultaneous first-orders-of-the-night for the same venue could
# otherwise both see no existing entry, both build a fresh Chronicle, and
# the second assignment silently discard the first's ChronicleEvent.
_CHRONICLES_LOCK = threading.Lock()


def _chronicle_for(venue: Venue) -> Chronicle:
    # Keyed by (venue, local date), not just venue: a worker that survives
    # past midnight would otherwise hand the first orders of a new service
    # night a chronicle still full of last night's personas and events.
    key = f"{venue.id}:{timezone.localdate().isoformat()}"
    with _CHRONICLES_LOCK:
        chronicle = _CHRONICLES.get(key)
        if chronicle is None:
            # Drop this venue's stale keys from previous days — without
            # this the dict grows by one entry per (venue, day) forever,
            # for the life of the process.
            for stale_key in [
                k for k in _CHRONICLES if k.startswith(f"{venue.id}:") and k != key
            ]:
                del _CHRONICLES[stale_key]
            chronicle = Chronicle(venue.name, max_events=12)
            _CHRONICLES[key] = chronicle
        return chronicle


def _provider():
    """Optional real model, purely from the environment. None -> deterministic
    fallback (see lore.engine) — the default, and what the preview always uses.

    ATMOS_OPENAI_BASE_URL / ATMOS_OPENAI_MODEL let the OpenAI path point at
    any OpenAI-compatible endpoint, not just api.openai.com. (GitHub Models
    was the obvious free option here — it was retired 2026-07-30, so this is
    now only useful for a real OpenAI key or another compatible provider.)

    ATMOS_GEMINI_MODEL overrides the Gemini model — pass an explicit "-latest"
    alias here rather than relying on the class default staying valid; Google
    retires dated Gemini model IDs from under callers without notice.
    """
    key = os.environ.get("ATMOS_OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if key:
        kwargs = {}
        base_url = os.environ.get("ATMOS_OPENAI_BASE_URL")
        if base_url:
            kwargs["base_url"] = base_url
        model = os.environ.get("ATMOS_OPENAI_MODEL")
        if model:
            kwargs["model"] = model
        return OpenAIProvider(key, **kwargs)
    key = os.environ.get("ATMOS_GEMINI_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if key:
        kwargs = {}
        model = os.environ.get("ATMOS_GEMINI_MODEL")
        if model:
            kwargs["model"] = model
        return GeminiProvider(key, **kwargs)
    return None


def _decode_guest_cookie(request):
    """Shared cookie-decoding step for both guest resolvers below. Returns
    the signed guest id, or None if there's no cookie or it's invalid."""
    raw = request.COOKIES.get(GUEST_COOKIE)
    if not raw:
        return None
    try:
        return signing.loads(raw, max_age=GUEST_COOKIE_MAX_AGE)
    except signing.BadSignature:
        return None


def _get_guest(request):
    """Resolve the Guest from the signed cookie. Spec §8.1: object access
    always goes *through* the guest, never a bare pk lookup.

    Requires the guest still `active` and its tab still `open` — correct
    for every view that can *create new state* (browsing, cart, placing an
    order): once staff close a tab or settle a guest, no new activity
    should be possible under that identity. `order_status()` below needs a
    different, more permissive resolver — see `_get_guest_for_viewing()`.
    """
    guest_id = _decode_guest_cookie(request)
    if guest_id is None:
        return None
    return (
        Guest.objects.select_related("tab__table", "venue")
        # `tab__table__is_active`: staff taking a table out of service after
        # a guest already scanned used to only block *future* scans — the
        # existing cookie kept resolving here regardless, so the guest could
        # keep browsing/ordering from a table staff explicitly deactivated.
        .filter(
            pk=guest_id,
            status="active",
            tab__status="open",
            tab__table__is_active=True,
        ).first()
    )


def _get_guest_for_viewing(request):
    """Resolve the Guest from the signed cookie, WITHOUT requiring the
    guest still be `active` or its tab still `open` — used only by
    `order_status()`.

    `_get_guest()`'s stricter filter is correct for anything that creates
    new state, but applying it here as well meant a guest lost access to
    their OWN already-placed, still-in-progress order the instant staff
    closed the tab (Tab.save() settles guests, but never touches
    Order.status — the bar kept fulfilling the order while the guest could
    no longer see it). Order *ownership* is still enforced at the call
    site via `guest.orders.filter(pk=pk)`, so this doesn't relax any real
    authorization boundary — a cookie only ever resolves to its own guest.
    """
    guest_id = _decode_guest_cookie(request)
    if guest_id is None:
        return None
    return (
        Guest.objects.select_related("tab__table", "venue").filter(pk=guest_id).first()
    )


def _guest_can_still_scan(guest) -> bool:
    """Whether `order_status()` should embed the table's live scan QR on
    this guest's ticket. `_get_guest_for_viewing()` deliberately resolves a
    removed/settled guest too (so they can still see their OWN historical
    order), but that same permissiveness would otherwise hand them the
    table's CURRENT scan token on every reload of an old ticket — if staff
    removed them and rotated the QR specifically to cut them off, they
    could read the newly rotated URL straight off their old ticket and
    immediately mint a fresh identity, defeating the rotation entirely.
    Requires `guest`'s `tab__table` to already be select_related.
    """
    return (
        guest.status == "active"
        and guest.tab.status == "open"
        and guest.tab.table.is_active
    )


def _active_aliases(venue: Venue):
    return list(
        Guest.objects.filter(venue=venue, status="active").values_list(
            "alias", flat=True
        )
    )


def _clamp_qty(raw, default=1) -> int:
    try:
        qty = int(raw)
    except (TypeError, ValueError):
        qty = default
    return max(1, min(MAX_ITEM_QTY, qty))


def _resolve_menu_item(guest, item_id):
    """Look up a MenuItem scoped to the guest's venue, the same way
    `_create_order_atomic`'s `available` query does. Returns None for a
    missing, unavailable, wrong-venue, *or malformed* id — a UUIDField
    lookup raises ValidationError (not DoesNotExist) on a non-UUID string,
    which `get_object_or_404` does not catch, so callers must go through
    here rather than filtering on a raw POSTed id directly.
    """
    try:
        return MenuItem.objects.get(
            pk=item_id,
            category__venue=guest.venue,
            category__is_visible=True,
            is_available=True,
        )
    except (MenuItem.DoesNotExist, ValidationError, ValueError):
        return None


def _order_signature(pairs) -> str:
    """Content fingerprint of a cart's orderable `(item, quantity)` pairs —
    item identity, quantity, price, name, AND allergens, not just the summed
    total. `expected_total` alone catches a price change but not a
    same-priced rename: if staff repurpose what "Old Fashioned" points to
    without touching its price, the numeric total still matches what the
    guest reviewed, but they never confirmed the new item — it would
    otherwise reach the KDS and ticket as something else entirely.
    `allergens` is included for the same reason: cart.html displays it
    prominently next to each line, so a staff edit that adds/removes an
    allergen between review and submission is exactly the kind of
    "guest never actually saw this" change this signature exists to catch —
    not just renames. Order-independent (sorted) so cart line ordering
    doesn't matter.
    """
    parts = sorted(
        f"{item.id}:{qty}:{item.price}:{item.name}:{item.allergens}"
        for item, qty in pairs
    )
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def _cart_signature(lines) -> str:
    """`_order_signature()` over `_cart_lines()`-shaped dicts, restricted to
    the orderable ones — the one line every call site (cart_detail and both
    of order_place's cart.html re-renders) needs, kept in one place so they
    can't drift out of sync with each other.
    """
    return _order_signature(
        (line["item"], line["quantity"]) for line in lines if line["is_orderable"]
    )


# ---------------------------------------------------------------- guest zone


def guest_scan(request, qr_token):
    table = get_object_or_404(
        Table.objects.select_related("venue"), qr_token=qr_token, is_active=True
    )

    if not table.venue.service_open:
        return render(request, "atmos/closed.html", {"table": table})

    guest = _get_guest(request)
    if guest and guest.tab.table_id == table.id:
        return redirect("atmos:guest_menu")

    tab, _created = Tab.objects.get_or_create(
        table=table, status="open", defaults={"venue": table.venue}
    )
    request.session[TAB_SESSION_KEY] = str(tab.id)
    return redirect("atmos:guest_join")


def guest_join(request):
    # TAB_SESSION_KEY is a one-time handoff from guest_scan(), consumed
    # below the moment it successfully produces a guest. Without that, a
    # guest staff later settles/removes could revisit this page and mint a
    # fresh active identity on the same (still open) tab without ever
    # rescanning — silently bypassing a QR token staff rotated specifically
    # to cut off exactly that browser.
    tab_id = request.session.get(TAB_SESSION_KEY)
    tab = None
    if tab_id:
        # `table__is_active=True` here too, matching _get_guest() and the
        # POST-time select_for_update() lock below: Table.save() never
        # touches Tab.status, so a table deactivated after the scan would
        # otherwise still resolve here and render the join form for a table
        # staff just took out of service. The POST is already safely
        # rejected either way (the locked recheck below), so this is a UX
        # fix, not a data-integrity one — but worth being consistent.
        tab = (
            Tab.objects.select_related("venue", "table")
            .filter(pk=tab_id, status="open", table__is_active=True)
            .first()
        )

    # A revisit (direct URL, browser back, or a double-submit) must not mint
    # a second Guest for the SAME tab — that would replace the cookie, clear
    # the in-progress cart, and orphan the first identity's order-status
    # pages. Scoped to `tab` (or its absence), not any active guest cookie —
    # scanning a DIFFERENT table's QR while already active at another table
    # is a supported flow (see the cart-reset comment below) and must fall
    # through to mint a new guest there, not bounce back to the old table's
    # menu. When the handoff has already expired/been consumed (`tab` is
    # None) an existing active guest still has nowhere else to go but their
    # own menu. Same pattern guest_scan() uses for the same-tab case.
    existing_guest = _get_guest(request)
    if existing_guest and (tab is None or existing_guest.tab_id == tab.id):
        return redirect("atmos:guest_menu")

    if not tab:
        return render(request, "atmos/no_session.html")

    if _join_rate_limited(request, tab.id):
        return HttpResponse(
            "Too many requests — please wait a moment and try again.",
            status=429,
        )

    error = ""
    if request.method == "POST":
        active = _active_aliases(tab.venue)
        typed = request.POST.get("display_name", "").strip()
        rolled = request.POST.get("rolled_alias", "").strip()

        display_name = ""
        if typed:
            try:
                display_name = sanitize_persona(
                    typed, max_length=_DISPLAY_NAME_MAX_LENGTH
                )
            except PersonaRejected:
                error = "Let's try a noir one instead."

        if not error:
            # `rolled` arrives via a hidden form field — guest-controlled.
            # Only accept it if it's actually a catalog persona; anything
            # else (edited, injected, or just stale after a collision)
            # gets a fresh server-side roll instead of being trusted as-is.
            # This is the same moderation boundary sanitize_persona()
            # enforces for typed names — the rolled path was accidentally
            # exempt from it.
            alias = (
                rolled
                if rolled in NOIR_PERSONAS and rolled not in active
                else random_persona(exclude=active)
            )
            if alias in active:  # collision (e.g. a double submit) — reroll silently
                alias = random_persona(exclude=active)

            # `active` above is a snapshot read once at the top of this POST
            # — two concurrent joins can both pass the in-memory checks
            # above with the same alias. `uniq_active_alias_per_venue` is
            # the real backstop for that, and violating it raises
            # IntegrityError, not something `.create()` callers normally
            # expect. Retry a bounded number of times against a *fresh* DB
            # read instead of letting a genuine collision 500. Each attempt
            # gets its own savepoint so a failed one can't poison a wider
            # transaction if this view is ever called from inside one.
            guest = None
            tab_closed_mid_join = False
            for _attempt in range(3):
                try:
                    with transaction.atomic():
                        # The scan handoff can go stale between guest_scan()
                        # setting it and this POST landing — staff can close
                        # the tab, or deactivate its table, in between. Lock
                        # and re-check both in the SAME transaction as the
                        # create: a losing race here would otherwise create
                        # an active guest that _get_guest() can never
                        # resolve again (it requires tab__status="open" AND
                        # tab__table__is_active=True), leaving the guest
                        # stuck on an unusable cookie while its alias stays
                        # reserved indefinitely.
                        locked_tab = (
                            Tab.objects.select_for_update()
                            .filter(pk=tab.pk, status="open", table__is_active=True)
                            .first()
                        )
                        if locked_tab is None:
                            tab_closed_mid_join = True
                            break
                        guest = Guest.objects.create(
                            tab=locked_tab,
                            venue=locked_tab.venue,
                            alias=alias,
                            display_name=display_name,
                        )
                    break
                except IntegrityError:
                    alias = random_persona(exclude=_active_aliases(tab.venue))

            if tab_closed_mid_join:
                return render(request, "atmos/no_session.html")

            if guest is None:
                error = "This table's crowded with ghosts — try again."
            else:
                # Switching identity (new table scan, new guest) must not
                # carry the previous guest's cart forward — item IDs are
                # shared across every venue on the platform, so a stale cart
                # could otherwise be ordered onto the new table under the
                # new persona. Deliberately deferred to HERE (only once
                # guest creation actually succeeded) rather than before the
                # retry loop above: an existing guest with an in-progress
                # round at table A scanning table B, where B turns out
                # closed/deactivated or every alias retry collides, must not
                # lose that unrelated round just because a table switch was
                # attempted and failed.
                request.session[CART_SESSION_KEY] = {}
                # Consume the handoff — see the note where it's read above.
                request.session.pop(TAB_SESSION_KEY, None)
                response = redirect("atmos:guest_menu")
                response.set_cookie(
                    GUEST_COOKIE,
                    signing.dumps(str(guest.id)),
                    max_age=GUEST_COOKIE_MAX_AGE,
                    httponly=True,
                    samesite="Lax",
                    secure=not settings.DEBUG,
                )
                return response

    rolled_alias = random_persona(exclude=_active_aliases(tab.venue))
    return render(
        request,
        "atmos/join.html",
        {"tab": tab, "rolled_alias": rolled_alias, "error": error},
    )


def guest_menu(request):
    guest = _get_guest(request)
    if not guest:
        return render(request, "atmos/no_session.html")
    categories = guest.venue.menu_categories.filter(is_visible=True).prefetch_related(
        "items"
    )
    cart = request.session.get(CART_SESSION_KEY, {})
    return render(
        request,
        "atmos/menu.html",
        {"guest": guest, "categories": categories, "cart_count": sum(cart.values())},
    )


def cart_add(request):
    guest = _get_guest(request)
    if not guest or request.method != "POST":
        return render(request, "atmos/no_session.html")
    item = _resolve_menu_item(guest, request.POST.get("item_id"))
    if item is None:
        raise Http404("Menu item not found.")
    qty = _clamp_qty(request.POST.get("quantity", 1))
    cart = request.session.get(CART_SESSION_KEY, {})
    # Clamp the running total too, not just this add — repeated adds must
    # not be able to stack past the advertised max.
    cart[str(item.id)] = min(MAX_ITEM_QTY, cart.get(str(item.id), 0) + qty)
    request.session[CART_SESSION_KEY] = cart
    return redirect("atmos:guest_menu")


def cart_update(request):
    guest = _get_guest(request)
    if not guest or request.method != "POST":
        return render(request, "atmos/no_session.html")
    item_id = request.POST.get("item_id", "")
    try:
        qty = int(request.POST.get("quantity", 0))
    except (TypeError, ValueError):
        qty = 0
    cart = request.session.get(CART_SESSION_KEY, {})
    if qty <= 0:
        # A pop needs no validation — it can't crash and can't smuggle
        # anything in, unlike the qty > 0 branch below.
        cart.pop(item_id, None)
    else:
        # Unlike cart_add, this id didn't come from a rendered menu button —
        # validate it the same way, or a forged id (garbage, or a real
        # MenuItem from a *different* venue) lands straight in the session
        # cart and crashes or leaks through the next `pk__in` lookup.
        if _resolve_menu_item(guest, item_id) is None:
            return redirect("atmos:cart_detail")
        cart[item_id] = min(MAX_ITEM_QTY, qty)
    request.session[CART_SESSION_KEY] = cart
    return redirect("atmos:cart_detail")


def _cart_lines(guest, cart):
    """Same venue + availability scoping `_create_order_atomic`'s `available`
    query uses — without it, a displayed total can promise more than
    order_place will actually charge (e.g. staff 86's an item while the
    guest is sitting on the cart page). Shared by cart_detail and
    order_place's unavailable-item bounce, so both show the same reality.
    """
    # Keep stale entries visible so the guest can remove them. Cross-venue
    # IDs remain hidden; only items that once belonged to this venue are shown.
    items = MenuItem.objects.select_related("category").filter(
        pk__in=cart.keys(), category__venue=guest.venue
    )
    lines, total = [], Decimal("0.00")
    for item in items:
        qty = cart.get(str(item.id), 0)
        if qty <= 0:
            continue
        is_orderable = item.is_available and item.category.is_visible
        line_total = (item.price * qty).quantize(Decimal("0.01"))
        if is_orderable:
            total += line_total
        lines.append(
            {
                "item": item,
                "quantity": qty,
                "line_total": line_total,
                "is_orderable": is_orderable,
            }
        )
    return lines, total


def _prune_orphaned_cart_keys(request, guest, cart):
    """`_cart_lines` shows a stale-but-still-real MenuItem as a removable
    "unavailable" line — but a key can also be *orphaned*: the row was
    deleted outright, or its category moved to a different venue. Neither
    case resolves to anything under this venue at all, so there's nothing
    to render or let the guest act on, and `_create_order_atomic` would
    keep rejecting placement over it forever. Cross-venue ids must stay
    invisible (never reveal another venue's item), so silently dropping the
    key is the only safe option, not surfacing it.
    """
    if not cart:
        return cart
    real_ids = {
        str(i)
        for i in MenuItem.objects.filter(
            pk__in=cart.keys(), category__venue=guest.venue
        ).values_list("id", flat=True)
    }
    orphaned = [item_id for item_id in cart if item_id not in real_ids]
    if orphaned:
        for item_id in orphaned:
            cart.pop(item_id, None)
        request.session[CART_SESSION_KEY] = cart
    return cart


def cart_detail(request):
    guest = _get_guest(request)
    if not guest:
        return render(request, "atmos/no_session.html")
    cart = _prune_orphaned_cart_keys(
        request, guest, request.session.get(CART_SESSION_KEY, {})
    )
    lines, total = _cart_lines(guest, cart)
    cart_signature = _cart_signature(lines)
    return render(
        request,
        "atmos/cart.html",
        {
            "guest": guest,
            "lines": lines,
            "total": total,
            "cart_signature": cart_signature,
        },
    )


@transaction.atomic
def _create_order_atomic(
    request, guest, expected_total=None, expected_signature="", expected_currency=""
) -> PlacementResult:
    """DB-only half of order placement: re-check the master switch, lock and
    validate the cart, create the Order + OrderItems. Returns a
    `PlacementResult` — see that class for the possible outcomes.

    Deliberately makes no network calls. `order_place()` below calls this,
    then generates the vignette *outside* this transaction — a slow model
    response must never hold the venue/menu-item locks acquired here, or
    every other guest ordering the same items queues up behind it.
    """
    # Locking the venue row does two jobs: it re-checks `service_open` at
    # the moment of placement (a guest's cookie can outlive staff flipping
    # the switch — the scan-time check in guest_scan() isn't enough), and
    # it serializes the short_code allocation below across concurrent
    # placements at the same venue (see the comment there).
    venue = Venue.objects.select_for_update().get(pk=guest.venue_id)
    if not venue.service_open:
        return PlacementResult(PlacementOutcome.VENUE_CLOSED)

    # A valid guest cookie must not revive a historical tab. Lock and
    # re-check the tab (and its table's is_active — staff taking a table
    # out of service mid-session used to only block future scans, not an
    # order already in flight against it) in the same transaction as
    # placement, so an admin close/deactivation racing this POST cannot
    # accept another order.
    tab_is_open = (
        Tab.objects.select_for_update()
        .filter(pk=guest.tab_id, venue=venue, status="open", table__is_active=True)
        .exists()
    )
    if not tab_is_open:
        return PlacementResult(PlacementOutcome.TAB_CLOSED)

    # A guest cookie can outlive staff settling/removing that guest (e.g.
    # from admin) between _get_guest() resolving it and this transaction
    # committing — lock and re-check status here too, not just venue/tab, or
    # a stale-but-signed cookie can keep placing orders under a guest staff
    # already closed out. Locking this row also lets
    # purge_stale_guest_names serialize against a concurrent placement for
    # the same guest (see that command) — re-fetched here rather than
    # trusting the `guest` argument, which may have been loaded before a
    # concurrent purge/edit committed.
    guest = (
        Guest.objects.select_for_update()
        .select_related("tab__table", "venue")
        .filter(pk=guest.pk, status="active")
        .first()
    )
    if guest is None:
        return PlacementResult(PlacementOutcome.GUEST_INACTIVE)

    cart = _prune_orphaned_cart_keys(
        request, guest, request.session.get(CART_SESSION_KEY, {})
    )
    available = {
        str(i.id): i
        for i in MenuItem.objects.select_for_update().filter(
            pk__in=cart.keys(),
            category__venue=venue,
            category__is_visible=True,
            is_available=True,
        )
    }

    # A cart entry can go stale between cart_detail and this lock (staff
    # 86's it, or its category changes venue) — used to be silently
    # dropped here, placing a smaller order than the guest just reviewed
    # with no warning. Reject the whole placement instead: leave the
    # session cart untouched and let order_place show what changed.
    missing_ids = [
        item_id for item_id, qty in cart.items() if qty > 0 and item_id not in available
    ]
    if missing_ids:
        names = list(
            MenuItem.objects.filter(
                pk__in=missing_ids, category__venue=venue
            ).values_list("name", flat=True)
        )
        return PlacementResult(PlacementOutcome.UNAVAILABLE, unavailable_names=names)

    lines, total = [], Decimal("0.00")
    for item_id, qty in cart.items():
        item = available.get(item_id)
        if not item or qty <= 0:
            continue
        lines.append((item, qty))
        total += (item.price * qty).quantize(Decimal("0.01"))

    if not lines:
        return PlacementResult(PlacementOutcome.EMPTY_CART)

    # `expected_total` alone catches a price change but not a same-priced
    # rename/repurpose of a menu item between cart review and this lock —
    # the numeric total still matches, but the guest never confirmed the
    # new item identity. Comparing the full content signature (item id,
    # qty, price, AND name) catches that too. `expected_signature` is only
    # sent by the current cart.html; an empty one (e.g. an old cached page)
    # skips this specific check rather than failing safe-by-total alone.
    signature_changed = bool(
        expected_signature
    ) and expected_signature != _order_signature(lines)
    # A venue currency change between cart review and this lock could
    # otherwise slip through when the numeric total happens to stay equal
    # (e.g. prices already keyed in the new currency's units) — reviewed and
    # charged currency must match, not just the number.
    currency_changed = bool(expected_currency) and expected_currency != venue.currency
    if (
        expected_total is None
        or total != expected_total
        or signature_changed
        or currency_changed
    ):
        return PlacementResult(
            PlacementOutcome.PRICE_CHANGED, expected_total=expected_total, actual_total=total
        )

    # `short_code` has no uniqueness constraint. A `count()`-derived number
    # used to work here (the venue lock above serializes it against other
    # concurrent placements), but it isn't monotonic: OrderAdmin permits
    # deleting orders, and a deletion moves the count backwards, so a later
    # order can reissue an already-used code. `next_order_number` only ever
    # increments, under the same lock, so deletions can't cause a collision.
    order_number = venue.next_order_number
    venue.next_order_number += 1
    venue.save(update_fields=["next_order_number"])
    short_code = f"T{guest.tab.table.label.upper()[:3]}-{order_number:02d}"

    order = Order.objects.create(
        guest=guest,
        tab=guest.tab,
        venue=venue,
        short_code=short_code,
        alias_snapshot=guest.display,
        total_amount=total,
        currency=venue.currency,
    )
    for item, qty in lines:
        OrderItem.objects.create(
            order=order,
            menu_item=item,
            name_snapshot=item.name,
            unit_price_snapshot=item.price,
            contains_alcohol_snapshot=item.contains_alcohol,
            quantity=qty,
        )

    request.session[CART_SESSION_KEY] = {}
    return PlacementResult(
        PlacementOutcome.PLACED, order=order, venue=venue, lines=lines
    )


def order_place(request):
    guest = _get_guest(request)
    if not guest or request.method != "POST":
        return render(request, "atmos/no_session.html")

    try:
        expected_total = Decimal(request.POST.get("expected_total", ""))
    except (InvalidOperation, TypeError, ValueError):
        expected_total = None
    expected_signature = request.POST.get("expected_signature", "")
    expected_currency = request.POST.get("expected_currency", "")

    result = _create_order_atomic(
        request,
        guest,
        expected_total=expected_total,
        expected_signature=expected_signature,
        expected_currency=expected_currency,
    )

    if result.outcome is PlacementOutcome.EMPTY_CART:
        return redirect("atmos:cart_detail")
    if result.outcome is PlacementOutcome.VENUE_CLOSED:
        return render(request, "atmos/closed.html", {"table": guest.tab.table})
    if result.outcome in (PlacementOutcome.TAB_CLOSED, PlacementOutcome.GUEST_INACTIVE):
        return render(request, "atmos/no_session.html")
    if result.outcome in (PlacementOutcome.UNAVAILABLE, PlacementOutcome.PRICE_CHANGED):
        # Re-fetch rather than reuse the `guest` fetched at the top of this
        # view: that instance's `venue` (select_related at that point) can
        # already be stale by the time we get here — e.g. a currency change
        # is exactly what triggered PRICE_CHANGED in the first place. Without
        # this, the hidden expected_currency field below re-renders with the
        # OLD currency again, so a resubmit fails the same check forever
        # until the guest manually reloads the cart. Fall back to the
        # original instance only if this somehow returns nothing (shouldn't
        # happen — nothing between the two fetches removes the guest).
        guest = _get_guest(request) or guest
    if result.outcome is PlacementOutcome.UNAVAILABLE:
        # Cart is untouched on purpose (see _create_order_atomic) — show the
        # guest the same lines cart_detail would, plus what changed, rather
        # than silently placing a smaller order.
        cart = request.session.get(CART_SESSION_KEY, {})
        lines, total = _cart_lines(guest, cart)
        cart_signature = _cart_signature(lines)
        return render(
            request,
            "atmos/cart.html",
            {
                "guest": guest,
                "lines": lines,
                "total": total,
                "cart_signature": cart_signature,
                "unavailable_names": result.unavailable_names,
            },
        )
    if result.outcome is PlacementOutcome.PRICE_CHANGED:
        cart = request.session.get(CART_SESSION_KEY, {})
        lines, total = _cart_lines(guest, cart)
        cart_signature = _cart_signature(lines)
        return render(
            request,
            "atmos/cart.html",
            {
                "guest": guest,
                "lines": lines,
                "total": total,
                "cart_signature": cart_signature,
                "price_changed": True,
            },
        )

    # PlacementOutcome.PLACED from here down.
    order, venue, lines = result.order, result.venue, result.lines

    # The transaction above already committed the order, and cleared the
    # cart in `request.session` — but Django doesn't write session changes
    # to the store until SessionMiddleware's process_response, i.e. after
    # this whole view returns. generate_vignette() below can take up to
    # ~1.2s by design; a second POST landing in that window would still see
    # the OLD (un-cleared) cart in the session store and place a full
    # duplicate order. Force the write now, before the slow call, so a
    # near-simultaneous resubmit sees the cart already emptied.
    request.session.save()

    # generate_vignette() itself runs outside the transaction — see
    # _create_order_atomic()'s docstring.
    chronicle = _chronicle_for(venue)
    event = ChronicleEvent(
        at=timezone.localtime(order.placed_at),
        table_label=guest.tab.table.label,
        # `guest.alias` (the noir persona), not `order.alias_snapshot` — the
        # snapshot can be a guest-typed personal name, and this event both
        # joins the venue-wide chronicle (so a *different* guest's fallback
        # vignette can reference it later) and, when a model provider is
        # configured, leaves the prompt/completion at that external
        # provider. Neither destination should ever see a personal name.
        persona=guest.alias,
        drinks=tuple(DrinkLine(item.name, qty) for item, qty in lines),
        ticket_code=order.short_code,
    )
    vignette = generate_vignette(event, chronicle, provider=_provider())
    Order.objects.filter(pk=order.pk).update(
        vignette=vignette.text, vignette_source=vignette.source
    )

    return redirect("atmos:order_status", pk=order.pk)


def order_status(request, pk):
    guest = _get_guest_for_viewing(request)
    if not guest:
        return render(request, "atmos/no_session.html")
    # Scoped through the guest, per spec §8.1 — a guest cannot address a
    # table-mate's order by editing the URL.
    order = get_object_or_404(guest.orders.prefetch_related("items__menu_item"), pk=pk)

    item_names = [i.name_snapshot for i in order.items.all()]
    ascii_art = select_ascii_art(item_names)
    mission = select_mission(f"{order.id}-{order.short_code}")

    ticket = TicketData(
        venue_name=guest.venue.name,
        table_label=guest.tab.table.label,
        ticket_code=order.short_code,
        placed_at=timezone.localtime(order.placed_at),
        persona=order.alias_snapshot,
        lines=tuple(
            TicketLine(i.name_snapshot, i.quantity, i.unit_price_snapshot, i.note)
            for i in order.items.all()
        ),
        vignette=order.vignette,
        ascii_art=ascii_art,
        mission=mission,
        # The placement-time snapshot, not guest.venue.currency: staff
        # changing a venue's currency after the fact must not silently turn
        # a historical €12.00 order into a $12.00 receipt.
        currency=order.currency,
        # Points at this table's own scan URL ("order another round"), not a
        # per-order status page: /atmos/o/<code> was never implemented, and
        # order_status itself is guest-cookie-scoped (spec §8.1) so a bare
        # public QR to it couldn't work for whoever picks up the ticket
        # anyway. This is the one link that's actually live and useful.
        # Built from THIS request/deployment (build_absolute_uri), not a
        # hardcoded production host — on test.power-up.lu or a local
        # runserver, a hardcoded power-up.lu origin would print a ticket
        # whose QR opens production with a token that database doesn't have,
        # 404ing every time. Omitted entirely for an inactive guest — see
        # _guest_can_still_scan()'s docstring.
        qr_payload=(
            request.build_absolute_uri(
                reverse("atmos:guest_scan", args=[guest.tab.table.qr_token])
            )
            if _guest_can_still_scan(guest)
            else ""
        ),
        contains_alcohol=order.contains_alcohol,
        footer="pay at the table",
    )
    ticket_preview = render_plain_text(render_ticket(ticket, Paper.MM80), Paper.MM80)
    return render(
        request,
        "atmos/order_status.html",
        {
            "guest": guest,
            "order": order,
            "ticket_preview": ticket_preview,
            "mission": mission,
            "ascii_art": ascii_art,
        },
    )


# ---------------------------------------------------------------- staff zone


@staff_member_required
@permission_required("atmos.view_venue", raise_exception=True)
def staff_home(request):
    return render(request, "atmos/staff_home.html", {"venues": Venue.objects.all()})


@staff_member_required
@permission_required("atmos.view_order", raise_exception=True)
def kds(request, venue_slug):
    venue = get_object_or_404(Venue, slug=venue_slug)
    live = (
        Order.objects.filter(venue=venue)
        .exclude(status__in=["served", "cancelled"])
        .select_related("guest", "tab__table")
        .prefetch_related("items__menu_item")
    )
    done = (
        Order.objects.filter(
            venue=venue,
            status="served",
            served_at__gte=timezone.now() - timedelta(minutes=30),
        )
        .select_related("guest", "tab__table")
        .prefetch_related("items__menu_item")
    )
    columns = {
        "new": [o for o in live if o.status == "placed"],
        "in_progress": [o for o in live if o.status in ("accepted", "preparing")],
        "done": list(done),
    }
    return render(request, "atmos/kds.html", {"venue": venue, "columns": columns})


@staff_member_required
@permission_required("atmos.change_order", raise_exception=True)
def order_set_status(request, pk):
    if request.method == "POST":
        # Locked read-modify-write: two staff devices acting on the same
        # order within the same window (one taps Accept, another taps
        # Cancel) used to both read the same pre-transition status, both
        # validate independently, and whichever save() landed last silently
        # discarded the other's transition with no error to either staff
        # member. The lock serializes them — the second request now reads
        # the FIRST request's already-committed status, so its own
        # transition is validated against current reality, not a stale read.
        with transaction.atomic():
            order = get_object_or_404(Order.objects.select_for_update(), pk=pk)
            try:
                order.transition_to(request.POST.get("status", ""))
            except ValidationError:
                pass  # illegal transition (e.g. double-tap) — KDS just re-renders as-is
    else:
        order = get_object_or_404(Order, pk=pk)
    return redirect("atmos:kds", venue_slug=order.venue.slug)
