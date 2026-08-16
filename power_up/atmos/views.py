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

import os
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
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

# Dev-only in-process chronicle store — see module docstring.
_CHRONICLES: dict[str, Chronicle] = {}


def _chronicle_for(venue: Venue) -> Chronicle:
    chronicle = _CHRONICLES.get(str(venue.id))
    if chronicle is None:
        chronicle = Chronicle(venue.name, max_events=12)
        _CHRONICLES[str(venue.id)] = chronicle
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


def _get_guest(request):
    """Resolve the Guest from the signed cookie. Spec §8.1: object access
    always goes *through* the guest, never a bare pk lookup."""
    raw = request.COOKIES.get(GUEST_COOKIE)
    if not raw:
        return None
    try:
        guest_id = signing.loads(raw, max_age=GUEST_COOKIE_MAX_AGE)
    except signing.BadSignature:
        return None
    return (
        Guest.objects.select_related("tab__table", "venue")
        .filter(pk=guest_id, status="active", tab__status="open")
        .first()
    )


def _active_aliases(venue: Venue):
    return list(Guest.objects.filter(venue=venue, status="active").values_list("alias", flat=True))


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
        return MenuItem.objects.get(pk=item_id, category__venue=guest.venue, category__is_visible=True, is_available=True)
    except (MenuItem.DoesNotExist, ValidationError, ValueError):
        return None


# ---------------------------------------------------------------- guest zone


def guest_scan(request, qr_token):
    table = get_object_or_404(Table.objects.select_related("venue"), qr_token=qr_token, is_active=True)

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
    tab_id = request.session.get(TAB_SESSION_KEY)
    tab = None
    if tab_id:
        tab = Tab.objects.select_related("venue", "table").filter(pk=tab_id, status="open").first()
    if not tab:
        return render(request, "atmos/no_session.html")

    error = ""
    if request.method == "POST":
        active = _active_aliases(tab.venue)
        typed = request.POST.get("display_name", "").strip()
        rolled = request.POST.get("rolled_alias", "").strip()

        display_name = ""
        if typed:
            try:
                display_name = sanitize_persona(typed, max_length=_DISPLAY_NAME_MAX_LENGTH)
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

            # Switching identity (new table scan, new guest) must not carry
            # the previous guest's cart forward — item IDs are shared across
            # every venue on the platform, so a stale cart could otherwise
            # be ordered onto the new table under the new persona.
            request.session[CART_SESSION_KEY] = {}

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
            for _attempt in range(3):
                try:
                    with transaction.atomic():
                        guest = Guest.objects.create(
                            tab=tab, venue=tab.venue, alias=alias, display_name=display_name
                        )
                    break
                except IntegrityError:
                    alias = random_persona(exclude=_active_aliases(tab.venue))

            if guest is None:
                error = "This table's crowded with ghosts — try again."
            else:
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
    return render(request, "atmos/join.html", {"tab": tab, "rolled_alias": rolled_alias, "error": error})


def guest_menu(request):
    guest = _get_guest(request)
    if not guest:
        return render(request, "atmos/no_session.html")
    categories = guest.venue.menu_categories.filter(is_visible=True).prefetch_related("items")
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


def cart_detail(request):
    guest = _get_guest(request)
    if not guest:
        return render(request, "atmos/no_session.html")
    cart = request.session.get(CART_SESSION_KEY, {})
    lines, total = _cart_lines(guest, cart)
    return render(request, "atmos/cart.html", {"guest": guest, "lines": lines, "total": total})


@transaction.atomic
def _create_order_atomic(request, guest, expected_total=None):
    """DB-only half of order placement: re-check the master switch, lock and
    validate the cart, create the Order + OrderItems. Returns
    `(order, venue, lines)`, the string `"closed"`, `("unavailable", names)`,
    or `None` (empty cart).

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
        return "closed"

    # A valid guest cookie must not revive a historical tab. Lock and re-check
    # the tab in the same transaction as placement so an admin close racing
    # this POST cannot accept another order.
    tab_is_open = Tab.objects.select_for_update().filter(
        pk=guest.tab_id, venue=venue, status="open"
    ).exists()
    if not tab_is_open:
        return "tab_closed"

    cart = request.session.get(CART_SESSION_KEY, {})
    available = {
        str(i.id): i
        for i in MenuItem.objects.select_for_update().filter(
            pk__in=cart.keys(), category__venue=venue, category__is_visible=True, is_available=True
        )
    }

    # A cart entry can go stale between cart_detail and this lock (staff
    # 86's it, or its category changes venue) — used to be silently
    # dropped here, placing a smaller order than the guest just reviewed
    # with no warning. Reject the whole placement instead: leave the
    # session cart untouched and let order_place show what changed.
    missing_ids = [item_id for item_id, qty in cart.items() if qty > 0 and item_id not in available]
    if missing_ids:
        names = list(
            MenuItem.objects.filter(pk__in=missing_ids, category__venue=venue).values_list(
                "name", flat=True
            )
        )
        return "unavailable", names

    lines, total = [], Decimal("0.00")
    for item_id, qty in cart.items():
        item = available.get(item_id)
        if not item or qty <= 0:
            continue
        lines.append((item, qty))
        total += (item.price * qty).quantize(Decimal("0.01"))

    if not lines:
        return None

    if expected_total is not None and total != expected_total:
        return "price_changed", expected_total, total

    # `short_code` has no uniqueness constraint, and this count-then-use
    # sequence isn't atomic on its own — two concurrent placements at the
    # same venue could previously read the same count before either
    # committed and produce duplicate delivery codes. The venue lock above
    # now serializes this too.
    order_number = Order.objects.filter(venue=venue).count() + 1
    short_code = f"T{guest.tab.table.label.upper()[:3]}-{order_number:02d}"

    order = Order.objects.create(
        guest=guest,
        tab=guest.tab,
        venue=venue,
        short_code=short_code,
        alias_snapshot=guest.display,
        total_amount=total,
    )
    for item, qty in lines:
        OrderItem.objects.create(
            order=order,
            menu_item=item,
            name_snapshot=item.name,
            unit_price_snapshot=item.price,
            quantity=qty,
        )

    request.session[CART_SESSION_KEY] = {}
    return order, venue, lines


def order_place(request):
    guest = _get_guest(request)
    if not guest or request.method != "POST":
        return render(request, "atmos/no_session.html")

    try:
        expected_total = Decimal(request.POST.get("expected_total", ""))
    except (TypeError, ValueError):
        expected_total = None

    result = _create_order_atomic(request, guest, expected_total=expected_total)
    if result is None:
        return redirect("atmos:cart_detail")
    if result == "closed":
        return render(request, "atmos/closed.html", {"table": guest.tab.table})
    if result == "tab_closed":
        return render(request, "atmos/no_session.html")
    if result[0] == "unavailable":
        # Cart is untouched on purpose (see _create_order_atomic) — show the
        # guest the same lines cart_detail would, plus what changed, rather
        # than silently placing a smaller order.
        _, names = result
        cart = request.session.get(CART_SESSION_KEY, {})
        lines, total = _cart_lines(guest, cart)
        return render(
            request,
            "atmos/cart.html",
            {"guest": guest, "lines": lines, "total": total, "unavailable_names": names},
        )

    if result[0] == "price_changed":
        cart = request.session.get(CART_SESSION_KEY, {})
        lines, total = _cart_lines(guest, cart)
        return render(
            request,
            "atmos/cart.html",
            {
                "guest": guest,
                "lines": lines,
                "total": total,
                "price_changed": True,
            },
        )

    order, venue, lines = result

    # Outside the transaction — see _create_order_atomic()'s docstring.
    chronicle = _chronicle_for(venue)
    event = ChronicleEvent(
        at=timezone.localtime(order.placed_at),
        table_label=guest.tab.table.label,
        persona=order.alias_snapshot,
        drinks=tuple(DrinkLine(item.name, qty) for item, qty in lines),
        ticket_code=order.short_code,
    )
    vignette = generate_vignette(event, chronicle, provider=_provider())
    Order.objects.filter(pk=order.pk).update(
        vignette=vignette.text, vignette_source=vignette.source
    )

    return redirect("atmos:order_status", pk=order.pk)


def order_status(request, pk):
    guest = _get_guest(request)
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
        currency=guest.venue.currency,
        # Points at this table's own scan URL ("order another round"), not a
        # per-order status page: /atmos/o/<code> was never implemented, and
        # order_status itself is guest-cookie-scoped (spec §8.1) so a bare
        # public QR to it couldn't work for whoever picks up the ticket
        # anyway. This is the one link that's actually live and useful.
        qr_payload=f"https://power-up.lu/atmos/t/{guest.tab.table.qr_token}/",
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
def staff_home(request):
    return render(request, "atmos/staff_home.html", {"venues": Venue.objects.all()})


@staff_member_required
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
            venue=venue, status="served", served_at__gte=timezone.now() - timedelta(minutes=30)
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
def order_set_status(request, pk):
    order = get_object_or_404(Order, pk=pk)
    if request.method == "POST":
        try:
            order.transition_to(request.POST.get("status", ""))
        except ValidationError:
            pass  # illegal transition (e.g. double-tap) — KDS just re-renders as-is
    return redirect("atmos:kds", venue_slug=order.venue.slug)
