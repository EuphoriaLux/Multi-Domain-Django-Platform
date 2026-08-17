import pytest
from django.core.exceptions import ValidationError

from power_up.atmos.models import Guest, Order, Tab, Table, Venue


@pytest.fixture
def venue(db):
    return Venue.objects.create(name="Noir Bar", slug="noir")


@pytest.mark.django_db
def test_table_label_editable_before_any_tabs(venue):
    table = Table.objects.create(venue=venue, label="A1")
    table.label = "A2"
    table.full_clean()  # must not raise — no tabs yet


@pytest.mark.django_db
def test_table_label_immutable_once_tabs_exist(venue):
    table = Table.objects.create(venue=venue, label="A1")
    Tab.objects.create(table=table, venue=venue)

    table.label = "A2"
    with pytest.raises(ValidationError):
        table.full_clean()


@pytest.mark.django_db
def test_tab_reopen_rejected(venue):
    table = Table.objects.create(venue=venue, label="A1")
    tab = Tab.objects.create(table=table, venue=venue)
    tab.status = "closed"
    tab.save(update_fields=["status"])

    tab.status = "open"
    with pytest.raises(ValidationError):
        tab.full_clean()


@pytest.mark.django_db
def test_tab_close_purges_guest_display_name_and_snapshots(venue):
    table = Table.objects.create(venue=venue, label="A1")
    tab = Tab.objects.create(table=table, venue=venue)
    guest = Guest.objects.create(
        tab=tab, venue=venue, alias="The Velvet Silhouette", display_name="Alice"
    )
    order = Order.objects.create(
        guest=guest,
        tab=tab,
        venue=venue,
        short_code="TA1-01",
        alias_snapshot="Alice",  # the personal name, as order_place() would copy it
    )

    tab.status = "closed"
    tab.save(update_fields=["status"])

    guest.refresh_from_db()
    order.refresh_from_db()
    assert guest.display_name == ""
    assert guest.status == "settled"
    # Reset to the non-personal alias, not left as the typed name — this is
    # the same promise purge_stale_guest_names makes, just applied at the
    # moment the tab actually closes instead of waiting for a time cutoff.
    assert order.alias_snapshot == guest.alias


@pytest.mark.django_db
def test_tab_close_purges_removed_guest_names_too(venue):
    """GuestAdmin lets staff mark a guest "removed" (e.g. ejecting someone)
    before the tab itself closes. That guest already left the
    status="active" bucket, but its display_name/alias_snapshot are still
    personal data that must not survive close-of-night just because of
    that — the purge must not be scoped to active guests only."""
    table = Table.objects.create(venue=venue, label="A1")
    tab = Tab.objects.create(table=table, venue=venue)
    guest = Guest.objects.create(
        tab=tab,
        venue=venue,
        alias="The Velvet Silhouette",
        display_name="Alice",
        status="removed",
    )
    order = Order.objects.create(
        guest=guest,
        tab=tab,
        venue=venue,
        short_code="TA1-01",
        alias_snapshot="Alice",
    )

    tab.status = "closed"
    tab.save(update_fields=["status"])

    guest.refresh_from_db()
    order.refresh_from_db()
    assert guest.display_name == ""
    assert guest.status == "removed"  # untouched — only active guests settle
    assert order.alias_snapshot == guest.alias
