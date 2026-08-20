"""Login-protected public retail price intelligence views."""

from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.db.models import Count, Max, Min
from django.shortcuts import render
from django.utils import timezone

from .models import RetailPriceSnapshot, RetailPriceSyncRun
from .retail_prices.connectors.azure import EUROPEAN_AZURE_REGIONS

PERIOD_OPTIONS = {30, 90, 180, 365}


def _safe_period(value):
    try:
        days = int(value)
    except (TypeError, ValueError):
        return 90
    return days if days in PERIOD_OPTIONS else 90


@login_required
def retail_price_dashboard(request):
    """Compare equivalent Azure VM offers across selected European regions."""

    provider = request.GET.get("provider", "azure")
    currency = request.GET.get("currency", "EUR").upper()
    period = _safe_period(request.GET.get("period", 90))
    end_date = timezone.localdate()
    start_date = end_date - timedelta(days=period - 1)

    if "region" in request.GET:
        selected_regions = [value for value in request.GET.getlist("region") if value]
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

    requested_sku = request.GET.get("sku", "").strip()
    if requested_sku:
        active_sku = requested_sku
    else:
        default_sku = (
            base.values("provider_sku")
            .annotate(region_count=Count("region_code", distinct=True))
            .order_by("-region_count", "provider_sku")
            .first()
        )
        active_sku = default_sku["provider_sku"] if default_sku else ""

    filtered = base
    if active_sku:
        filtered = filtered.filter(provider_sku__iexact=active_sku)

    latest_snapshot = filtered.aggregate(value=Max("snapshot_date"))["value"]
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
        previous_candidates = RetailPriceSnapshot.objects.filter(
            price_key__in=history_keys,
            snapshot_date__gte=start_date - timedelta(days=365),
        ).order_by("price_key", "-snapshot_date")
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
        row.change_direction = "new"
        if previous and previous.unit_price:
            change = (
                (row.unit_price - previous.unit_price) / previous.unit_price
            ) * Decimal("100")
            row.change_percent = change.quantize(Decimal("0.01"))
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

    captured_region_codes = set(
        RetailPriceSnapshot.objects.filter(provider=provider)
        .values_list("region_code", flat=True)
        .distinct()
    )
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
    sku_options_query = RetailPriceSnapshot.objects.filter(
        provider=provider,
        currency=currency,
        service_category="compute",
        resource_type="virtual_machines",
    )
    if os_filter:
        sku_options_query = sku_options_query.filter(operating_system=os_filter)
    sku_options = list(
        sku_options_query
        .values_list("provider_sku", flat=True)
        .distinct()
        .order_by("provider_sku")[:500]
    )
    product_options_query = RetailPriceSnapshot.objects.filter(
        provider=provider,
        currency=currency,
        service_category="compute",
        resource_type="virtual_machines",
    )
    if active_sku:
        product_options_query = product_options_query.filter(
            provider_sku__iexact=active_sku
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
    os_options = list(
        RetailPriceSnapshot.objects.filter(
            provider=provider,
            service_category="compute",
            resource_type="virtual_machines",
        )
        .exclude(operating_system="")
        .values_list("operating_system", flat=True)
        .distinct()
        .order_by("operating_system")
    )
    currency_options = list(
        RetailPriceSnapshot.objects.filter(provider=provider)
        .values_list("currency", flat=True)
        .distinct()
        .order_by("currency")
    ) or ["EUR"]
    latest_sync = RetailPriceSyncRun.objects.filter(
        provider=provider,
        currency=currency,
        status=RetailPriceSyncRun.Status.COMPLETED,
    ).first()

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
            "chart_series": chart_series,
            "region_comparison": region_comparison,
            "history_rows": history_rows,
            "changed_count": changed_count,
            "increased_count": increased_count,
            "decreased_count": decreased_count,
        },
    )
