from datetime import date
import gzip
import json

import pytest
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError

from power_up.finops.models import (
    RetailPriceRawPage,
    RetailPriceSnapshot,
    RetailPriceSyncRun,
)
from power_up.finops.retail_prices.connectors.base import ConnectorPage
from power_up.finops.retail_prices.connectors.azure import AzureRetailPricesConnector
from power_up.finops.retail_prices.service import (
    SnapshotAlreadyExists,
    sync_retail_prices,
)


def azure_item(price="0.10000000", region="westeurope"):
    return {
        "currencyCode": "EUR",
        "retailPrice": price,
        "armRegionName": region,
        "location": "EU West",
        "effectiveStartDate": "2026-08-01T00:00:00Z",
        "meterId": "meter-1",
        "meterName": "D2s v5",
        "productId": "product-1",
        "skuId": "product-1/sku-1",
        "productName": "Virtual Machines Dsv5 Series",
        "skuName": "D2s v5",
        "serviceName": "Virtual Machines",
        "serviceFamily": "Compute",
        "unitOfMeasure": "1 Hour",
        "type": "Consumption",
        "isPrimaryMeterRegion": True,
        "armSkuName": "Standard_D2s_v5",
        "savingsPlan": [{"term": "1 Year", "retailPrice": "0.07000000"}],
    }


def storage_item(price="0.02000000"):
    return {
        "currencyCode": "EUR",
        "retailPrice": price,
        "armRegionName": "westeurope",
        "location": "EU West",
        "effectiveStartDate": "2026-08-01T00:00:00Z",
        "meterId": "storage-meter-1",
        "meterName": "LRS Data Stored",
        "productId": "storage-product-1",
        "skuId": "storage-product-1/sku-1",
        "productName": "Storage Blob Hot LRS",
        "skuName": "Hot LRS",
        "serviceName": "Storage",
        "serviceFamily": "Storage",
        "unitOfMeasure": "1 GB/Month",
        "type": "Consumption",
    }


class FakeConnector:
    name = "Fake Azure connector"
    endpoint = "https://prices.example.test/api"

    def __init__(self, payload):
        self.payload = payload

    def iter_pages(self, *, regions, currency):
        raw = json.dumps(self.payload, separators=(",", ":")).encode("utf-8")
        yield ConnectorPage(
            source_url="https://prices.example.test/api?page=1",
            next_page_url="",
            payload=self.payload,
            raw_content=raw,
        )

    normalize_item = AzureRetailPricesConnector.normalize_item


@pytest.mark.django_db
def test_sync_archives_raw_page_normalizes_savings_and_preserves_history():
    valid = azure_item()
    invalid = {"currencyCode": "EUR", "retailPrice": "1"}
    payload = {"Items": [valid, dict(valid), invalid], "NextPageLink": None}

    run = sync_retail_prices(
        snapshot_date=date(2026, 8, 20),
        region="westeurope",
        connector=FakeConnector(payload),
    )

    assert run.status == RetailPriceSyncRun.Status.COMPLETED
    assert run.raw_item_count == 3
    assert run.normalized_item_count == 2
    assert run.invalid_item_count == 1
    assert run.duplicate_item_count == 2
    assert RetailPriceSnapshot.objects.count() == 2
    assert set(
        RetailPriceSnapshot.objects.values_list("purchase_model", flat=True)
    ) == {"on_demand", "savings_plan"}
    raw_page = RetailPriceRawPage.objects.get()
    assert gzip.decompress(bytes(raw_page.payload_gzip)) == json.dumps(
        payload, separators=(",", ":")
    ).encode("utf-8")
    assert (
        RetailPriceSnapshot.objects.get(purchase_model="on_demand").data_residency_scope
        == "EU"
    )

    with pytest.raises(SnapshotAlreadyExists):
        sync_retail_prices(
            snapshot_date=date(2026, 8, 20),
            region="westeurope",
            connector=FakeConnector(payload),
        )
    assert RetailPriceSyncRun.objects.count() == 1


@pytest.mark.django_db
def test_sync_normalizes_non_compute_catalogue_items_without_arm_sku():
    payload = {"Items": [storage_item()], "NextPageLink": None}

    sync_retail_prices(
        snapshot_date=date(2026, 8, 18),
        region="westeurope",
        connector=FakeConnector(payload),
    )

    row = RetailPriceSnapshot.objects.get()
    assert row.provider_sku == "Hot LRS"
    assert row.service_category == "storage"
    assert row.resource_type == "storage"
    assert row.purchase_model == "on_demand"


@pytest.mark.django_db
def test_each_region_gets_its_own_completed_run_on_the_same_day():
    """A day's snapshot is assembled from one completed run per region.

    The old constraint allowed a single completed run per day, which is what
    forced the whole 208-page catalogue into one request. Chunking per region
    only works if same-day runs for different regions can both complete.
    """
    today = date(2026, 8, 21)
    west_payload = {"Items": [azure_item(region="westeurope")], "NextPageLink": None}
    north_payload = {"Items": [azure_item(region="northeurope")], "NextPageLink": None}

    west = sync_retail_prices(
        snapshot_date=today, region="westeurope", connector=FakeConnector(west_payload)
    )
    north = sync_retail_prices(
        snapshot_date=today,
        region="northeurope",
        connector=FakeConnector(north_payload),
    )

    assert west.status == RetailPriceSyncRun.Status.COMPLETED
    assert north.status == RetailPriceSyncRun.Status.COMPLETED
    assert west.region == "westeurope"
    assert north.region == "northeurope"
    # ``regions`` is kept in step for rows written before the region column.
    assert west.regions == ["westeurope"]
    assert RetailPriceSyncRun.objects.filter(snapshot_date=today).count() == 2

    # ...but the same region twice in one day is still refused.
    with pytest.raises(SnapshotAlreadyExists):
        sync_retail_prices(
            snapshot_date=today,
            region="westeurope",
            connector=FakeConnector(west_payload),
        )


@pytest.mark.django_db
def test_a_failing_region_does_not_discard_the_regions_that_worked():
    """One bad region must cost only that region, not the whole day."""
    payload = {"Items": [azure_item()], "NextPageLink": None}
    today = date(2026, 8, 22)

    class ExplodingConnector(FakeConnector):
        def iter_pages(self, *, regions, currency):
            raise RuntimeError("prices.azure.com is having a bad day")
            yield  # pragma: no cover - generator marker

    sync_retail_prices(
        snapshot_date=today, region="westeurope", connector=FakeConnector(payload)
    )
    with pytest.raises(RuntimeError):
        sync_retail_prices(
            snapshot_date=today,
            region="northeurope",
            connector=ExplodingConnector(payload),
        )

    assert RetailPriceSnapshot.objects.filter(snapshot_date=today).exists()
    statuses = dict(
        RetailPriceSyncRun.objects.filter(snapshot_date=today).values_list(
            "region", "status"
        )
    )
    assert statuses == {
        "westeurope": RetailPriceSyncRun.Status.COMPLETED,
        "northeurope": RetailPriceSyncRun.Status.FAILED,
    }


@pytest.mark.django_db
def test_command_syncs_only_the_requested_regions(mocker):
    """Covers the seam the webhook relies on: ``call_command(regions=[one])``.

    The view passes the region through as a management-command kwarg, so a
    regression here would silently sync all nineteen again.
    """
    payload = {"Items": [azure_item(region="northeurope")], "NextPageLink": None}
    mocker.patch(
        "power_up.finops.retail_prices.connectors.azure.AzureRetailPricesConnector",
        return_value=FakeConnector(payload),
    )

    call_command("sync_retail_prices", regions=["northeurope"], currency="EUR")

    runs = RetailPriceSyncRun.objects.all()
    assert [run.region for run in runs] == ["northeurope"]
    assert runs.get().status == RetailPriceSyncRun.Status.COMPLETED


@pytest.mark.django_db
def test_command_fails_loudly_when_a_region_fails(mocker):
    """The webhook turns a CommandError into a 500 so the timer alerts."""

    class ExplodingConnector(FakeConnector):
        def iter_pages(self, *, regions, currency):
            raise RuntimeError("prices.azure.com is having a bad day")
            yield  # pragma: no cover - generator marker

    mocker.patch(
        "power_up.finops.retail_prices.connectors.azure.AzureRetailPricesConnector",
        return_value=ExplodingConnector({}),
    )

    with pytest.raises(CommandError):
        call_command("sync_retail_prices", regions=["westeurope"], currency="EUR")

    assert RetailPriceSyncRun.objects.get().status == (
        RetailPriceSyncRun.Status.FAILED
    )


@pytest.mark.django_db
def test_reversing_the_region_migration_is_refused_once_regions_exist():
    """The reverse re-adds a constraint the per-region data would violate.

    Better to refuse up front with the offending days named than to die on the
    final operation with the migration half-applied.
    """
    from importlib import import_module

    from django.apps import apps as global_apps
    from django.db.migrations.exceptions import IrreversibleError

    guard = import_module(
        "power_up.finops.migrations.0009_retailpricesyncrun_region"
    )._refuse_reverse_once_regions_exist

    payload = {"Items": [azure_item(region="westeurope")], "NextPageLink": None}
    today = date(2026, 8, 23)

    # A single region a day is still reversible.
    sync_retail_prices(
        snapshot_date=today, region="westeurope", connector=FakeConnector(payload)
    )
    guard(global_apps, None)

    # A second region for the same day is not.
    sync_retail_prices(
        snapshot_date=today,
        region="northeurope",
        connector=FakeConnector(
            {"Items": [azure_item(region="northeurope")], "NextPageLink": None}
        ),
    )
    with pytest.raises(IrreversibleError) as excinfo:
        guard(global_apps, None)

    assert "2026-08-23" in str(excinfo.value)
    assert "2 runs" in str(excinfo.value)


@pytest.mark.django_db
def test_completed_runs_and_raw_pages_are_immutable():
    payload = {"Items": [azure_item()], "NextPageLink": None}
    run = sync_retail_prices(
        snapshot_date=date(2026, 8, 19),
        region="westeurope",
        connector=FakeConnector(payload),
    )
    run.error_message = "attempted rewrite"
    with pytest.raises(ValidationError):
        run.save()

    raw_page = run.raw_pages.get()
    raw_page.item_count = 999
    with pytest.raises(ValidationError):
        raw_page.save()

    price = run.prices.first()
    price.unit_price = "999"
    with pytest.raises(ValidationError):
        price.save()
