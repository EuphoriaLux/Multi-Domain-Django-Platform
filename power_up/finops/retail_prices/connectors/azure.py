"""Azure Retail Prices connector for the European public price catalogue."""

from collections.abc import Iterable
from decimal import Decimal, InvalidOperation
import hashlib
import json

import requests
from django.utils.dateparse import parse_datetime
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ...models import CloudProvider, RetailPriceSnapshot
from .base import ConnectorPage, RetailPriceConnector

EUROPEAN_AZURE_REGIONS = {
    "austriaeast": {"label": "Austria East", "data_residency_scope": "EU"},
    "belgiumcentral": {"label": "Belgium Central", "data_residency_scope": "EU"},
    "denmarkeast": {"label": "Denmark East", "data_residency_scope": "EU"},
    "francecentral": {"label": "France Central", "data_residency_scope": "EU"},
    "francesouth": {"label": "France South", "data_residency_scope": "EU", "restricted_access": True},
    "germanynorth": {"label": "Germany North", "data_residency_scope": "EU", "restricted_access": True},
    "germanywestcentral": {"label": "Germany West Central", "data_residency_scope": "EU"},
    "italynorth": {"label": "Italy North", "data_residency_scope": "EU"},
    "northeurope": {"label": "North Europe", "data_residency_scope": "EU"},
    "norwayeast": {"label": "Norway East", "data_residency_scope": "Europe (non-EU)"},
    "norwaywest": {"label": "Norway West", "data_residency_scope": "Europe (non-EU)", "restricted_access": True},
    "polandcentral": {"label": "Poland Central", "data_residency_scope": "EU"},
    "spaincentral": {"label": "Spain Central", "data_residency_scope": "EU"},
    "swedencentral": {"label": "Sweden Central", "data_residency_scope": "EU"},
    "switzerlandnorth": {"label": "Switzerland North", "data_residency_scope": "Europe (non-EU)"},
    "switzerlandwest": {"label": "Switzerland West", "data_residency_scope": "Europe (non-EU)", "restricted_access": True},
    "uksouth": {"label": "UK South", "data_residency_scope": "Europe (non-EU)"},
    "ukwest": {"label": "UK West", "data_residency_scope": "Europe (non-EU)"},
    "westeurope": {"label": "West Europe", "data_residency_scope": "EU"},
}

DEFAULT_EUROPEAN_REGIONS = list(EUROPEAN_AZURE_REGIONS)


class _BoundedRetry(Retry):
    """A ``Retry`` whose ``Retry-After`` sleep cannot outlast the caller.

    urllib3 sleeps for the server-supplied duration verbatim; nothing in the
    timeout configuration constrains it. Capping it keeps a throttled upstream
    from parking a web worker for minutes inside a request that the scheduler
    has already stopped waiting on.
    """

    #: Longest ``Retry-After`` this client will honour before giving up.
    #:
    #: Waiting minutes inside a request nobody is reading any more buys
    #: nothing — the region is re-attempted on the next daily snapshot either
    #: way — while parking a web worker for the whole duration.
    max_retry_after: float = 10

    def get_retry_after(self, response):
        retry_after = super().get_retry_after(response)
        if retry_after is None:
            return None
        return min(retry_after, self.max_retry_after)

    def new(self, **kw):
        # urllib3 rebuilds the Retry on every increment and only carries its
        # own known kwargs across, so the cap has to be re-attached by hand or
        # it silently reverts to the class default mid-sequence.
        retry = super().new(**kw)
        retry.max_retry_after = self.max_retry_after
        return retry


class AzureRetailPricesConnector(RetailPriceConnector):
    provider = "azure"
    name = "Azure Retail Prices API"
    endpoint = "https://prices.azure.com/api/retail/prices"
    api_version = "2023-01-01-preview"

    #: Read timeout for a single page request.
    #:
    #: Kept deliberately tight, along with the retry budget below. The caller
    #: is an HTTP request that a scheduler is waiting on, so the worst case for
    #: one page has to stay well under that client's timeout: otherwise the
    #: client gives up, moves to the next region, and leaves this worker still
    #: fetching. A handful of those and the site has no workers left. Worst
    #: case here is 2 attempts x (5s connect + 20s read) + 0.5s backoff, so
    #: about 50s.
    DEFAULT_TIMEOUT = 20
    CONNECT_TIMEOUT = 5

    def __init__(self, *, timeout: int = DEFAULT_TIMEOUT, session=None):
        self.timeout = timeout
        self.session = session or self._build_session()

    #: Single source of truth for the cap; see :class:`_BoundedRetry`.
    #:
    #: ``respect_retry_after_header`` makes urllib3 sleep for whatever the
    #: server asks, and that sleep is bounded by nothing — not the connect
    #: timeout, not the read timeout, not the backoff cap. A
    #: ``Retry-After: 300`` from a throttled ``prices.azure.com`` would blow
    #: straight through the region budget and the caller's timeout alike,
    #: which is the exact worker-abandonment this class exists to prevent.
    MAX_RETRY_AFTER = _BoundedRetry.max_retry_after

    @staticmethod
    def _build_session():
        retry = _BoundedRetry(
            # One retry, not three. A retry storm here used to be able to run
            # ~227s for a single page. Losing a region to a transient 429 is
            # cheap now that runs are per-region: it is reported by name and
            # re-attempted on the next daily snapshot.
            total=1,
            backoff_factor=0.5,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET",),
            respect_retry_after_header=True,
        )
        session = requests.Session()
        session.mount("https://", HTTPAdapter(max_retries=retry))
        session.headers.update({"User-Agent": "Power-Hub-Retail-Price-Tracker/1.0"})
        return session

    def iter_pages(
        self, *, regions: list[str], currency: str
    ) -> Iterable[ConnectorPage]:
        for region in regions:
            yield from self._iter_region_pages(region=region, currency=currency)

    def _iter_region_pages(
        self, *, region: str, currency: str
    ) -> Iterable[ConnectorPage]:
        url = self.endpoint
        params = {
            "api-version": self.api_version,
            "currencyCode": f"'{currency.upper()}'",
            # MVP scope is deliberately limited to public VM Compute prices.
            "$filter": (
                f"armRegionName eq '{region}' "
                "and serviceName eq 'Virtual Machines'"
            ),
        }

        while url:
            response = self.session.get(
                url,
                params=params,
                timeout=(self.CONNECT_TIMEOUT, self.timeout),
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload.get("Items"), list):
                raise ValueError("Azure Retail Prices response has no Items list.")
            next_url = payload.get("NextPageLink") or ""
            yield ConnectorPage(
                source_url=response.url,
                next_page_url=next_url,
                payload=payload,
                raw_content=response.content,
            )
            url = next_url
            params = None

    def normalize_item(self, *, item, snapshot_date, run, raw_page, item_index):
        required = {
            "armRegionName": item.get("armRegionName"),
            "currencyCode": item.get("currencyCode"),
            "productName": item.get("productName"),
            "meterName": item.get("meterName"),
            "unitOfMeasure": item.get("unitOfMeasure"),
        }
        if not all(required.values()):
            raise ValueError(f"Missing required fields: {required}")

        try:
            base_price = Decimal(str(item.get("retailPrice")))
        except (InvalidOperation, TypeError):
            raise ValueError("retailPrice is not a decimal") from None
        if base_price < 0:
            raise ValueError("retailPrice cannot be negative")

        price_type = item.get("type") or item.get("priceType") or "Unknown"
        effective = parse_datetime(item.get("effectiveStartDate") or "")
        region = item["armRegionName"].lower()
        region_metadata = EUROPEAN_AZURE_REGIONS.get(region, {})
        service_name = item.get("serviceName") or "Azure service"
        service_family = item.get("serviceFamily") or "Other"
        provider_sku = (
            item.get("armSkuName")
            or item.get("skuName")
            or item.get("productId")
            or item["meterName"]
        )
        base_values = {
            "provider": CloudProvider.AZURE,
            "snapshot_date": snapshot_date,
            "sync_run": run,
            "source_page": raw_page,
            "source_item_index": item_index,
            "service_category": _service_category(service_family),
            "resource_type": _resource_type(service_name),
            "provider_sku": provider_sku,
            "sku_name": item.get("skuName") or "",
            "instance_family": item.get("productName") or "",
            "product_name": item["productName"],
            "meter_name": item["meterName"],
            "operating_system": _operating_system(item),
            "region_code": region,
            "location_name": item.get("location") or region_metadata.get("label", ""),
            "data_residency_scope": region_metadata.get("data_residency_scope", ""),
            "currency": item["currencyCode"].upper(),
            "price_type": price_type,
            "purchase_model": _purchase_model(item, price_type),
            "term": item.get("reservationTerm") or "",
            "unit_price": base_price,
            "unit_of_measure": item["unitOfMeasure"],
            "effective_start_date": effective,
            "provider_item_id": item.get("skuId") or "",
            "provider_product_id": item.get("productId") or item.get("productid") or "",
            "provider_meter_id": item.get("meterId") or "",
            "is_primary_meter": item.get("isPrimaryMeterRegion"),
        }

        variants = [base_values]
        for saving_plan in item.get("savingsPlan") or []:
            try:
                saving_price = Decimal(str(saving_plan.get("retailPrice")))
            except (InvalidOperation, TypeError):
                continue
            if saving_price < 0:
                continue
            variants.append(
                {
                    **base_values,
                    "price_type": "SavingsPlan",
                    "purchase_model": "savings_plan",
                    "term": saving_plan.get("term") or "",
                    "unit_price": saving_price,
                }
            )

        rows = []
        for values in variants:
            values["price_key"] = _price_key(values)
            rows.append(RetailPriceSnapshot(**values))
        return rows


def _operating_system(item: dict) -> str:
    value = f"{item.get('productName', '')} {item.get('meterName', '')}".lower()
    if "windows" in value:
        return "Windows"
    if "red hat" in value or " rhel" in value:
        return "Red Hat Enterprise Linux"
    if "suse" in value:
        return "SUSE Linux"
    if "ubuntu" in value or "linux" in value:
        return "Linux"
    return "Unspecified"


def _purchase_model(item: dict, price_type: str) -> str:
    value = f"{item.get('meterName', '')} {item.get('skuName', '')}".lower()
    if price_type == "Reservation":
        return "reservation"
    if price_type == "DevTestConsumption":
        return "dev_test"
    if "spot" in value or "low priority" in value:
        return "spot"
    return "on_demand"


def _service_category(service_family: str) -> str:
    return service_family.lower().replace(" ", "_")[:50]


def _resource_type(service_name: str) -> str:
    return service_name.lower().replace(" ", "_")[:50]


def _price_key(values: dict) -> str:
    identity = {
        key: values.get(key, "")
        for key in (
            "provider",
            "service_category",
            "resource_type",
            "provider_sku",
            "sku_name",
            "product_name",
            "meter_name",
            "provider_product_id",
            "provider_meter_id",
            "region_code",
            "currency",
            "price_type",
            "purchase_model",
            "term",
            "unit_of_measure",
            "effective_start_date",
        )
    }
    encoded = json.dumps(identity, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
