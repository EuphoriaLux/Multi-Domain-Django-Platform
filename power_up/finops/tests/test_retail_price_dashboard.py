import re
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from power_up.finops.retail_prices.service import sync_retail_prices
from power_up.finops.tests.test_retail_price_sync import FakeConnector, azure_item

User = get_user_model()


@pytest.fixture(autouse=True)
def _fresh_option_cache():
    # The dashboard caches its dropdowns per day, and every test syncs "today".
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def regular_user(db):
    return User.objects.create_user(
        username="price-user",
        email="price-user@example.com",
        password="pw",
        is_staff=False,
    )


@pytest.mark.django_db
def test_price_dashboard_requires_login(client):
    response = client.get("/finops/prices/")
    assert response.status_code == 302
    assert "login" in response.url


@pytest.mark.django_db
def test_power_up_login_page_uses_domain_safe_template(client):
    response = client.get(
        "/accounts/login/?next=/finops/prices/",
        HTTP_HOST="test.powerup.lu",
    )

    assert response.status_code == 200
    content = response.content.decode()
    assert "Power Hub" in content
    assert "Access the protected Azure price tracker." in content


@pytest.mark.django_db
def test_authenticated_customer_sees_eur_european_comparison_and_change(
    client, regular_user
):
    today = timezone.localdate()
    first = {"Items": [azure_item("0.10000000")], "NextPageLink": None}
    second = {"Items": [azure_item("0.12000000")], "NextPageLink": None}
    sync_retail_prices(
        snapshot_date=today - timedelta(days=1),
        region="westeurope",
        connector=FakeConnector(first),
    )
    sync_retail_prices(
        snapshot_date=today, region="westeurope", connector=FakeConnector(second)
    )
    client.force_login(regular_user)

    response = client.get(
        "/finops/prices/",
        {
            "sku": "Standard_D2s_v5",
            "currency": "EUR",
            "region": "westeurope",
            "price_type": "Consumption",
            "purchase_model": "on_demand",
        },
    )

    assert response.status_code == 200
    assert response.context["currency"] == "EUR"
    assert response.context["selected_regions"] == ["westeurope"]
    assert response.context["increased_count"] == 1
    assert len(response.context["region_options"]) == 19
    germany_north = next(
        item
        for item in response.context["region_options"]
        if item["code"] == "germanynorth"
    )
    assert germany_north["restricted_access"] is True
    assert germany_north["has_snapshot"] is False
    assert len(response.context["chart_series"][0]["data"]) == 2
    content = response.content.decode()
    assert "European Azure VM retail prices" in content
    assert "legal or contractual compliance" in content
    assert "▲ +20.00%" in content


@pytest.mark.django_db
def test_price_decrease_renders_without_a_redundant_sign(client, regular_user):
    """A price decrease must render as '▼ 16.67%', never '▼ -16.67%'.

    change_percent is negative for a decrease -- the down arrow already
    conveys direction, so the magnitude shown next to it must be unsigned.
    Regression test for issue #896.
    """
    today = timezone.localdate()
    first = {"Items": [azure_item("0.12000000")], "NextPageLink": None}
    second = {"Items": [azure_item("0.10000000")], "NextPageLink": None}
    sync_retail_prices(
        snapshot_date=today - timedelta(days=1),
        region="westeurope",
        connector=FakeConnector(first),
    )
    sync_retail_prices(
        snapshot_date=today, region="westeurope", connector=FakeConnector(second)
    )
    client.force_login(regular_user)

    response = client.get(
        "/finops/prices/",
        {
            "sku": "Standard_D2s_v5",
            "currency": "EUR",
            "region": "westeurope",
            "price_type": "Consumption",
            "purchase_model": "on_demand",
        },
    )

    assert response.status_code == 200
    assert response.context["decreased_count"] == 1
    content = response.content.decode()
    assert "▼ 16.67%" in content
    assert "▼ -16.67%" not in content


def _sync_two_days(first_price, second_price):
    today = timezone.localdate()
    for offset, price in ((1, first_price), (0, second_price)):
        sync_retail_prices(
            snapshot_date=today - timedelta(days=offset),
            region="westeurope",
            connector=FakeConnector(
                {"Items": [azure_item(price)], "NextPageLink": None}
            ),
        )


@pytest.mark.django_db
def test_hand_typed_sku_resolves_to_its_stored_casing(client, regular_user):
    """The SKU box is free text; the lookup behind it must stay exact.

    A lowercase SKU still finds its prices, because the view maps it to the
    stored casing instead of querying with iexact (UPPER() skips the index).
    """
    _sync_two_days("0.10000000", "0.12000000")
    client.force_login(regular_user)

    response = client.get("/finops/prices/", {"sku": "standard_d2s_v5"})

    assert response.status_code == 200
    assert response.context["active_sku"] == "Standard_D2s_v5"
    assert len(response.context["chart_series"][0]["data"]) == 2


@pytest.mark.django_db
def test_default_view_never_scans_the_whole_price_history(client, regular_user):
    """Every snapshot query must be pinned to one day, one SKU or known keys.

    The table grows by a full European VM catalogue each night. Unbounded
    DISTINCTs over it ran for up to 20 minutes on production (2026-09-21) and
    the page never loaded. SQLite has no query plans, so assert the shape.
    """
    _sync_two_days("0.10000000", "0.12000000")
    client.force_login(regular_user)

    with CaptureQueriesContext(connection) as queries:
        response = client.get("/finops/prices/")

    assert response.status_code == 200
    assert response.context["active_sku"] == "Standard_D2s_v5"
    snapshot_selects = [
        q["sql"]
        for q in queries.captured_queries
        if q["sql"].startswith("SELECT")
        and '"finops_hub_retailpricesnapshot"' in q["sql"]
    ]
    assert snapshot_selects
    bounded = re.compile(
        r'"snapshot_date" = |"provider_sku" = |"price_key" IN |'
        r'SELECT MAX\("finops_hub_retailpricesnapshot"\."snapshot_date"\)'
    )
    unbounded = [sql for sql in snapshot_selects if not bounded.search(sql)]
    assert unbounded == []
    assert not any("UPPER(" in sql for sql in snapshot_selects)


@pytest.mark.django_db
def test_default_sku_follows_the_selected_regions_own_latest_day(client, regular_user):
    """A region that lags the newest day (mid-sync, failed sync) still gets a SKU.

    Otherwise the page would fall back to every SKU of the region at once.
    """
    today = timezone.localdate()
    sync_retail_prices(
        snapshot_date=today - timedelta(days=1),
        region="westeurope",
        connector=FakeConnector(
            {"Items": [azure_item("0.10000000")], "NextPageLink": None}
        ),
    )
    sync_retail_prices(
        snapshot_date=today,
        region="northeurope",
        connector=FakeConnector(
            {"Items": [azure_item("0.11000000", "northeurope")], "NextPageLink": None}
        ),
    )
    client.force_login(regular_user)

    response = client.get("/finops/prices/", {"region": "westeurope"})

    assert response.status_code == 200
    assert response.context["active_sku"] == "Standard_D2s_v5"
    assert len(response.context["chart_series"]) == 1
    assert len(response.context["history_rows"]) == 1


@pytest.mark.django_db
def test_sku_missing_from_the_newest_day_still_matches_exactly(client, regular_user):
    """A retired SKU typed with its stored casing keeps its history."""
    _sync_two_days("0.10000000", "0.12000000")
    from power_up.finops.models import RetailPriceSnapshot

    RetailPriceSnapshot.objects.filter(snapshot_date=timezone.localdate()).update(
        provider_sku="Standard_D4s_v5"
    )
    client.force_login(regular_user)

    response = client.get("/finops/prices/", {"sku": "Standard_D2s_v5"})

    assert response.context["active_sku"] == "Standard_D2s_v5"
    assert len(response.context["history_rows"]) == 1


@pytest.mark.django_db
def test_option_cache_key_ignores_request_controlled_filters(
    client, regular_user, mocker
):
    """Arbitrary currency/os values must not mint new cache entries."""
    _sync_two_days("0.10000000", "0.12000000")
    client.force_login(regular_user)
    cache_set = mocker.spy(cache, "set")

    for index in range(3):
        client.get("/finops/prices/", {"currency": f"X{index}", "os": f"bogus-{index}"})

    option_keys = {
        call.args[0]
        for call in cache_set.call_args_list
        if call.args[0].startswith("finops:prices:options:")
    }
    assert option_keys == {
        f"finops:prices:options:azure:{timezone.localdate().isoformat()}"
    }


@pytest.mark.django_db
def test_trend_chart_ships_sorted_day_labels_and_lets_chartjs_parse(
    client, regular_user
):
    """The trend's x axis is a category scale: it needs the days as labels.

    With ``parsing: false`` and no labels, Chart.js read the ISO date strings
    as label indexes and drew the points off-canvas, so a price drop showed
    as a flat line at the old price (2026-09-26, Easv6_Type1 in westeurope).
    """
    _sync_two_days("0.12000000", "0.10000000")
    client.force_login(regular_user)

    response = client.get("/finops/prices/")

    today = timezone.localdate()
    assert response.context["chart_labels"] == [
        (today - timedelta(days=1)).isoformat(),
        today.isoformat(),
    ]
    assert [point["y"] for point in response.context["chart_series"][0]["data"]] == [
        0.12,
        0.10,
    ]
    content = response.content.decode()
    assert 'id="retail-price-labels"' in content
    assert "parsing: false" not in content


@pytest.mark.django_db
def test_retail_sync_webhook_requires_token_and_invokes_command(
    client, settings, mocker
):
    settings.SECRET_SYNC_TOKEN = "expected-token"
    command = mocker.patch("power_up.finops.views_webhook.call_command")

    assert client.post("/finops/api/sync/retail-prices/").status_code == 403
    response = client.post(
        "/finops/api/sync/retail-prices/",
        data={"region": "westeurope"},
        content_type="application/json",
        HTTP_X_SYNC_TOKEN="expected-token",
    )

    assert response.status_code == 200
    assert response.json()["region"] == "westeurope"
    command.assert_called_once()
    assert command.call_args.args[0] == "sync_retail_prices"
    assert command.call_args.kwargs["currency"] == "EUR"
    assert command.call_args.kwargs["regions"] == ["westeurope"]


@pytest.mark.django_db
@pytest.mark.parametrize(
    "body",
    [{}, {"region": ""}, {"region": "us-east-1"}],
    ids=["missing", "blank", "unknown"],
)
def test_retail_sync_webhook_rejects_a_request_without_a_valid_region(
    client, settings, mocker, body
):
    """A region-less POST must fail loudly rather than fall back to all 19.

    Syncing the whole catalogue in one request is what timed out every night;
    silently accepting the old payload shape would reintroduce it.
    """
    settings.SECRET_SYNC_TOKEN = "expected-token"
    command = mocker.patch("power_up.finops.views_webhook.call_command")

    response = client.post(
        "/finops/api/sync/retail-prices/",
        data=body,
        content_type="application/json",
        HTTP_X_SYNC_TOKEN="expected-token",
    )

    assert response.status_code == 400
    command.assert_not_called()


@pytest.mark.django_db
def test_retail_sync_regions_endpoint_requires_token_and_lists_every_region(
    client, settings
):
    from power_up.finops.retail_prices.connectors.azure import (
        DEFAULT_EUROPEAN_REGIONS,
    )

    settings.SECRET_SYNC_TOKEN = "expected-token"

    assert client.get("/finops/api/sync/retail-prices/regions/").status_code == 403
    response = client.get(
        "/finops/api/sync/retail-prices/regions/",
        HTTP_X_SYNC_TOKEN="expected-token",
    )

    assert response.status_code == 200
    assert response.json()["regions"] == list(DEFAULT_EUROPEAN_REGIONS)
    assert response.json()["pending"] == list(DEFAULT_EUROPEAN_REGIONS)
    # Handed out once so every region of an invocation shares a date.
    assert response.json()["snapshot_date"] == timezone.localdate().isoformat()


@pytest.mark.django_db
def test_retail_sync_regions_endpoint_leaves_out_regions_captured_today(
    client, settings
):
    """The timer runs several times a morning; only what is missing is pending.

    A failed attempt, another day's run, or another currency does not count as
    captured, so those regions stay pending.
    """
    from power_up.finops.models import RetailPriceSyncRun

    settings.SECRET_SYNC_TOKEN = "expected-token"
    today = timezone.localdate()

    def add_run(region, **overrides):
        values = {
            "provider": "azure",
            "snapshot_date": today,
            "currency": "EUR",
            "region": region,
            "regions": [region],
            "status": RetailPriceSyncRun.Status.COMPLETED,
        }
        values.update(overrides)
        RetailPriceSyncRun.objects.create(**values)

    add_run("westeurope")
    add_run("northeurope", status=RetailPriceSyncRun.Status.FAILED)
    add_run("uksouth", snapshot_date=today - timedelta(days=1))
    add_run("swedencentral", currency="USD")

    response = client.get(
        "/finops/api/sync/retail-prices/regions/",
        HTTP_X_SYNC_TOKEN="expected-token",
    )

    pending = response.json()["pending"]
    assert "westeurope" not in pending
    assert {"northeurope", "uksouth", "swedencentral"} <= set(pending)
    assert len(pending) == len(response.json()["regions"]) - 1


@pytest.mark.django_db
def test_retail_sync_webhook_pins_the_snapshot_date_it_is_given(
    client, settings, mocker
):
    """Each region arrives as its own request, so the date must be caller-pinned.

    Left to itself every call would re-evaluate ``localdate()``, and a walk
    straddling local midnight would file its regions under two dates and
    complete neither.
    """
    settings.SECRET_SYNC_TOKEN = "expected-token"
    command = mocker.patch("power_up.finops.views_webhook.call_command")

    response = client.post(
        "/finops/api/sync/retail-prices/",
        data={"region": "westeurope", "snapshot_date": "2026-08-21"},
        content_type="application/json",
        HTTP_X_SYNC_TOKEN="expected-token",
    )

    assert response.status_code == 200
    assert command.call_args.kwargs["snapshot_date"] == "2026-08-21"

    # Omitting it is fine for a manual one-off: the command falls back to today.
    command.reset_mock()
    client.post(
        "/finops/api/sync/retail-prices/",
        data={"region": "westeurope"},
        content_type="application/json",
        HTTP_X_SYNC_TOKEN="expected-token",
    )
    assert command.call_args.kwargs["snapshot_date"] is None

    # A malformed date is refused rather than silently ignored.
    command.reset_mock()
    bad = client.post(
        "/finops/api/sync/retail-prices/",
        data={"region": "westeurope", "snapshot_date": "21-08-2026"},
        content_type="application/json",
        HTTP_X_SYNC_TOKEN="expected-token",
    )
    assert bad.status_code == 400
    command.assert_not_called()
