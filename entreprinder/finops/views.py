# entreprinder/finops/views.py
"""
FinOps Hub Views - Azure Cost Dashboard

Dashboard views for cost management and analytics.
"""

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib import messages
from django.http import JsonResponse
from django.utils.translation import gettext as _
from django.utils import timezone
from django.db.models import Sum, Count, Min, Max
from django.db.models.functions import TruncDate
from django.core.serializers.json import DjangoJSONEncoder
from datetime import timedelta
import json

from .models import CostExport, CostRecord, CostAggregation
from .forms import SubscriptionIDForm


def is_admin(user):
    """Check if user is staff/admin"""
    return user.is_staff or user.is_superuser


def dashboard(request):
    """Main FinOps dashboard with cost overview"""
    days = int(request.GET.get('days', 30))

    # Smart date range: check actual usage dates in the database
    date_range = CostRecord.objects.aggregate(
        min_date=Min(TruncDate('charge_period_start')),
        max_date=Max(TruncDate('charge_period_start'))
    )

    end_date = timezone.now().date()
    start_date = end_date - timedelta(days=days)

    if date_range['min_date'] and date_range['max_date']:
        if start_date > date_range['max_date'] or end_date < date_range['min_date']:
            end_date = date_range['max_date']
            start_date = end_date - timedelta(days=days - 1)
            if start_date < date_range['min_date']:
                start_date = date_range['min_date']

    base_queryset = CostRecord.objects.annotate(
        usage_date=TruncDate('charge_period_start')
    ).filter(
        usage_date__gte=start_date,
        usage_date__lte=end_date
    )

    total_cost = base_queryset.aggregate(total=Sum('billed_cost'))['total'] or 0

    month_start = end_date.replace(day=1)
    if month_start < start_date:
        month_start = start_date

    mtd_queryset = CostRecord.objects.annotate(
        usage_date=TruncDate('charge_period_start')
    ).filter(
        usage_date__gte=month_start,
        usage_date__lte=end_date
    )
    mtd_cost = mtd_queryset.aggregate(total=Sum('billed_cost'))['total'] or 0

    year_start = end_date.replace(month=1, day=1)
    if year_start < start_date:
        year_start = start_date

    ytd_queryset = CostRecord.objects.annotate(
        usage_date=TruncDate('charge_period_start')
    ).filter(
        usage_date__gte=year_start,
        usage_date__lte=end_date
    )
    ytd_cost = ytd_queryset.aggregate(total=Sum('billed_cost'))['total'] or 0

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

    context = {
        'page_title': _('FinOps Hub - Cost Dashboard'),
        'period': {
            'start': start_date,
            'end': end_date,
            'days': days,
        },
        'summary': {
            'total_cost': total_cost,
            'mtd_cost': mtd_cost,
            'ytd_cost': ytd_cost,
            'avg_daily_cost': avg_daily_cost,
            'total_exports': total_exports,
            'completed_exports': completed_exports,
        },
        'latest_export': latest_export,
        'top_subscriptions': json.dumps(top_subscriptions_list, cls=DjangoJSONEncoder),
        'top_services': top_services_list,
    }

    return render(request, 'finops_hub/dashboard.html', context)


def subscription_view(request):
    """Multi-subscription cost comparison view"""
    days = int(request.GET.get('days', 30))

    date_range = CostRecord.objects.aggregate(
        min_date=Min(TruncDate('charge_period_start')),
        max_date=Max(TruncDate('charge_period_start'))
    )

    end_date = timezone.now().date()
    start_date = end_date - timedelta(days=days)

    if date_range['min_date'] and date_range['max_date']:
        if start_date > date_range['max_date'] or end_date < date_range['min_date']:
            end_date = date_range['max_date']
            start_date = end_date - timedelta(days=days - 1)
            if start_date < date_range['min_date']:
                start_date = date_range['min_date']

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
        'subscriptions': json.dumps(subscriptions_list, cls=DjangoJSONEncoder),
        'subscriptions_list': subscriptions_list,
        'subscriptions_count': len(subscriptions_list),
        'total_cost': total_cost_all,
    }

    return render(request, 'finops_hub/subscription_view.html', context)


def service_breakdown(request):
    """Service-level cost breakdown"""
    days = int(request.GET.get('days', 30))
    subscription = request.GET.get('subscription')

    date_range = CostRecord.objects.aggregate(
        min_date=Min(TruncDate('charge_period_start')),
        max_date=Max(TruncDate('charge_period_start'))
    )

    end_date = timezone.now().date()
    start_date = end_date - timedelta(days=days)

    if date_range['min_date'] and date_range['max_date']:
        if start_date > date_range['max_date'] or end_date < date_range['min_date']:
            end_date = date_range['max_date']
            start_date = end_date - timedelta(days=days - 1)
            if start_date < date_range['min_date']:
                start_date = date_range['min_date']

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
    services_chart_data = json.dumps(services_list[:10], cls=DjangoJSONEncoder)

    context = {
        'page_title': _('FinOps Hub - Services'),
        'period': {'start': start_date, 'end': end_date, 'days': days},
        'subscription': subscription,
        'available_subscriptions': available_subscriptions,
        'services': services_chart_data,
        'services_table': services_list,
        'total_cost': total_cost,
    }

    return render(request, 'finops_hub/service_breakdown.html', context)


def resource_explorer(request):
    """Resource-level cost explorer"""
    days = int(request.GET.get('days', 30))
    subscription = request.GET.get('subscription')
    resource_group = request.GET.get('resource_group')

    date_range = CostRecord.objects.aggregate(
        min_date=Min(TruncDate('charge_period_start')),
        max_date=Max(TruncDate('charge_period_start'))
    )

    end_date = timezone.now().date()
    start_date = end_date - timedelta(days=days)

    if date_range['min_date'] and date_range['max_date']:
        if start_date > date_range['max_date'] or end_date < date_range['min_date']:
            end_date = date_range['max_date']
            start_date = end_date - timedelta(days=days - 1)
            if start_date < date_range['min_date']:
                start_date = date_range['min_date']

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

    return render(request, 'finops_hub/resource_explorer.html', context)


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

    return render(request, 'finops_hub/import.html', context)


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

    return render(request, 'finops_hub/update_subscription_id.html', context)


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

    return render(request, 'finops_hub/faq.html', context)
