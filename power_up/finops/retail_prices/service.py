"""Archive and normalize immutable daily cloud retail-price snapshots."""

from __future__ import annotations

from datetime import date
import gzip
import hashlib

from django.db import transaction
from django.utils import timezone

from ..models import (
    CloudProvider,
    RetailPriceRawPage,
    RetailPriceSnapshot,
    RetailPriceSyncRun,
)
from .connectors.azure import (
    AzureRetailPricesConnector,
    DEFAULT_EUROPEAN_REGIONS,
)


class SnapshotAlreadyExists(RuntimeError):
    pass


def sync_retail_prices(
    *,
    provider: str = CloudProvider.AZURE,
    snapshot_date: date | None = None,
    currency: str = "EUR",
    regions: list[str] | None = None,
    connector=None,
) -> RetailPriceSyncRun:
    """Fetch, archive, and append one complete daily retail-price snapshot."""

    snapshot_date = snapshot_date or timezone.localdate()
    currency = currency.upper()
    regions = [region.lower() for region in (regions or DEFAULT_EUROPEAN_REGIONS)]

    if RetailPriceSyncRun.objects.filter(
        provider=provider,
        snapshot_date=snapshot_date,
        currency=currency,
        status=RetailPriceSyncRun.Status.COMPLETED,
    ).exists():
        raise SnapshotAlreadyExists(
            f"A completed {provider} {currency} snapshot already exists for "
            f"{snapshot_date}; history was not overwritten."
        )

    if connector is None:
        if provider != CloudProvider.AZURE:
            raise ValueError(f"No retail price connector is configured for {provider}.")
        connector = AzureRetailPricesConnector()

    run = RetailPriceSyncRun.objects.create(
        provider=provider,
        snapshot_date=snapshot_date,
        currency=currency,
        regions=regions,
        connector_name=connector.name,
        source_endpoint=connector.endpoint,
    )

    normalized_rows = []
    seen_keys = set()
    invalid_count = 0
    duplicate_count = 0
    raw_item_count = 0
    page_count = 0

    try:
        for page_count, page in enumerate(
            connector.iter_pages(regions=regions, currency=currency), start=1
        ):
            items = page.payload["Items"]
            raw_item_count += len(items)
            raw_page = RetailPriceRawPage.objects.create(
                sync_run=run,
                page_number=page_count,
                source_url=page.source_url,
                next_page_url=page.next_page_url,
                response_sha256=hashlib.sha256(page.raw_content).hexdigest(),
                payload_gzip=gzip.compress(page.raw_content),
                item_count=len(items),
            )
            for item_index, item in enumerate(items):
                try:
                    rows = connector.normalize_item(
                        item=item,
                        snapshot_date=snapshot_date,
                        run=run,
                        raw_page=raw_page,
                        item_index=item_index,
                    )
                except (ValueError, TypeError):
                    invalid_count += 1
                    continue
                for row in rows:
                    if row.price_key in seen_keys:
                        duplicate_count += 1
                        continue
                    seen_keys.add(row.price_key)
                    normalized_rows.append(row)

        with transaction.atomic():
            RetailPriceSnapshot.objects.bulk_create(normalized_rows, batch_size=1000)
            run.page_count = page_count
            run.raw_item_count = raw_item_count
            run.normalized_item_count = len(normalized_rows)
            run.invalid_item_count = invalid_count
            run.duplicate_item_count = duplicate_count
            run.status = RetailPriceSyncRun.Status.COMPLETED
            run.completed_at = timezone.now()
            run.save(
                update_fields=[
                    "page_count",
                    "raw_item_count",
                    "normalized_item_count",
                    "invalid_item_count",
                    "duplicate_item_count",
                    "status",
                    "completed_at",
                ]
            )
    except Exception as exc:
        run.page_count = page_count
        run.raw_item_count = raw_item_count
        run.invalid_item_count = invalid_count
        run.duplicate_item_count = duplicate_count
        run.status = RetailPriceSyncRun.Status.FAILED
        run.completed_at = timezone.now()
        run.error_message = str(exc)[:4000]
        run.save(
            update_fields=[
                "page_count",
                "raw_item_count",
                "invalid_item_count",
                "duplicate_item_count",
                "status",
                "completed_at",
                "error_message",
            ]
        )
        raise

    return run
