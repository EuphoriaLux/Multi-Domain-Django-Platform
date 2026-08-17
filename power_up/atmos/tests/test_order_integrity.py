from decimal import Decimal

import pytest
from django.contrib.sessions.middleware import SessionMiddleware
from django.core import signing
from django.test import RequestFactory

from power_up.atmos.models import Guest, MenuCategory, MenuItem, Tab, Table, Venue
from power_up.atmos.views import (
    CART_SESSION_KEY,
    GUEST_COOKIE,
    PlacementOutcome,
    _cart_lines,
    _create_order_atomic,
    _get_guest,
    _resolve_menu_item,
)


def request_with_session(method="get", data=None):
    request = getattr(RequestFactory(), method)("/", data=data or {})
    SessionMiddleware(lambda req: None).process_request(request)
    request.session.save()
    return request


@pytest.fixture
def ordering_setup(db):
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
    return venue, table, tab, guest, category, item


@pytest.mark.django_db
def test_closed_tab_invalidates_existing_guest_cookie(ordering_setup):
    _venue, _table, tab, guest, _category, _item = ordering_setup
    tab.status = "closed"
    tab.save(update_fields=["status"])

    request = request_with_session()
    request.COOKIES[GUEST_COOKIE] = signing.dumps(str(guest.id))

    assert _get_guest(request) is None


@pytest.mark.django_db
def test_hidden_category_item_cannot_be_added(ordering_setup):
    _venue, _table, _tab, guest, category, item = ordering_setup
    category.is_visible = False
    category.save(update_fields=["is_visible"])

    assert _resolve_menu_item(guest, str(item.id)) is None


@pytest.mark.django_db
def test_unavailable_cart_item_stays_visible_and_removable(ordering_setup):
    _venue, _table, _tab, guest, _category, item = ordering_setup
    item.is_available = False
    item.save(update_fields=["is_available"])

    lines, total = _cart_lines(guest, {str(item.id): 2})

    assert len(lines) == 1
    assert lines[0]["item"] == item
    assert lines[0]["is_orderable"] is False
    assert total == Decimal("0.00")


# Plain @pytest.mark.django_db, not transaction=True: _create_order_atomic's
# own @transaction.atomic just becomes a savepoint under the test's outer
# transaction, and select_for_update is a no-op on SQLite in tests anyway, so
# there's nothing here that needs real commits. transaction=True would make
# pytest-django flush (not just roll back) this worker's whole test database
# afterward — wiping every crush_lu/hub table seeded by data migrations
# (e.g. the Interest catalog) for the rest of the tests sharing that worker,
# with no serialized_rollback to restore them. That flush-order bug is
# exactly what broke crush_lu's WizardStep2Tests under pytest-xdist here.
@pytest.mark.django_db
def test_order_rejects_price_changed_since_cart_review(ordering_setup):
    _venue, _table, _tab, guest, _category, item = ordering_setup
    request = request_with_session("post", {"expected_total": "10.00"})
    request.session[CART_SESSION_KEY] = {str(item.id): 1}

    result = _create_order_atomic(request, guest, expected_total=Decimal("10.00"))

    assert result.outcome is PlacementOutcome.PRICE_CHANGED
    assert result.expected_total == Decimal("10.00")
    assert result.actual_total == Decimal("12.00")
    assert guest.orders.count() == 0
    assert request.session[CART_SESSION_KEY] == {str(item.id): 1}


@pytest.mark.django_db
def test_order_rechecks_tab_status_inside_transaction(ordering_setup):
    _venue, _table, tab, guest, _category, item = ordering_setup
    tab.status = "closed"
    tab.save(update_fields=["status"])
    request = request_with_session("post", {"expected_total": "12.00"})
    request.session[CART_SESSION_KEY] = {str(item.id): 1}

    result = _create_order_atomic(request, guest, expected_total=Decimal("12.00"))
    assert result.outcome is PlacementOutcome.TAB_CLOSED
    assert guest.orders.count() == 0
