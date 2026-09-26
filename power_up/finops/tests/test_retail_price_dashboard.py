import re
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from django.utils.formats import date_format

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


def _sync_region(day, region, price):
    sync_retail_prices(
        snapshot_date=day,
        region=region,
        connector=FakeConnector(
            {"Items": [azure_item(price, region)], "NextPageLink": None}
        ),
    )


BOUNDED_SNAPSHOT_SQL = re.compile(
    r'"snapshot_date" = |"provider_sku" = |"price_key" IN |'
    r'SELECT MAX\("finops_hub_retailpricesnapshot"\."snapshot_date"\)'
)


@pytest.mark.django_db
@pytest.mark.parametrize(
    "params", [{}, {"sku": "Standard_D2s_v5"}], ids=["region-index", "one-sku"]
)
def test_dashboard_never_scans_the_whole_price_history(client, regular_user, params):
    """Every snapshot query must be pinned to one day, one SKU or known keys.

    The table grows by a full European VM catalogue each night. Unbounded
    DISTINCTs over it ran for up to 20 minutes on production (2026-09-21) and
    the page never loaded. SQLite has no query plans, so assert the shape.
    """
    _sync_two_days("0.10000000", "0.12000000")
    client.force_login(regular_user)

    with CaptureQueriesContext(connection) as queries:
        response = client.get("/finops/prices/", params)

    assert response.status_code == 200
    snapshot_selects = [
        q["sql"]
        for q in queries.captured_queries
        if q["sql"].startswith("SELECT")
        and '"finops_hub_retailpricesnapshot"' in q["sql"]
    ]
    assert snapshot_selects
    unbounded = [
        sql for sql in snapshot_selects if not BOUNDED_SNAPSHOT_SQL.search(sql)
    ]
    assert unbounded == []
    assert not any("UPPER(" in sql for sql in snapshot_selects)


@pytest.mark.django_db
def test_no_sku_shows_an_overall_price_index_per_region(client, regular_user):
    """Without a SKU the page compares whole regions against West Europe."""
    today = timezone.localdate()
    _sync_region(today, "westeurope", "10.00000000")
    _sync_region(today, "northeurope", "10.40000000")
    _sync_region(today, "swedencentral", "9.50000000")
    client.force_login(regular_user)

    response = client.get("/finops/prices/")

    assert response.status_code == 200
    assert response.context["active_sku"] == ""
    assert response.context["history_rows"] == []
    assert response.context["index_reference"] == "West Europe"
    assert response.context["index_day"] == today
    index = {item["region_code"]: item for item in response.context["region_index"]}
    assert index["westeurope"]["index"] == 100.0
    assert index["westeurope"]["is_reference"] is True
    assert index["northeurope"]["index"] == 104.0
    assert index["northeurope"]["difference_percent"] == 4.0
    assert index["swedencentral"]["index"] == 95.0
    assert index["swedencentral"]["compared"] == 1
    # Cheapest first.
    assert [item["region_code"] for item in response.context["region_index"]] == [
        "swedencentral",
        "westeurope",
        "northeurope",
    ]
    content = response.content.decode()
    assert "Overall price by region" in content
    assert "No matching price history yet" not in content
    # 16 selected regions had no snapshot that day and are named, not dropped.
    assert "North Europe" not in response.context["index_missing_regions"]
    assert len(response.context["index_missing_regions"]) == 16


@pytest.mark.django_db
def test_region_index_only_compares_the_same_offer(client, regular_user):
    """Different SKUs or OS/licensing never enter one ratio."""
    today = timezone.localdate()
    _sync_region(today, "westeurope", "10.00000000")
    other = azure_item("50.00000000", "northeurope")
    other.update(
        armSkuName="Standard_E64s_v5", meterId="meter-2", skuId="product-1/sku-2"
    )
    sync_retail_prices(
        snapshot_date=today,
        region="northeurope",
        connector=FakeConnector({"Items": [other], "NextPageLink": None}),
    )
    client.force_login(regular_user)

    response = client.get("/finops/prices/")

    codes = [item["region_code"] for item in response.context["region_index"]]
    assert codes == ["westeurope"]
    assert "North Europe" in response.context["index_missing_regions"]


@pytest.mark.django_db
def test_region_index_falls_back_to_the_selected_regions_own_latest_day(
    client, regular_user
):
    """A region that lags the newest day (mid-sync, failed sync) still gets an index."""
    today = timezone.localdate()
    _sync_region(today - timedelta(days=1), "westeurope", "0.10000000")
    _sync_region(today, "northeurope", "0.11000000")
    client.force_login(regular_user)

    response = client.get("/finops/prices/", {"region": "westeurope"})

    assert response.status_code == 200
    assert response.context["index_day"] == today - timedelta(days=1)
    assert [item["region_code"] for item in response.context["region_index"]] == [
        "westeurope"
    ]


@pytest.mark.django_db
def test_region_index_keeps_every_region_during_the_morning_sync(client, regular_user):
    """Mid-sync, regions not reached yet keep yesterday's prices.

    Indexing only the newest day would show the few regions synced so far,
    measured against a stand-in reference instead of West Europe.
    """
    today = timezone.localdate()
    yesterday = today - timedelta(days=1)
    _sync_region(yesterday, "westeurope", "10.00000000")
    _sync_region(yesterday, "northeurope", "10.00000000")
    _sync_region(yesterday, "swedencentral", "9.00000000")
    _sync_region(today, "northeurope", "10.40000000")  # Only region synced so far.
    client.force_login(regular_user)

    response = client.get("/finops/prices/")

    assert response.context["index_reference"] == "West Europe"
    index = {item["region_code"]: item for item in response.context["region_index"]}
    assert set(index) == {"westeurope", "northeurope", "swedencentral"}
    assert index["northeurope"]["index"] == 104.0
    assert index["northeurope"]["snapshot_date"] == today
    assert index["westeurope"]["snapshot_date"] == yesterday
    assert response.context["index_day"] == today
    assert f"prices of {date_format(yesterday)}" in response.content.decode()


@pytest.mark.django_db
def test_region_index_never_mixes_commercial_offers(client, regular_user):
    """With "All" price types, each ratio still compares the same offer.

    The fixture item also carries a 1-year savings-plan price of 0.07 in both
    regions. Pooled with on-demand under one key, Min() would pick 0.07 on
    both sides and hide the 4% on-demand gap.
    """
    today = timezone.localdate()
    _sync_region(today, "westeurope", "10.00000000")
    _sync_region(today, "northeurope", "10.40000000")
    client.force_login(regular_user)

    response = client.get("/finops/prices/", {"price_type": "", "purchase_model": ""})

    index = {item["region_code"]: item for item in response.context["region_index"]}
    # Median of on-demand 1.04 and savings plan 1.00.
    assert index["northeurope"]["index"] == 102.0
    assert index["northeurope"]["compared"] == 2


@pytest.mark.django_db
def test_status_card_reports_the_index_snapshot(client, regular_user):
    today = timezone.localdate()
    _sync_region(today, "westeurope", "10.00000000")
    client.force_login(regular_user)

    response = client.get("/finops/prices/")

    assert response.context["latest_snapshot"] == today
    assert "No price snapshot yet" not in response.content.decode()


def _index_group_by_queries(queries):
    return [
        q["sql"]
        for q in queries.captured_queries
        if '"finops_hub_retailpricesnapshot"' in q["sql"]
        and "GROUP BY" in q["sql"]
        and '"meter_name"' in q["sql"]
    ]


@pytest.mark.django_db
def test_region_index_is_cached_until_a_region_snapshot_moves(client, regular_user):
    """Grouping the daily catalogue runs once, not on every page load.

    Selecting fewer regions reuses the same cached index; a new snapshot day
    for any region invalidates it.
    """
    today = timezone.localdate()
    yesterday = today - timedelta(days=1)
    _sync_region(yesterday, "westeurope", "10.00000000")
    _sync_region(yesterday, "northeurope", "10.40000000")
    client.force_login(regular_user)

    with CaptureQueriesContext(connection) as first:
        client.get("/finops/prices/")
    with CaptureQueriesContext(connection) as second:
        response = client.get(
            "/finops/prices/", {"region": ["westeurope", "northeurope"]}
        )

    assert len(_index_group_by_queries(first)) == 1
    assert _index_group_by_queries(second) == []
    index = {item["region_code"]: item for item in response.context["region_index"]}
    assert index["northeurope"]["index"] == 104.0

    _sync_region(today, "northeurope", "10.80000000")
    with CaptureQueriesContext(connection) as third:
        response = client.get("/finops/prices/")

    assert len(_index_group_by_queries(third)) == 1
    index = {item["region_code"]: item for item in response.context["region_index"]}
    assert index["northeurope"]["index"] == 108.0


@pytest.mark.django_db
@pytest.mark.parametrize(
    "params",
    [
        {"currency": "XYZ"},
        {"os": "bogus"},
        {"price_type": "bogus"},
        {"purchase_model": "bogus"},
        {"product": "no such product"},
    ],
    ids=["currency", "os", "price-type", "purchase-model", "product"],
)
def test_unknown_filters_neither_build_nor_cache_an_index(
    client, regular_user, mocker, params
):
    _sync_region(timezone.localdate(), "westeurope", "10.00000000")
    client.force_login(regular_user)
    cache_set = mocker.spy(cache, "set")

    with CaptureQueriesContext(connection) as queries:
        response = client.get("/finops/prices/", params)

    assert response.status_code == 200
    assert response.context["region_index"] == []
    assert _index_group_by_queries(queries) == []
    assert not any(
        call.args[0].startswith("finops:prices:index:")
        for call in cache_set.call_args_list
    )


@pytest.mark.django_db
def test_region_index_reads_each_region_in_the_requested_currency(client, regular_user):
    """A newer snapshot in another currency must not hide the EUR one."""
    from power_up.finops.models import RetailPriceSnapshot

    today = timezone.localdate()
    yesterday = today - timedelta(days=1)
    _sync_region(yesterday, "westeurope", "10.00000000")
    _sync_region(yesterday, "northeurope", "10.40000000")
    _sync_region(today, "northeurope", "11.00000000")
    RetailPriceSnapshot.objects.filter(
        region_code="northeurope", snapshot_date=today
    ).update(currency="USD")
    client.force_login(regular_user)

    response = client.get("/finops/prices/", {"currency": "EUR"})

    index = {item["region_code"]: item for item in response.context["region_index"]}
    assert index["northeurope"]["index"] == 104.0
    assert index["northeurope"]["snapshot_date"] == yesterday


@pytest.mark.django_db
def test_region_lookups_never_scale_with_submitted_region_values(client, regular_user):
    """Repeated or invented region values cost no extra queries."""
    _sync_region(timezone.localdate(), "westeurope", "10.00000000")
    client.force_login(regular_user)

    with CaptureQueriesContext(connection) as few:
        client.get("/finops/prices/", {"region": ["westeurope"]})
    cache.clear()
    with CaptureQueriesContext(connection) as many:
        response = client.get(
            "/finops/prices/",
            {"region": ["westeurope"] * 300 + [f"fake{i}" for i in range(300)]},
        )

    assert response.status_code == 200
    assert len(many.captured_queries) == len(few.captured_queries)
    assert response.context["selected_regions"][0] == "westeurope"
    assert response.context["selected_regions"].count("westeurope") == 1


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
        f"finops:prices:options:v2:azure:{timezone.localdate().isoformat()}"
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

    response = client.get("/finops/prices/", {"sku": "Standard_D2s_v5"})

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
