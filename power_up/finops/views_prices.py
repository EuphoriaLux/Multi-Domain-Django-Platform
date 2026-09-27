"""Login-protected public retail price intelligence views."""

import hashlib
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal
from statistics import median

from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.db.models import Count, Max, Min, Q, Sum
from django.shortcuts import render
from django.utils import timezone

from .models import RetailPriceSnapshot, RetailPriceSyncRun
from .retail_prices.connectors.azure import EUROPEAN_AZURE_REGIONS

PERIOD_OPTIONS = {30, 90, 180, 365}
# The option lists change once a night, when the sync lands a new day.
OPTIONS_CACHE_SECONDS = 60 * 60
# The region every other region's price index is measured against.
INDEX_REFERENCE_REGION = "westeurope"
# Filter values the dashboard offers. The region index is cached per filter
# combination, so anything else is refused rather than minting a cache entry.
PRICE_TYPES = {"", "Consumption", "Reservation", "DevTestConsumption", "SavingsPlan"}
PURCHASE_MODELS = {"", "on_demand", "spot", "reservation", "savings_plan", "dev_test"}


def _safe_period(value):
    try:
        days = int(value)
    except (TypeError, ValueError):
        return 90
    return days if days in PERIOD_OPTIONS else 90


def _latest_day_options(provider, latest_day):
    """Dropdown values, read from the newest snapshot day only.

    The table grows by one full European VM catalogue per night, so a DISTINCT
    over its whole history ran for minutes on production and the page never
    rendered. One day holds every current SKU, OS, currency and region.

    The key holds only the provider and that day, both taken from stored rows
    (no rows, no key), so crafted query strings cannot multiply cache entries.
    Currency and OS narrow the SKU list in Python instead.
    """
    key = f"finops:prices:options:v2:{provider}:{latest_day}"
    options = cache.get(key)
    if options is not None:
        return options

    day = RetailPriceSnapshot.objects.filter(
        provider=provider, snapshot_date=latest_day
    ).order_by()
    vms = day.filter(service_category="compute", resource_type="virtual_machines")
    options = {
        "vm_skus": list(
            vms.values_list(
                "currency", "operating_system", "provider_sku", "product_name"
            )
            .distinct()
            .order_by("provider_sku", "currency", "operating_system", "product_name")
        ),
        "os": list(
            vms.exclude(operating_system="")
            .values_list("operating_system", flat=True)
            .distinct()
            .order_by("operating_system")
        ),
        "currencies": list(
            day.values_list("currency", flat=True).distinct().order_by("currency")
        ),
        # Without the explicit order_by() the model's Meta.ordering joins the
        # DISTINCT, which then runs over (region, date, sku) instead of region.
        "regions": set(day.values_list("region_code", flat=True).distinct()),
    }
    cache.set(key, options, OPTIONS_CACHE_SECONDS)
    return options


# A like-for-like offer: same VM, OS/licensing, meter and commercial terms.
# Without the offer dimensions, "All" price types would divide one region's
# savings-plan price by the reference's on-demand price.
INDEX_OFFER_FIELDS = (
    "provider_sku",
    "product_name",
    "meter_name",
    "price_type",
    "purchase_model",
    "term",
    "unit_of_measure",
)


def _region_price_index(scope, region_days):
    """Each region's median price relative to the reference region.

    Every region is read on its own latest snapshot day (``region_days``), so a
    region the morning sync has not reached yet keeps yesterday's prices
    instead of dropping out, or leaving a partial set measured against a
    stand-in reference. Only like-for-like offers count, compared pairwise
    with the reference, not as an intersection across every region, so a
    region with a small catalogue does not shrink everyone's basket.
    """
    day_filter = Q()
    for code, day in region_days.items():
        day_filter |= Q(region_code=code, snapshot_date=day)
    prices = defaultdict(dict)
    locations = {}
    for row in (
        scope.filter(day_filter)
        .order_by()
        .values("region_code", "location_name", *INDEX_OFFER_FIELDS)
        .annotate(price=Min("unit_price"))
    ):
        if not row["price"]:
            continue  # Zero-priced meters would divide by zero or skew ratios.
        key = tuple(row[field] for field in INDEX_OFFER_FIELDS)
        prices[row["region_code"]][key] = row["price"]
        locations[row["region_code"]] = EUROPEAN_AZURE_REGIONS.get(
            row["region_code"], {}
        ).get("label", row["location_name"] or row["region_code"])
    if not prices:
        return [], ""

    reference = (
        INDEX_REFERENCE_REGION
        if INDEX_REFERENCE_REGION in prices
        else max(sorted(prices), key=lambda code: len(prices[code]))
    )
    reference_prices = prices[reference]
    index = []
    for code, region_prices in prices.items():
        ratios = [
            price / reference_prices[key]
            for key, price in region_prices.items()
            if key in reference_prices
        ]
        if not ratios:
            continue
        value = float(median(ratios) * 100)
        index.append(
            {
                "region_code": code,
                "location_name": locations[code],
                "index": round(value, 1),
                "difference_percent": round(value - 100, 1),
                "compared": len(ratios),
                "is_reference": code == reference,
                "snapshot_date": region_days[code],
            }
        )
    index.sort(key=lambda item: (item["index"], item["region_code"]))
    return index, reference


@login_required
def retail_price_dashboard(request):
    """Compare equivalent Azure VM offers across selected European regions."""

    provider = request.GET.get("provider", "azure")
    currency = request.GET.get("currency", "EUR").upper()
    period = _safe_period(request.GET.get("period", 90))
    end_date = timezone.localdate()
    start_date = end_date - timedelta(days=period - 1)

    if "region" in request.GET:
        # dict.fromkeys de-duplicates while keeping the submitted order.
        selected_regions = list(
            dict.fromkeys(value for value in request.GET.getlist("region") if value)
        )
    else:
        selected_regions = list(EUROPEAN_AZURE_REGIONS)

    price_type = request.GET.get("price_type", "Consumption")
    purchase_model = request.GET.get("purchase_model", "on_demand")
    product_filter = request.GET.get("product", "").strip()
    os_filter = request.GET.get("os", "").strip()

    base = RetailPriceSnapshot.objects.filter(
        provider=provider,
        currency=currency,
        service_category="compute",
        resource_type="virtual_machines",
        snapshot_date__gte=start_date,
        snapshot_date__lte=end_date,
    )
    if selected_regions:
        base = base.filter(region_code__in=selected_regions)
    if price_type:
        base = base.filter(price_type=price_type)
    if purchase_model:
        base = base.filter(purchase_model=purchase_model)
    if product_filter:
        base = base.filter(product_name__icontains=product_filter)
    if os_filter:
        base = base.filter(operating_system=os_filter)

    latest_day = (
        RetailPriceSnapshot.objects.filter(provider=provider)
        .order_by()
        .aggregate(value=Max("snapshot_date"))["value"]
    )
    empty_options = {"vm_skus": [], "os": [], "currencies": [], "regions": set()}
    options = _latest_day_options(provider, latest_day) if latest_day else empty_options
    offer_choices = [
        (sku, product)
        for sku_currency, sku_os, sku, product in options["vm_skus"]
        if sku_currency == currency and (not os_filter or sku_os == os_filter)
    ]
    sku_choices = sorted({sku for sku, _ in offer_choices})

    requested_sku = request.GET.get("sku", "").strip()
    if requested_sku:
        # The SKU box is free text. Resolve the casing here so the lookups
        # below stay exact: iexact compiles to UPPER(provider_sku) on
        # Postgres, which skips the (provider, provider_sku, date) index.
        # An exact match (a retired SKU included) is an index probe; otherwise
        # the newest day's SKUs supply the stored casing.
        if RetailPriceSnapshot.objects.filter(
            provider=provider, provider_sku=requested_sku
        ).exists():
            active_sku = requested_sku
        else:
            canonical = {sku.lower(): sku for _, _, sku, _ in options["vm_skus"]}
            active_sku = canonical.get(requested_sku.lower(), requested_sku)
    else:
        active_sku = ""

    # Without a SKU the page compares whole regions instead: a like-for-like
    # price index for one day, never a scan of the full history.
    region_index, index_reference, index_day = [], "", None
    # The product box is free text. For the index it resolves to the known
    # product names it matches, so every substring naming the same products
    # shares one cache entry and the query is an exact IN, not a LIKE.
    index_products = (
        sorted(
            {
                product
                for _, product in offer_choices
                if product_filter.lower() in product.lower()
            }
        )
        if product_filter
        else []
    )
    # Currencies come from the sync runs, not the newest day: a day the sync
    # has only started may not carry every currency yet.
    synced_currencies = set(
        RetailPriceSyncRun.objects.filter(
            provider=provider, status=RetailPriceSyncRun.Status.COMPLETED
        )
        .order_by()
        .values_list("currency", flat=True)
        .distinct()
    )
    index_filters_valid = (
        currency in synced_currencies
        and (not os_filter or os_filter in options["os"])
        and price_type in PRICE_TYPES
        and purchase_model in PURCHASE_MODELS
        and (not product_filter or index_products)
    )
    if not active_sku and latest_day and index_filters_valid:
        # Each region's own latest day in the requested currency: one MAX per
        # known region (never per submitted value) on the (provider,
        # region_code, date) index.
        region_days = {}
        for code in sorted(set(EUROPEAN_AZURE_REGIONS) | options["regions"]):
            day = (
                RetailPriceSnapshot.objects.filter(
                    provider=provider, region_code=code, currency=currency
                )
                .order_by()
                .aggregate(value=Max("snapshot_date"))["value"]
            )
            if day and start_date <= day <= end_date:
                region_days[code] = day
        # A region's index is pairwise against the reference, so while the
        # reference is selected the index does not depend on the other
        # selected regions: build it once for all regions and cache it until
        # any region's snapshot day moves. Grouping the daily catalogue is too
        # heavy to repeat on every page load. Without the reference, the
        # selection must pick its own, so only selected regions take part.
        if selected_regions and INDEX_REFERENCE_REGION not in selected_regions:
            region_days = {
                code: day
                for code, day in region_days.items()
                if code in selected_regions
            }
        signature = repr(
            (
                provider,
                currency,
                os_filter,
                price_type,
                purchase_model,
                index_products,
                sorted(region_days.items()),
            )
        )
        key = "finops:prices:index:v1:" + hashlib.sha256(signature.encode()).hexdigest()
        cached = cache.get(key)
        if cached is None:
            index_scope = RetailPriceSnapshot.objects.filter(
                provider=provider,
                currency=currency,
                service_category="compute",
                resource_type="virtual_machines",
            )
            if price_type:
                index_scope = index_scope.filter(price_type=price_type)
            if purchase_model:
                index_scope = index_scope.filter(purchase_model=purchase_model)
            if index_products:
                index_scope = index_scope.filter(product_name__in=index_products)
            if os_filter:
                index_scope = index_scope.filter(operating_system=os_filter)
            cached = (
                _region_price_index(index_scope, region_days)
                if region_days
                else ([], "")
            )
            if cached[0]:
                cache.set(key, cached, OPTIONS_CACHE_SECONDS)
        all_regions_index, index_reference = cached
        region_index = [
            item
            for item in all_regions_index
            if not selected_regions or item["region_code"] in selected_regions
        ]
        if region_index:
            index_day = max(item["snapshot_date"] for item in region_index)
    indexed_codes = {item["region_code"] for item in region_index}
    index_missing_regions = [
        EUROPEAN_AZURE_REGIONS.get(code, {}).get("label", code)
        for code in selected_regions
        if code not in indexed_codes
    ]

    # Never fall back to every SKU at once: that is the full-window scan this
    # page timed out on, and it would compare unlike offers anyway.
    filtered = base.filter(provider_sku=active_sku) if active_sku else base.none()

    latest_snapshot = (
        filtered.aggregate(value=Max("snapshot_date"))["value"]
        if active_sku
        else index_day
    )
    chart_rows = list(
        filtered.values("snapshot_date", "region_code", "location_name")
        .annotate(unit_price=Min("unit_price"))
        .order_by("snapshot_date", "region_code")
    )
    series = defaultdict(list)
    series_labels = {}
    for row in chart_rows:
        region_code = row["region_code"]
        series_labels[region_code] = row["location_name"] or region_code
        series[region_code].append(
            {
                "x": row["snapshot_date"].isoformat(),
                "y": float(row["unit_price"]),
            }
        )
    chart_series = [
        {"label": series_labels[code], "data": values}
        for code, values in sorted(series.items())
    ]
    # The x axis is a category scale: it needs every snapshot day, in order,
    # as its labels. Without them Chart.js cannot place the {x, y} points.
    chart_labels = sorted({row["snapshot_date"].isoformat() for row in chart_rows})

    region_comparison = []
    if latest_snapshot:
        comparison_rows = (
            filtered.filter(snapshot_date=latest_snapshot)
            .values(
                "region_code",
                "location_name",
                "data_residency_scope",
                "currency",
                "unit_of_measure",
            )
            .annotate(unit_price=Min("unit_price"))
            .order_by("unit_price", "region_code")
        )
        region_comparison = [
            {
                **row,
                "unit_price": float(row["unit_price"]),
            }
            for row in comparison_rows
        ]

    history_rows = list(
        filtered.select_related("sync_run").order_by(
            "-snapshot_date", "region_code", "product_name"
        )[:200]
    )
    history_keys = {row.price_key for row in history_rows}
    history_by_key = defaultdict(list)
    if history_keys:
        # Only price_key/snapshot_date/unit_price are read below -- .only()
        # keeps the SELECT to those columns instead of the full row.
        previous_candidates = (
            RetailPriceSnapshot.objects.filter(
                price_key__in=history_keys,
                snapshot_date__gte=start_date - timedelta(days=365),
            )
            .only("price_key", "snapshot_date", "unit_price")
            .order_by("price_key", "-snapshot_date")
        )
        for candidate in previous_candidates:
            history_by_key[candidate.price_key].append(candidate)

    changed_count = increased_count = decreased_count = 0
    for row in history_rows:
        previous = next(
            (
                candidate
                for candidate in history_by_key[row.price_key]
                if candidate.snapshot_date < row.snapshot_date
            ),
            None,
        )
        row.previous_unit_price = previous.unit_price if previous else None
        row.change_percent = None
        row.change_percent_abs = None
        row.change_direction = "new"
        if previous and previous.unit_price:
            change = (
                (row.unit_price - previous.unit_price) / previous.unit_price
            ) * Decimal("100")
            row.change_percent = change.quantize(Decimal("0.01"))
            # The template prefixes an explicit +/- arrow, so the magnitude
            # shown alongside it must not carry its own redundant sign
            # (e.g. "▼ -16.67%").
            row.change_percent_abs = abs(row.change_percent)
            if change > 0:
                row.change_direction = "up"
                increased_count += 1
                changed_count += 1
            elif change < 0:
                row.change_direction = "down"
                decreased_count += 1
                changed_count += 1
            else:
                row.change_direction = "same"

    captured_region_codes = options["regions"]
    region_codes = sorted(set(EUROPEAN_AZURE_REGIONS) | captured_region_codes)
    region_options = [
        {
            "code": code,
            "label": EUROPEAN_AZURE_REGIONS.get(code, {}).get("label", code),
            "scope": EUROPEAN_AZURE_REGIONS.get(code, {}).get(
                "data_residency_scope", ""
            ),
            "restricted_access": EUROPEAN_AZURE_REGIONS.get(code, {}).get(
                "restricted_access", False
            ),
            "has_snapshot": code in captured_region_codes,
        }
        for code in region_codes
    ]
    sku_options = sku_choices[:500]
    # Without a SKU, the newest day's products still help narrow the index.
    product_options = sorted({product for _, product in offer_choices})[:200]
    if active_sku:
        product_options_query = RetailPriceSnapshot.objects.filter(
            provider=provider,
            currency=currency,
            service_category="compute",
            resource_type="virtual_machines",
            provider_sku=active_sku,
        )
        if os_filter:
            product_options_query = product_options_query.filter(
                operating_system=os_filter
            )
        product_options = list(
            product_options_query.values_list("product_name", flat=True)
            .distinct()
            .order_by("product_name")[:200]
        )
    os_options = options["os"]
    currency_options = options["currencies"] or ["EUR"]
    latest_sync = RetailPriceSyncRun.objects.filter(
        provider=provider,
        currency=currency,
        status=RetailPriceSyncRun.Status.COMPLETED,
    ).first()
    # A day's snapshot is one run per region, so the newest run alone would
    # under-report the day by roughly a factor of nineteen.
    latest_sync_totals = {"rows": 0, "regions": 0}
    if latest_sync is not None:
        latest_sync_totals = RetailPriceSyncRun.objects.filter(
            provider=provider,
            currency=currency,
            status=RetailPriceSyncRun.Status.COMPLETED,
            snapshot_date=latest_sync.snapshot_date,
        ).aggregate(
            rows=Sum("normalized_item_count"),
            regions=Count("id"),
        )

    return render(
        request,
        "finops/retail_price_dashboard.html",
        {
            "page_title": "Power Hub European Azure Price Tracker",
            "provider": provider,
            "currency": currency,
            "currency_options": currency_options,
            "period": period,
            "selected_regions": selected_regions,
            "region_options": region_options,
            "sku_options": sku_options,
            "active_sku": active_sku,
            "product_filter": product_filter,
            "product_options": product_options,
            "os_filter": os_filter,
            "os_options": os_options,
            "price_type": price_type,
            "purchase_model": purchase_model,
            "latest_snapshot": latest_snapshot,
            "latest_sync": latest_sync,
            "latest_sync_rows": latest_sync_totals["rows"] or 0,
            "latest_sync_regions": latest_sync_totals["regions"] or 0,
            "chart_series": chart_series,
            "chart_labels": chart_labels,
            "region_index": region_index,
            "index_reference": EUROPEAN_AZURE_REGIONS.get(index_reference, {}).get(
                "label", index_reference
            ),
            "index_day": index_day,
            "index_missing_regions": index_missing_regions,
            "region_comparison": region_comparison,
            "history_rows": history_rows,
            "changed_count": changed_count,
            "increased_count": increased_count,
            "decreased_count": decreased_count,
        },
    )
