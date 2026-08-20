from datetime import date
import gzip
import json

import pytest
from django.core.exceptions import ValidationError

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


def azure_item(price="0.10000000"):
    return {
        "currencyCode": "EUR",
        "retailPrice": price,
        "armRegionName": "westeurope",
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
            connector=FakeConnector(payload),
        )
    assert RetailPriceSyncRun.objects.count() == 1


@pytest.mark.django_db
def test_completed_runs_and_raw_pages_are_immutable():
    payload = {"Items": [azure_item()], "NextPageLink": None}
    run = sync_retail_prices(
        snapshot_date=date(2026, 8, 19),
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
