"""seed_atmos_demo must produce spec-sized demo data (§10 step 5: "one
venue, 12 tables, ~25 items at realistic Luxembourg prices") and stay
idempotent — a second run must not create duplicates or a second staff
account."""

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command

from power_up.atmos.models import MenuCategory, MenuItem, Table, Venue


@pytest.mark.django_db
def test_seed_creates_spec_sized_venue():
    call_command("seed_atmos_demo")

    venue = Venue.objects.get(slug="velvet-hour")
    assert Table.objects.filter(venue=venue).count() == 12

    item_count = MenuItem.objects.filter(category__venue=venue).count()
    # "~25" per spec — allow a little room without silently regressing back
    # toward the old 11-item seed.
    assert 23 <= item_count <= 27, f"expected ~25 menu items, got {item_count}"

    # The three categories the spec/preview fixture both assume stay intact.
    assert set(
        MenuCategory.objects.filter(venue=venue).values_list("name", flat=True)
    ) == {"Cocktails", "Beer & Wine", "Snacks"}

    # Table labels "4", "9", "12", "7" are load-bearing: preview.py's SERVICE
    # fixture prints tickets against exactly these labels, so a demo can walk
    # the printed tickets and the live web flow side by side.
    labels = set(Table.objects.filter(venue=venue).values_list("label", flat=True))
    assert {"4", "7", "9", "12"} <= labels

    # Prices preview.py's SERVICE fixture bakes in verbatim must survive the
    # expansion unchanged.
    for name, price in [
        ("Old Fashioned", "8.50"),
        ("Smoky Mezcalita", "9.50"),
        ("Rye Sour", "9.00"),
        ("French 75", "11.00"),
        ("Bar Nuts", "4.00"),
    ]:
        item = MenuItem.objects.get(category__venue=venue, name=name)
        assert str(item.price) == price


@pytest.mark.django_db
def test_seed_is_idempotent():
    call_command("seed_atmos_demo")
    call_command("seed_atmos_demo")

    venue = Venue.objects.get(slug="velvet-hour")
    assert Venue.objects.filter(slug="velvet-hour").count() == 1
    assert Table.objects.filter(venue=venue).count() == 12
    assert MenuCategory.objects.filter(venue=venue).count() == 3
    # The real duplicate-creation check: MenuItem has no DB-level unique
    # constraint on (category, name), so idempotency rests entirely on
    # get_or_create matching by name — a second run that silently created a
    # second "Old Fashioned" would still pass a `.distinct()` comparison
    # (the FK join gives one row per item either way) but would fail this
    # exact count.
    assert MenuItem.objects.filter(category__venue=venue).count() == 25
    assert get_user_model().objects.filter(username="atmos_staff").count() == 1


@pytest.mark.django_db
def test_seed_alcohol_flags_accurate():
    call_command("seed_atmos_demo")

    venue = Venue.objects.get(slug="velvet-hour")
    non_alcoholic = {
        "Velvet Fizz (0%)",
        "Virgin Mule (0%)",
        "Alcohol-Free Lager (0%)",
        "Bar Nuts",
        "Olives",
        "Charcuterie Board",
        "Cheese Board",
        "Patatas Bravas",
        "Croquettes (3 pcs)",
        "Chips & Dip",
    }
    for item in MenuItem.objects.filter(category__venue=venue):
        assert item.contains_alcohol == (item.name not in non_alcoholic), item.name
