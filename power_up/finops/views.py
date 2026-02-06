# power_up/finops/views.py
"""
FinOps Hub Views - Azure Cost Dashboard

Dashboard views for cost management and analytics.
"""

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib import messages
from django.http import JsonResponse
from django.utils.translation import gettext as _
from django.utils import timezone
from django.core.cache import cache
from django.db.models import Sum, Count, Min, Max, Q
from django.db.models.functions import TruncDate
from django.core.serializers.json import DjangoJSONEncoder
from datetime import timedelta
from decimal import Decimal
import json

from .models import CostExport, CostRecord, CostAggregation, ReservationCost, CostAnomaly
from .forms import SubscriptionIDForm
from .utils.date_helpers import resolve_date_range


def is_admin(user):
    """Check if user is staff/admin"""
    return user.is_staff or user.is_superuser


def _build_json_config(page, **kwargs):
    """Build JSON config dict for finops_dashboard.js auto-init."""
    config = {'page': page}
    config.update(kwargs)
    return json.dumps(config, cls=DjangoJSONEncoder)


def dashboard(request):
    """Main FinOps dashboard with cost overview and filtering"""
    subscription_filter = request.GET.get('subscription')
    service_filter = request.GET.get('service')
    charge_type_filter = request.GET.get('charge_type', 'payg')

    # Resolve date range using shared helper
    date_info = resolve_date_range(request.GET, use_month_filter=True)
    start_date = date_info['start_date']
    end_date = date_info['end_date']
    days = date_info['days']
    month_filter = date_info['month_filter']

    base_queryset = CostRecord.objects.annotate(
        usage_date=TruncDate('charge_period_start')
    ).filter(
        usage_date__gte=start_date,
        usage_date__lte=end_date
    )

    # Apply filters
    if subscription_filter:
        base_queryset = base_queryset.filter(sub_account_name=subscription_filter)
    if service_filter:
        base_queryset = base_queryset.filter(service_name__icontains=service_filter)

    # Charge type filter for CSP model (based on PricingCategory from extended_data)
    if charge_type_filter and charge_type_filter != 'all':
        if charge_type_filter == 'payg':
            base_queryset = base_queryset.filter(
                extended_data__PricingCategory='Standard'
            )
        elif charge_type_filter == 'reserved':
            base_queryset = base_queryset.filter(
                extended_data__PricingCategory='Committed'
            )

    # Calculate total cost (use effective_cost for actual charges, includes reserved)
    cost_aggregates = base_queryset.aggregate(
        total_effective=Sum('effective_cost'),
        total_list=Sum('list_cost')
    )
    total_cost = cost_aggregates['total_effective'] or 0
    total_list_cost = cost_aggregates['total_list'] or 0
    total_savings = total_list_cost - total_cost

    # Calculate reservation costs (amortized for selected period)
    reservation_cost = Decimal('0.00')
    reservation_savings_estimate = Decimal('0.00')
    active_reservations = ReservationCost.objects.all()

    if charge_type_filter != 'reserved':
        for reservation in active_reservations:
            if reservation.is_active(start_date) or reservation.is_active(end_date):
                period_cost = reservation.get_amortized_cost_for_period(start_date, end_date)
                reservation_cost += Decimal(str(period_cost))
                estimated_payg = period_cost / 0.45
                reservation_savings_estimate += Decimal(str(estimated_payg - period_cost))

    total_cost_with_reservations = float(total_cost) + float(reservation_cost)

    # Combined MTD/YTD in a single query using conditional aggregation
    month_start = end_date.replace(day=1)
    if month_start < start_date:
        month_start = start_date

    year_start = end_date.replace(month=1, day=1)
    if year_start < start_date:
        year_start = start_date

    mtd_ytd_queryset = CostRecord.objects.annotate(
        usage_date=TruncDate('charge_period_start')
    ).filter(
        usage_date__gte=year_start,
        usage_date__lte=end_date
    )
    if charge_type_filter and charge_type_filter != 'all':
        if charge_type_filter == 'payg':
            mtd_ytd_queryset = mtd_ytd_queryset.filter(extended_data__PricingCategory='Standard')
        elif charge_type_filter == 'reserved':
            mtd_ytd_queryset = mtd_ytd_queryset.filter(extended_data__PricingCategory='Committed')

    mtd_ytd = mtd_ytd_queryset.aggregate(
        mtd=Sum('billed_cost', filter=Q(
            usage_date__gte=month_start,
            usage_date__lte=end_date,
        )),
        ytd=Sum('billed_cost'),
    )
    mtd_cost = mtd_ytd['mtd'] or 0
    ytd_cost = mtd_ytd['ytd'] or 0

    actual_days = (end_date - start_date).days + 1
    avg_daily_cost = total_cost / actual_days if actual_days > 0 else 0

    total_exports = CostExport.objects.count()
    completed_exports = CostExport.objects.filter(import_status='completed').count()

    latest_export = CostExport.objects.filter(
        import_status='completed'
    ).order_by('-billing_period_end').first()

    top_subscriptions = base_queryset.values('sub_account_name').annotate(
        cost=Sum('billed_cost')
    ).order_by('-cost')[:5]

    top_services = base_queryset.values('service_name').annotate(
        cost=Sum('billed_cost')
    ).order_by('-cost')[:10]

    top_subscriptions_list = list(top_subscriptions)
    top_services_list = list(top_services)

    # Get all subscriptions, services for filter dropdowns (cached 5 min)
    all_subscriptions = cache.get('finops_all_subscriptions')
    if all_subscriptions is None:
        all_subscriptions = list(CostRecord.objects.values_list('sub_account_name', flat=True).distinct().order_by('sub_account_name'))
        cache.set('finops_all_subscriptions', all_subscriptions, 300)

    all_services = cache.get('finops_all_services')
    if all_services is None:
        all_services = list(CostRecord.objects.values_list('service_name', flat=True).distinct().order_by('service_name'))
        cache.set('finops_all_services', all_services, 300)
    pricing_categories = [
        ('payg', 'Pay-as-you-go'),
        ('reserved', 'Reserved Instance'),
        ('all', 'All Usage'),
    ]

    # Get unique tag keys for tag filter
    tag_keys = []
    tag_records = CostRecord.objects.exclude(tags__isnull=True).exclude(tags={}).values_list('tags', flat=True)[:100]
    seen_keys = set()
    for tags_dict in tag_records:
        if isinstance(tags_dict, dict):
            for key in tags_dict.keys():
                if key not in seen_keys:
                    seen_keys.add(key)
                    tag_keys.append(key)

    # Generate available months from cost data
    date_range = CostRecord.objects.aggregate(
        min_date=Min(TruncDate('charge_period_start')),
        max_date=Max(TruncDate('charge_period_start'))
    )
    available_months = []
    if date_range['min_date'] and date_range['max_date']:
        current = date_range['min_date'].replace(day=1)
        end = date_range['max_date'].replace(day=1)
        while current <= end:
            available_months.append({
                'value': f"{current.year}-{current.month:02d}",
                'label': current.strftime('%B %Y')
            })
            if current.month == 12:
                current = current.replace(year=current.year + 1, month=1)
            else:
                current = current.replace(month=current.month + 1)
        available_months.reverse()

    # Last import timestamp
    last_import_at = None
    if latest_export and latest_export.import_completed_at:
        last_import_at = latest_export.import_completed_at

    # Budget data
    budgets = []
    try:
        from .models import CostBudget
        for budget in CostBudget.objects.filter(is_active=True):
            spent = budget.get_current_spend()
            utilization = (float(spent) / float(budget.monthly_budget) * 100) if budget.monthly_budget > 0 else 0
            budgets.append({
                'name': budget.name,
                'monthly_budget': float(budget.monthly_budget),
                'spent': float(spent),
                'utilization': utilization,
                'alert_threshold': budget.alert_threshold,
                'over_threshold': utilization >= budget.alert_threshold,
            })
    except (ImportError, Exception):
        pass

    context = {
        'page_title': _('FinOps Hub - Cost Dashboard'),
        'period': {
            'start': start_date,
            'end': end_date,
            'days': days,
        },
        'filters': {
            'subscription': subscription_filter,
            'service': service_filter,
            'charge_type': charge_type_filter,
            'month': month_filter,
            'all_subscriptions': all_subscriptions,
            'all_services': all_services,
            'pricing_categories': pricing_categories,
            'available_months': available_months,
            'tag_keys': tag_keys,
        },
        'summary': {
            'total_cost': total_cost,
            'total_list_cost': total_list_cost,
            'total_savings': total_savings,
            'reservation_cost': float(reservation_cost),
            'reservation_savings_estimate': float(reservation_savings_estimate),
            'total_cost_with_reservations': total_cost_with_reservations,
            'mtd_cost': mtd_cost,
            'ytd_cost': ytd_cost,
            'avg_daily_cost': avg_daily_cost,
            'total_exports': total_exports,
            'completed_exports': completed_exports,
        },
        'latest_export': latest_export,
        'last_import_at': last_import_at,
        'active_reservations': active_reservations,
        'budgets': budgets,
        'top_subscriptions': json.dumps(top_subscriptions_list, cls=DjangoJSONEncoder),
        'top_services': top_services_list,
        'finops_json_config': _build_json_config(
            'dashboard',
            periodDays=days,
            totalCost=float(total_cost),
            topSubscriptions=top_subscriptions_list,
        ),
    }

    return render(request, 'finops/dashboard.html', context)


def subscription_view(request):
    """Multi-subscription cost comparison view"""
    date_info = resolve_date_range(request.GET)
    start_date = date_info['start_date']
    end_date = date_info['end_date']
    days = date_info['days']

    subscriptions = CostRecord.objects.annotate(
        usage_date=TruncDate('charge_period_start')
    ).filter(
        usage_date__gte=start_date,
        usage_date__lte=end_date
    ).values('sub_account_name').annotate(
        total_cost=Sum('billed_cost'),
        record_count=Count('id')
    ).order_by('-total_cost')

    subscriptions_list = list(subscriptions)
    total_cost_all = sum(s['total_cost'] for s in subscriptions_list)

    context = {
        'page_title': _('FinOps Hub - Subscriptions'),
        'period': {'start': start_date, 'end': end_date, 'days': days},
        'subscriptions_list': subscriptions_list,
        'subscriptions_count': len(subscriptions_list),
        'total_cost': total_cost_all,
        'finops_json_config': _build_json_config(
            'subscriptions',
            subscriptions=subscriptions_list,
        ),
    }

    return render(request, 'finops/subscription_view.html', context)


def service_breakdown(request):
    """Service-level cost breakdown"""
    date_info = resolve_date_range(request.GET)
    start_date = date_info['start_date']
    end_date = date_info['end_date']
    days = date_info['days']
    subscription = request.GET.get('subscription')

    base_queryset = CostRecord.objects.annotate(
        usage_date=TruncDate('charge_period_start')
    ).filter(
        usage_date__gte=start_date,
        usage_date__lte=end_date
    )

    if subscription:
        base_queryset = base_queryset.filter(sub_account_name=subscription)

    services = base_queryset.values('service_name', 'service_category').annotate(
        total_cost=Sum('billed_cost'),
        record_count=Count('id')
    ).order_by('-total_cost')

    total_cost = base_queryset.aggregate(total=Sum('billed_cost'))['total'] or 1

    available_subscriptions = CostRecord.objects.annotate(
        usage_date=TruncDate('charge_period_start')
    ).filter(
        usage_date__gte=start_date,
        usage_date__lte=end_date
    ).values_list('sub_account_name', flat=True).distinct().order_by('sub_account_name')

    services_list = list(services)
    services_chart = services_list[:10]

    context = {
        'page_title': _('FinOps Hub - Services'),
        'period': {'start': start_date, 'end': end_date, 'days': days},
        'subscription': subscription,
        'available_subscriptions': available_subscriptions,
        'services_table': services_list,
        'total_cost': total_cost,
        'finops_json_config': _build_json_config(
            'services',
            services=services_chart,
        ),
    }

    return render(request, 'finops/service_breakdown.html', context)


def resource_explorer(request):
    """Resource-level cost explorer"""
    date_info = resolve_date_range(request.GET)
    start_date = date_info['start_date']
    end_date = date_info['end_date']
    days = date_info['days']
    subscription = request.GET.get('subscription')
    resource_group = request.GET.get('resource_group')

    base_queryset = CostRecord.objects.annotate(
        usage_date=TruncDate('charge_period_start')
    ).filter(
        usage_date__gte=start_date,
        usage_date__lte=end_date
    )

    if subscription:
        base_queryset = base_queryset.filter(sub_account_name=subscription)
    if resource_group:
        base_queryset = base_queryset.filter(resource_group_name=resource_group)

    resources = base_queryset.values('resource_name').annotate(
        total_cost=Sum('billed_cost'),
        record_count=Count('id'),
        resource_type=Min('resource_type'),
        resource_group_name=Min('resource_group_name'),
        service_name=Min('service_name'),
        resource_id=Min('resource_id'),
        sub_account_id=Min('sub_account_id')
    ).order_by('-total_cost')[:100]

    filter_base = CostRecord.objects.annotate(
        usage_date=TruncDate('charge_period_start')
    ).filter(
        usage_date__gte=start_date,
        usage_date__lte=end_date
    )

    available_subscriptions = filter_base.values_list(
        'sub_account_name', flat=True
    ).distinct().order_by('sub_account_name')

    available_resource_groups = filter_base.values_list(
        'resource_group_name', flat=True
    ).distinct().order_by('resource_group_name')

    context = {
        'page_title': _('FinOps Hub - Resources'),
        'period': {'start': start_date, 'end': end_date, 'days': days},
        'subscription': subscription,
        'resource_group': resource_group,
        'available_subscriptions': available_subscriptions,
        'available_resource_groups': available_resource_groups,
        'resources': resources,
    }

    return render(request, 'finops/resource_explorer.html', context)


@staff_member_required
def trigger_import(request):
    """Admin page to manually trigger cost data import"""
    if request.method == 'POST':
        return JsonResponse({
            'status': 'info',
            'message': 'Run: python manage.py import_cost_data'
        })

    incomplete_exports = CostExport.get_incomplete_exports()
    recent_imports = CostExport.objects.exclude(
        id__in=incomplete_exports.values_list('id', flat=True)
    ).order_by('-import_started_at')[:20]

    context = {
        'page_title': _('FinOps Hub - Import Data'),
        'recent_imports': recent_imports,
        'incomplete_exports': incomplete_exports,
    }

    return render(request, 'finops/import.html', context)


@staff_member_required
def update_subscription_id(request, export_id):
    """Update subscription ID for an incomplete export"""
    export = get_object_or_404(CostExport, id=export_id)

    if request.method == 'POST':
        form = SubscriptionIDForm(request.POST, instance=export)
        if form.is_valid():
            form.save()
            messages.success(
                request,
                f'Subscription ID saved for {export.subscription_name}. You can now re-import this export.'
            )
            return redirect('finops_hub:import')
    else:
        form = SubscriptionIDForm(instance=export)

    context = {
        'page_title': _('Update Subscription ID'),
        'form': form,
        'export': export,
    }

    return render(request, 'finops/update_subscription_id.html', context)


def faq(request):
    """FAQ page explaining data flow, updates, and Azure export configuration"""
    latest_export = CostExport.objects.filter(
        import_status='completed'
    ).order_by('-import_completed_at').first()

    subscriptions = CostExport.objects.filter(
        import_status='completed'
    ).values_list('subscription_name', flat=True).distinct()

    context = {
        'page_title': _('FinOps Hub - FAQ & Data Flow'),
        'latest_export': latest_export,
        'subscriptions_count': len(subscriptions),
        'subscriptions': subscriptions,
    }

    return render(request, 'finops/faq.html', context)


def anomalies_view(request):
    """Cost anomalies dashboard"""
    days = int(request.GET.get('days', 30))
    severity_filter = request.GET.get('severity')
    acknowledged_filter = request.GET.get('acknowledged')

    end_date = timezone.now().date()
    start_date = end_date - timedelta(days=days)

    queryset = CostAnomaly.objects.filter(detected_date__gte=start_date)

    if severity_filter:
        queryset = queryset.filter(severity=severity_filter)
    if acknowledged_filter is not None:
        is_ack = acknowledged_filter.lower() == 'true'
        queryset = queryset.filter(is_acknowledged=is_ack)

    anomalies = queryset[:50]

    context = {
        'page_title': _('FinOps Hub - Cost Anomalies'),
        'anomalies': anomalies,
        'critical_count': CostAnomaly.objects.filter(severity='critical', is_acknowledged=False).count(),
        'high_count': CostAnomaly.objects.filter(severity='high', is_acknowledged=False).count(),
        'medium_count': CostAnomaly.objects.filter(severity='medium', is_acknowledged=False).count(),
        'low_count': CostAnomaly.objects.filter(severity='low', is_acknowledged=False).count(),
        'days': days,
        'severity_filter': severity_filter,
        'acknowledged_filter': acknowledged_filter,
    }

    return render(request, 'finops/anomalies.html', context)


def forecast_view(request):
    """Cost forecast dashboard"""
    from power_up.finops.models import CostForecast

    forecast_days = int(request.GET.get('days', 30))

    forecasts = CostForecast.objects.filter(
        dimension_type='overall',
        dimension_value='Total'
    ).order_by('forecast_date')[:forecast_days]

    end_date = timezone.now().date()
    start_date = end_date - timedelta(days=30)

    historical = CostAggregation.objects.filter(
        aggregation_type='daily',
        dimension_type='overall',
        period_start__gte=start_date,
        period_start__lte=end_date
    ).order_by('period_start')

    if forecasts:
        avg_forecast = sum(f.forecast_cost for f in forecasts) / len(forecasts)
        total_forecast = sum(f.forecast_cost for f in forecasts)
        model_accuracy = forecasts[0].metadata.get('r_squared', 0) if forecasts else 0
    else:
        avg_forecast = 0
        total_forecast = 0
        model_accuracy = 0

    if historical:
        avg_historical = sum(h.total_cost for h in historical) / len(historical)
    else:
        avg_historical = 0

    if avg_historical > 0:
        pct_change = ((float(avg_forecast) - float(avg_historical)) / float(avg_historical)) * 100
    else:
        pct_change = 0

    has_data = forecasts.count() > 0

    # Build chart JSON config
    historical_chart = [
        {'date': h.period_start.isoformat(), 'cost': float(h.total_cost)}
        for h in historical
    ]
    forecast_chart = [
        {
            'date': f.forecast_date.isoformat(),
            'cost': float(f.forecast_cost),
            'upper': float(f.upper_bound),
            'lower': float(f.lower_bound),
        }
        for f in forecasts
    ]

    context = {
        'page_title': _('FinOps Hub - Cost Forecast'),
        'forecasts': forecasts,
        'historical': historical,
        'forecast_days': forecast_days,
        'avg_forecast': avg_forecast,
        'total_forecast': total_forecast,
        'avg_historical': avg_historical,
        'model_accuracy': model_accuracy,
        'has_data': has_data,
        'pct_change': pct_change,
        'finops_json_config': _build_json_config(
            'forecast',
            hasData=has_data,
            historical=historical_chart,
            forecasts=forecast_chart,
        ),
    }

    return render(request, 'finops/forecast.html', context)


def comparison_view(request):
    """Month-over-month cost comparison"""
    # Get available months
    date_range = CostRecord.objects.aggregate(
        min_date=Min(TruncDate('charge_period_start')),
        max_date=Max(TruncDate('charge_period_start'))
    )

    available_months = []
    if date_range['min_date'] and date_range['max_date']:
        current = date_range['min_date'].replace(day=1)
        end = date_range['max_date'].replace(day=1)
        while current <= end:
            available_months.append({
                'value': f"{current.year}-{current.month:02d}",
                'label': current.strftime('%B %Y')
            })
            if current.month == 12:
                current = current.replace(year=current.year + 1, month=1)
            else:
                current = current.replace(month=current.month + 1)
        available_months.reverse()

    month1 = request.GET.get('month1')
    month2 = request.GET.get('month2')

    # Default to last two months if available
    if not month1 and len(available_months) >= 2:
        month1 = available_months[0]['value']
        month2 = available_months[1]['value']
    elif not month1 and len(available_months) >= 1:
        month1 = available_months[0]['value']
        month2 = month1

    comparison_data = []
    month1_total = 0
    month2_total = 0
    month1_label = month1 or ''
    month2_label = month2 or ''
    delta_pct = 0

    if month1 and month2:
        # Get monthly aggregations by service
        m1_data = CostAggregation.objects.filter(
            aggregation_type='monthly',
            dimension_type='service',
            period_start__year=int(month1.split('-')[0]),
            period_start__month=int(month1.split('-')[1]),
        ).values('dimension_value', 'total_cost')

        m2_data = CostAggregation.objects.filter(
            aggregation_type='monthly',
            dimension_type='service',
            period_start__year=int(month2.split('-')[0]),
            period_start__month=int(month2.split('-')[1]),
        ).values('dimension_value', 'total_cost')

        # Build lookup dicts
        m1_dict = {row['dimension_value']: float(row['total_cost']) for row in m1_data}
        m2_dict = {row['dimension_value']: float(row['total_cost']) for row in m2_data}

        all_services = sorted(set(list(m1_dict.keys()) + list(m2_dict.keys())))

        for service in all_services:
            cost1 = m1_dict.get(service, 0)
            cost2 = m2_dict.get(service, 0)
            if cost1 > 0:
                d_pct = ((cost2 - cost1) / cost1) * 100
            elif cost2 > 0:
                d_pct = 100.0
            else:
                d_pct = 0
            comparison_data.append({
                'service_name': service,
                'month1_cost': cost1,
                'month2_cost': cost2,
                'delta_pct': round(d_pct, 1),
            })

        # Sort by absolute delta descending
        comparison_data.sort(key=lambda x: abs(x['delta_pct']), reverse=True)

        month1_total = sum(m1_dict.values())
        month2_total = sum(m2_dict.values())
        if month1_total > 0:
            delta_pct = ((month2_total - month1_total) / month1_total) * 100

    # Chart data (top 10 by combined cost)
    chart_services = sorted(comparison_data, key=lambda x: x['month1_cost'] + x['month2_cost'], reverse=True)[:10]

    context = {
        'page_title': _('FinOps Hub - Compare'),
        'available_months': available_months,
        'month1': month1,
        'month2': month2,
        'month1_label': month1_label,
        'month2_label': month2_label,
        'month1_total': month1_total,
        'month2_total': month2_total,
        'delta_pct': delta_pct,
        'comparison_data': comparison_data,
        'finops_json_config': _build_json_config(
            'comparison',
            services=chart_services,
            month1Label=month1_label,
            month2Label=month2_label,
        ),
    }

    return render(request, 'finops/comparison.html', context)


def resource_group_view(request):
    """Resource group cost breakdown"""
    date_info = resolve_date_range(request.GET)
    start_date = date_info['start_date']
    end_date = date_info['end_date']
    days = date_info['days']
    subscription = request.GET.get('subscription')

    base_queryset = CostRecord.objects.annotate(
        usage_date=TruncDate('charge_period_start')
    ).filter(
        usage_date__gte=start_date,
        usage_date__lte=end_date
    )

    if subscription:
        base_queryset = base_queryset.filter(sub_account_name=subscription)

    resource_groups = base_queryset.values('resource_group_name').annotate(
        total_cost=Sum('billed_cost'),
        record_count=Count('id')
    ).order_by('-total_cost')

    total_cost = base_queryset.aggregate(total=Sum('billed_cost'))['total'] or 1
    resource_groups_list = list(resource_groups)

    available_subscriptions = CostRecord.objects.annotate(
        usage_date=TruncDate('charge_period_start')
    ).filter(
        usage_date__gte=start_date,
        usage_date__lte=end_date
    ).values_list('sub_account_name', flat=True).distinct().order_by('sub_account_name')

    chart_data = resource_groups_list[:15]

    context = {
        'page_title': _('FinOps Hub - Resource Groups'),
        'period': {'start': start_date, 'end': end_date, 'days': days},
        'subscription': subscription,
        'available_subscriptions': available_subscriptions,
        'resource_groups_list': resource_groups_list,
        'resource_groups_count': len(resource_groups_list),
        'total_cost': total_cost,
        'finops_json_config': _build_json_config(
            'resource_groups',
            resourceGroups=chart_data,
        ),
    }

    return render(request, 'finops/resource_groups.html', context)
