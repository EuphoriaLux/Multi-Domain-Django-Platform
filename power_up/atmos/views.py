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

from django.contrib.admin.views.decorators import staff_member_required
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .lore.chronicle import Chronicle, ChronicleEvent, DrinkLine
from .lore.engine import generate_vignette
from .lore.personas import random_persona
from .lore.providers import GeminiProvider, OpenAIProvider
from .lore.safety import PersonaRejected, sanitize_persona
from .models import Guest, MenuItem, Order, OrderItem, Tab, Table, Venue
from .printing.escpos import render_plain_text
from .printing.layout import Paper, TicketData, TicketLine, render_ticket

GUEST_COOKIE = "atmos_guest"
GUEST_COOKIE_MAX_AGE = 60 * 60 * 6  # 6h, spec §7.1
CART_SESSION_KEY = "atmos_cart"
TAB_SESSION_KEY = "atmos_tab_id"

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

    ATMOS_OPENAI_BASE_URL / ATMOS_OPENAI_MODEL let this point at any
    OpenAI-compatible endpoint, not just api.openai.com — e.g. GitHub Models
    (https://models.inference.ai.azure.com), which is free for development
    and authenticates with a GitHub PAT (scope: models) as the API key.
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
        return GeminiProvider(key)
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
        .filter(pk=guest_id, status="active")
        .first()
    )


def _active_aliases(venue: Venue):
    return list(Guest.objects.filter(venue=venue, status="active").values_list("alias", flat=True))


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
                display_name = sanitize_persona(typed)
            except PersonaRejected:
                error = "Let's try a noir one instead."

        if not error:
            alias = rolled if rolled and rolled not in active else random_persona(exclude=active)
            if alias in active:  # collision (e.g. a double submit) — reroll silently
                alias = random_persona(exclude=active)
            guest = Guest.objects.create(tab=tab, venue=tab.venue, alias=alias, display_name=display_name)
            response = redirect("atmos:guest_menu")
            response.set_cookie(
                GUEST_COOKIE,
                signing.dumps(str(guest.id)),
                max_age=GUEST_COOKIE_MAX_AGE,
                httponly=True,
                samesite="Lax",
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
    item = get_object_or_404(
        MenuItem, pk=request.POST.get("item_id"), category__venue=guest.venue, is_available=True
    )
    try:
        qty = max(1, int(request.POST.get("quantity", 1)))
    except (TypeError, ValueError):
        qty = 1
    cart = request.session.get(CART_SESSION_KEY, {})
    cart[str(item.id)] = cart.get(str(item.id), 0) + qty
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
        cart.pop(item_id, None)
    else:
        cart[item_id] = qty
    request.session[CART_SESSION_KEY] = cart
    return redirect("atmos:cart_detail")


def cart_detail(request):
    guest = _get_guest(request)
    if not guest:
        return render(request, "atmos/no_session.html")
    cart = request.session.get(CART_SESSION_KEY, {})
    lines, total = [], Decimal("0.00")
    for item in MenuItem.objects.filter(pk__in=cart.keys()):
        qty = cart.get(str(item.id), 0)
        if qty <= 0:
            continue
        line_total = (item.price * qty).quantize(Decimal("0.01"))
        total += line_total
        lines.append({"item": item, "quantity": qty, "line_total": line_total})
    return render(request, "atmos/cart.html", {"guest": guest, "lines": lines, "total": total})


@transaction.atomic
def order_place(request):
    guest = _get_guest(request)
    if not guest or request.method != "POST":
        return render(request, "atmos/no_session.html")

    cart = request.session.get(CART_SESSION_KEY, {})
    available = {
        str(i.id): i
        for i in MenuItem.objects.select_for_update().filter(
            pk__in=cart.keys(), category__venue=guest.venue, is_available=True
        )
    }
    lines, total = [], Decimal("0.00")
    for item_id, qty in cart.items():
        item = available.get(item_id)
        if not item or qty <= 0:
            continue
        lines.append((item, qty))
        total += (item.price * qty).quantize(Decimal("0.01"))

    if not lines:
        return redirect("atmos:cart_detail")

    order_number = Order.objects.filter(venue=guest.venue).count() + 1
    short_code = f"T{guest.tab.table.label.upper()[:3]}-{order_number:02d}"

    order = Order.objects.create(
        guest=guest,
        tab=guest.tab,
        venue=guest.venue,
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

    chronicle = _chronicle_for(guest.venue)
    event = ChronicleEvent(
        at=timezone.localtime(order.placed_at),
        table_label=guest.tab.table.label,
        persona=order.alias_snapshot,
        drinks=tuple(DrinkLine(item.name, qty) for item, qty in lines),
        ticket_code=order.short_code,
    )
    result = generate_vignette(event, chronicle, provider=_provider())
    order.vignette = result.text
    order.vignette_source = result.source
    order.save(update_fields=["vignette", "vignette_source"])

    request.session[CART_SESSION_KEY] = {}
    return redirect("atmos:order_status", pk=order.pk)


def order_status(request, pk):
    guest = _get_guest(request)
    if not guest:
        return render(request, "atmos/no_session.html")
    # Scoped through the guest, per spec §8.1 — a guest cannot address a
    # table-mate's order by editing the URL.
    order = get_object_or_404(guest.orders.prefetch_related("items__menu_item"), pk=pk)

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
        currency=guest.venue.currency,
        qr_payload=f"https://power-up.lu/atmos/o/{order.short_code}",
        contains_alcohol=any(i.menu_item.contains_alcohol for i in order.items.all()),
        footer="pay at the table",
    )
    ticket_preview = render_plain_text(render_ticket(ticket, Paper.MM80), Paper.MM80)
    return render(
        request,
        "atmos/order_status.html",
        {"guest": guest, "order": order, "ticket_preview": ticket_preview},
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
        .prefetch_related("items")
    )
    done = (
        Order.objects.filter(
            venue=venue, status="served", served_at__gte=timezone.now() - timedelta(minutes=30)
        )
        .select_related("guest", "tab__table")
        .prefetch_related("items")
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
