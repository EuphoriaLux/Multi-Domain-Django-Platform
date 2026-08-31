"""order_print_payload — the KDS's RawBT hand-off endpoint.

The staff gate matters more than the payload here: this endpoint renders the
same ticket `order_status` shows the guest, but it is reachable by pk without
any guest-cookie scoping, so its whole security story is the decorator stack.
"""

import base64
from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client, override_settings
from django.urls import reverse

from power_up.atmos.models import (
    Guest,
    MenuCategory,
    MenuItem,
    Order,
    OrderItem,
    Tab,
    Table,
    Venue,
)
from power_up.atmos.printing.escpos import INIT

# Real requests reach /atmos/ because DomainURLRoutingMiddleware swaps in
# urls_power_up for the power-up.lu host; the test client must present that
# host for the same reason (matching power_up/tests.py), and reverse() at
# assertion time needs the matching ROOT_URLCONF override — same reasoning
# as test_order_integrity's guest_join test.
pytestmark = pytest.mark.django_db
kds_urlconf = override_settings(ROOT_URLCONF="azureproject.urls_power_up")


@pytest.fixture
def client():
    return Client(HTTP_HOST="power-up.lu")


@pytest.fixture
def printable_order(db):
    venue = Venue.objects.create(name="Noir Bar", slug="noir")
    table = Table.objects.create(venue=venue, label="A1")
    tab = Tab.objects.create(table=table, venue=venue)
    guest = Guest.objects.create(
        tab=tab,
        venue=venue,
        alias="The Velvet Silhouette",
    )
    category = MenuCategory.objects.create(venue=venue, name="Cocktails")
    item = MenuItem.objects.create(
        category=category,
        name="Midnight Fizz",
        price=Decimal("12.00"),
    )
    order = Order.objects.create(
        guest=guest,
        tab=tab,
        venue=venue,
        short_code="TA1-01",
        alias_snapshot=guest.alias,
        total_amount=Decimal("24.00"),
    )
    OrderItem.objects.create(
        order=order,
        menu_item=item,
        name_snapshot=item.name,
        unit_price_snapshot=item.price,
        quantity=2,
        line_total=Decimal("24.00"),
    )
    return order


def _staff_with_view_order(username="kds_staff"):
    user = get_user_model().objects.create_user(username=username, is_staff=True)
    user.user_permissions.add(
        Permission.objects.get(
            codename="view_order", content_type__app_label="atmos"
        )
    )
    return user


def _print_url(order):
    return reverse("atmos:order_print_payload", args=[order.pk])


@kds_urlconf
def test_anonymous_is_redirected_to_login(client, printable_order):
    response = client.get(_print_url(printable_order))
    assert response.status_code == 302


@kds_urlconf
def test_staff_without_atmos_permission_is_403(client, printable_order):
    user = get_user_model().objects.create_user(
        username="other_app_staff", is_staff=True
    )
    client.force_login(user)
    assert client.get(_print_url(printable_order)).status_code == 403


@kds_urlconf
def test_payload_is_the_orders_escpos_ticket(client, printable_order):
    client.force_login(_staff_with_view_order())
    response = client.get(_print_url(printable_order))

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["short_code"] == "TA1-01"

    payload = base64.b64decode(data["print_payload_base64"], validate=True)
    # A real ESC/POS job for THIS order: initialized for the printer, and
    # carrying the code and persona (uppercased by the layout) that the
    # guest's own on-screen ticket shows — both are plain ASCII, so they
    # survive CP858 encoding byte-for-byte.
    assert payload.startswith(INIT)
    assert b"TA1-01" in payload
    assert b"THE VELVET SILHOUETTE" in payload


@kds_urlconf
def test_served_orders_stay_reprintable(client, printable_order):
    """Terminal status must not block a reprint — paper jams outlive `served`."""
    printable_order.transition_to("accepted")
    printable_order.transition_to("preparing")
    printable_order.transition_to("served")

    client.force_login(_staff_with_view_order())
    response = client.get(_print_url(printable_order))
    assert response.status_code == 200
    assert response.json()["success"] is True


@kds_urlconf
def test_vignette_pending_defers_only_the_race_window(client, printable_order):
    """A fresh order whose vignette write hasn't landed yet is flagged so
    auto-print waits a cycle; once the vignette (or enough time) lands, it
    isn't. The payload itself is served either way — manual reprints don't
    defer."""
    client.force_login(_staff_with_view_order())
    url = _print_url(printable_order)

    fresh = client.get(url).json()
    assert fresh["vignette_pending"] is True
    assert fresh["print_payload_base64"]

    Order.objects.filter(pk=printable_order.pk).update(
        vignette="The night held its breath.", vignette_source="fallback"
    )
    assert client.get(url).json()["vignette_pending"] is False


@kds_urlconf
def test_old_storyless_order_is_not_pending_forever(client, printable_order):
    """If the vignette write died, the order legitimately has none — after
    the age cutoff it must print rather than stay deferred every cycle."""
    client.force_login(_staff_with_view_order())
    Order.objects.filter(pk=printable_order.pk).update(
        placed_at=timezone.now() - timedelta(seconds=30)
    )
    assert client.get(_print_url(printable_order)).json()["vignette_pending"] is False


@kds_urlconf
def test_kds_board_carries_the_print_wiring(client, printable_order):
    """The board page ships everything the RawBT script needs: the per-card
    endpoint URL and order identity, the auto-print toggle, and the intent
    fallback — if any of these drop out of the templates, printing dies
    silently on the tablet (the script is a deliberate no-op without them)."""
    client.force_login(_staff_with_view_order())
    response = client.get(reverse("atmos:kds", args=[printable_order.venue.slug]))

    assert response.status_code == 200
    html = response.content.decode()
    assert f'data-print-url="{_print_url(printable_order)}"' in html
    assert f'data-order-id="{printable_order.pk}"' in html
    assert 'id="printer-toggle"' in html
    assert "rawbtprinter" in html


@kds_urlconf
def test_unknown_order_is_404(client, printable_order):
    client.force_login(_staff_with_view_order())
    response = client.get(
        reverse(
            "atmos:order_print_payload",
            args=["00000000-0000-0000-0000-000000000000"],
        )
    )
    assert response.status_code == 404
