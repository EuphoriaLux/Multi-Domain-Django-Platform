"""
REST API views for FinOps Hub
"""
from rest_framework import viewsets, status
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, IsAdminUser, AllowAny
from django.http import HttpResponse
from django.db.models import Sum, Count, Q, Avg, Min, Max
from django.db.models.functions import TruncDate
from django.utils import timezone
from datetime import timedelta
from decimal import Decimal
import csv

from .models import CostExport, CostRecord, CostAggregation
from .serializers import (
    CostExportSerializer,
    CostRecordSerializer,
    CostRecordSummarySerializer,
    CostAggregationSerializer
)
from .permissions import HasSessionOrIsAuthenticated


class CostExportViewSet(viewsets.ReadOnlyModelViewSet):
    """
    API endpoint for cost export metadata
    """
    queryset = CostExport.objects.all()
    serializer_class = CostExportSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ['subscription_name', 'import_status']
    ordering_fields = ['billing_period_start', 'import_completed_at']
    ordering = ['-billing_period_start']


class CostRecordViewSet(viewsets.ReadOnlyModelViewSet):
    """
    API endpoint for detailed cost records
    """
    queryset = CostRecord.objects.select_related('cost_export')
    permission_classes = [IsAuthenticated]
    filterset_fields = [
        'service_name', 'sub_account_name', 'resource_group_name',
        'charge_category', 'billing_period_start'
    ]
    ordering_fields = ['charge_period_start', 'billed_cost']
    ordering = ['-charge_period_start']

    def get_serializer_class(self):
        """Use summary serializer for list, detailed for retrieve"""
        if self.action == 'list':
            return CostRecordSummarySerializer
        return CostRecordSerializer

    def get_queryset(self):
        """Filter by date range if provided"""
        queryset = super().get_queryset()

        # Date range filtering
        start_date = self.request.query_params.get('start_date')
        end_date = self.request.query_params.get('end_date')

        if start_date:
            queryset = queryset.filter(billing_period_start__gte=start_date)
        if end_date:
            queryset = queryset.filter(billing_period_start__lte=end_date)

        return queryset


class CostAggregationViewSet(viewsets.ReadOnlyModelViewSet):
    """
    API endpoint for pre-computed cost aggregations
    """
    queryset = CostAggregation.objects.all()
    serializer_class = CostAggregationSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ['aggregation_type', 'dimension_type', 'dimension_value', 'currency']
    ordering_fields = ['period_start', 'total_cost']
    ordering = ['-period_start']

    def get_queryset(self):
        """Filter by date range if provided"""
        queryset = super().get_queryset()

        start_date = self.request.query_params.get('start_date')
        end_date = self.request.query_params.get('end_date')

        if start_date:
            queryset = queryset.filter(period_start__gte=start_date)
        if end_date:
            queryset = queryset.filter(period_start__lte=end_date)

        return queryset


@api_view(['GET'])
@permission_classes([HasSessionOrIsAuthenticated])
def cost_summary(request):
    """
    Get overall cost summary

    Query params:
        - currency: Currency filter (default: EUR)
        - days: Number of days to look back (default: 30)
    """
    currency = request.query_params.get('currency', 'EUR')
    days = int(request.query_params.get('days', 30))

    # Check what date range actually exists in the database
    date_range = CostRecord.objects.filter(
        billing_currency=currency
    ).aggregate(
        min_date=Min('billing_period_start'),
        max_date=Max('billing_period_start')
    )

    # Smart date range selection
    end_date = timezone.now().date()
    start_date = end_date - timedelta(days=days)

    if date_range['min_date'] and date_range['max_date']:
        # Check if the requested date range overlaps with available data
        if start_date > date_range['max_date'] or end_date < date_range['min_date']:
            # No overlap - use the actual data range
            end_date = date_range['max_date']
            start_date = max(date_range['min_date'], end_date - timedelta(days=days))

    # Query cost records
    records = CostRecord.objects.filter(
        billing_period_start__gte=start_date,
        billing_period_start__lte=end_date,
        billing_currency=currency
    )

    # Aggregate totals
    summary = records.aggregate(
        total_cost=Sum('billed_cost'),
        total_records=Count('id'),
        avg_cost_per_day=Avg('billed_cost'),
        usage_cost=Sum('billed_cost', filter=Q(charge_category='Usage')),
        purchase_cost=Sum('billed_cost', filter=Q(charge_category='Purchase')),
    )

    # Top subscriptions
    top_subscriptions = records.values('sub_account_name').annotate(
        cost=Sum('billed_cost')
    ).order_by('-cost')[:5]

    # Top services
    top_services = records.values('service_name').annotate(
        cost=Sum('billed_cost')
    ).order_by('-cost')[:10]

    # Top resource groups
    top_resource_groups = records.values('resource_group_name').annotate(
        cost=Sum('billed_cost')
    ).order_by('-cost')[:10]

    # Daily trend - group by actual usage date
    daily_costs = records.annotate(
        usage_date=TruncDate('charge_period_start')
    ).values('usage_date').annotate(
        cost=Sum('billed_cost')
    ).order_by('usage_date')

    return Response({
        'period': {
            'start': start_date,
            'end': end_date,
            'days': days,
        },
        'currency': currency,
        'summary': {
            'total_cost': float(summary['total_cost'] or 0),
            'total_records': summary['total_records'],
            'avg_cost_per_day': float(summary['avg_cost_per_day'] or 0),
            'usage_cost': float(summary['usage_cost'] or 0),
            'purchase_cost': float(summary['purchase_cost'] or 0),
        },
        'top_subscriptions': list(top_subscriptions),
        'top_services': list(top_services),
        'top_resource_groups': list(top_resource_groups),
        'daily_trend': list(daily_costs),
    })


@api_view(['GET'])
@permission_classes([HasSessionOrIsAuthenticated])
def costs_by_subscription(request):
    """
    Get costs broken down by subscription

    Query params:
        - currency: Currency filter (default: EUR)
        - days: Number of days to look back (default: 30)
    """
    currency = request.query_params.get('currency', 'EUR')
    days = int(request.query_params.get('days', 30))

    # Check what date range actually exists
    date_range = CostAggregation.objects.filter(
        dimension_type='subscription',
        currency=currency
    ).aggregate(
        min_date=Min('period_start'),
        max_date=Max('period_start')
    )

    end_date = timezone.now().date()
    start_date = end_date - timedelta(days=days)

    if date_range['min_date'] and date_range['max_date']:
        if start_date > date_range['max_date'] or end_date < date_range['min_date']:
            end_date = date_range['max_date']
            start_date = max(date_range['min_date'], end_date - timedelta(days=days))

    # Get cost aggregations by subscription
    subscriptions = CostAggregation.objects.filter(
        dimension_type='subscription',
        period_start__gte=start_date,
        period_start__lte=end_date,
        currency=currency
    ).values('dimension_value').annotate(
        total_cost=Sum('total_cost'),
        usage_cost=Sum('usage_cost'),
        purchase_cost=Sum('purchase_cost'),
    ).order_by('-total_cost')

    return Response({
        'period': {'start': start_date, 'end': end_date},
        'currency': currency,
        'subscriptions': list(subscriptions),
    })


@api_view(['GET'])
@permission_classes([HasSessionOrIsAuthenticated])
def costs_by_service(request):
    """
    Get costs broken down by Azure service

    Query params:
        - currency: Currency filter (default: EUR)
        - days: Number of days to look back (default: 30)
        - subscription: Filter by subscription name (optional)
    """
    currency = request.query_params.get('currency', 'EUR')
    days = int(request.query_params.get('days', 30))
    subscription = request.query_params.get('subscription')

    # Build base filters
    base_filters = {
        'billing_currency': currency,
    }
    if subscription:
        base_filters['sub_account_name'] = subscription

    # Check what date range actually exists
    date_range = CostRecord.objects.filter(**base_filters).aggregate(
        min_date=Min('billing_period_start'),
        max_date=Max('billing_period_start')
    )

    end_date = timezone.now().date()
    start_date = end_date - timedelta(days=days)

    if date_range['min_date'] and date_range['max_date']:
        if start_date > date_range['max_date'] or end_date < date_range['min_date']:
            end_date = date_range['max_date']
            start_date = max(date_range['min_date'], end_date - timedelta(days=days))

    # Build final filters
    filters = {
        **base_filters,
        'billing_period_start__gte': start_date,
        'billing_period_start__lte': end_date,
    }

    # Get costs by service
    services = CostRecord.objects.filter(**filters).values('service_name').annotate(
        total_cost=Sum('billed_cost'),
        record_count=Count('id'),
    ).order_by('-total_cost')

    return Response({
        'period': {'start': start_date, 'end': end_date},
        'currency': currency,
        'subscription': subscription,
        'services': list(services),
    })


@api_view(['GET'])
@permission_classes([HasSessionOrIsAuthenticated])
def costs_by_resource_group(request):
    """
    Get costs broken down by resource group

    Query params:
        - currency: Currency filter (default: EUR)
        - days: Number of days to look back (default: 30)
        - subscription: Filter by subscription name (optional)
    """
    currency = request.query_params.get('currency', 'EUR')
    days = int(request.query_params.get('days', 30))
    subscription = request.query_params.get('subscription')

    # Build base filters
    base_filters = {
        'billing_currency': currency,
    }
    if subscription:
        base_filters['sub_account_name'] = subscription

    # Check what date range actually exists
    date_range = CostRecord.objects.filter(**base_filters).aggregate(
        min_date=Min('billing_period_start'),
        max_date=Max('billing_period_start')
    )

    end_date = timezone.now().date()
    start_date = end_date - timedelta(days=days)

    if date_range['min_date'] and date_range['max_date']:
        if start_date > date_range['max_date'] or end_date < date_range['min_date']:
            end_date = date_range['max_date']
            start_date = max(date_range['min_date'], end_date - timedelta(days=days))

    # Build final filters
    filters = {
        **base_filters,
        'billing_period_start__gte': start_date,
        'billing_period_start__lte': end_date,
    }

    # Get costs by resource group
    resource_groups = CostRecord.objects.filter(**filters).values('resource_group_name').annotate(
        total_cost=Sum('billed_cost'),
        record_count=Count('id'),
    ).order_by('-total_cost')

    return Response({
        'period': {'start': start_date, 'end': end_date},
        'currency': currency,
        'subscription': subscription,
        'resource_groups': list(resource_groups),
    })


@api_view(['GET'])
@permission_classes([HasSessionOrIsAuthenticated])
def cost_trend(request):
    """
    Get daily cost trend data

    Query params:
        - currency: Currency filter (default: EUR)
        - days: Number of days to look back (default: 30)
        - subscription: Filter by subscription name (optional)
        - service: Filter by service name (optional)
    """
    currency = request.query_params.get('currency', 'EUR')
    days = int(request.query_params.get('days', 30))
    subscription = request.query_params.get('subscription')
    service = request.query_params.get('service')

    # Build base filters for dimension filtering
    base_filters = {
        'billing_currency': currency,
    }
    if subscription:
        base_filters['sub_account_name'] = subscription
    if service:
        base_filters['service_name'] = service

    # First, check what date range actually exists in the database
    # Use charge_period_start (actual usage date) not billing_period_start
    date_range = CostRecord.objects.filter(**base_filters).aggregate(
        min_date=Min(TruncDate('charge_period_start')),
        max_date=Max(TruncDate('charge_period_start'))
    )

    # Smart date range selection:
    # 1. If we have data, check if it overlaps with the requested range
    # 2. If no overlap, use the actual data range
    # 3. Otherwise use the requested range
    end_date = timezone.now().date()
    start_date = end_date - timedelta(days=days)

    if date_range['min_date'] and date_range['max_date']:
        # Check if the requested date range overlaps with available data
        if start_date > date_range['max_date'] or end_date < date_range['min_date']:
            # No overlap - use the actual data range (last N days of available data)
            end_date = date_range['max_date']
            start_date = end_date - timedelta(days=days - 1)  # Subtract days-1 to get exactly N days
            # Don't go before the earliest available date
            if start_date < date_range['min_date']:
                start_date = date_range['min_date']

    # Build final filters - we'll apply date filtering after aggregation
    filters = base_filters

    # Get daily costs filtered by the actual usage date range
    # Group by charge_period_start (actual usage date) not billing_period_start (billing month start)
    # Use TruncDate to extract just the date portion from datetime field
    daily_costs_queryset = CostRecord.objects.filter(**filters).annotate(
        usage_date=TruncDate('charge_period_start')
    )

    # Filter by the calculated date range
    daily_costs_queryset = daily_costs_queryset.filter(
        usage_date__gte=start_date,
        usage_date__lte=end_date
    )

    daily_costs = daily_costs_queryset.values('usage_date').annotate(
        total_cost=Sum('billed_cost'),
        usage_cost=Sum('billed_cost', filter=Q(charge_category='Usage')),
        purchase_cost=Sum('billed_cost', filter=Q(charge_category='Purchase')),
        record_count=Count('id'),
    ).order_by('usage_date')

    return Response({
        'period': {'start': start_date, 'end': end_date},
        'currency': currency,
        'filters': {
            'subscription': subscription,
            'service': service,
        },
        'daily_costs': list(daily_costs),
        'data_availability': {
            'earliest_date': date_range['min_date'],
            'latest_date': date_range['max_date'],
        }
    })


@api_view(['GET'])
@permission_classes([HasSessionOrIsAuthenticated])
def export_status(request):
    """
    Get status of cost data imports

    Returns:
        - Total exports processed
        - Recent imports
        - Failed imports
        - Latest billing period
    """
    total_exports = CostExport.objects.count()
    completed_exports = CostExport.objects.filter(import_status='completed').count()
    failed_exports = CostExport.objects.filter(import_status='failed').count()

    recent_imports = CostExport.objects.order_by('-import_completed_at')[:10]
    failed_imports = CostExport.objects.filter(import_status='failed').order_by('-import_started_at')[:5]

    latest_period = CostExport.objects.filter(
        import_status='completed'
    ).order_by('-billing_period_end').first()

    return Response({
        'summary': {
            'total_exports': total_exports,
            'completed': completed_exports,
            'failed': failed_exports,
            'success_rate': round(completed_exports / total_exports * 100, 2) if total_exports > 0 else 0,
        },
        'latest_billing_period': {
            'start': latest_period.billing_period_start if latest_period else None,
            'end': latest_period.billing_period_end if latest_period else None,
        },
        'recent_imports': CostExportSerializer(recent_imports, many=True).data,
        'failed_imports': CostExportSerializer(failed_imports, many=True).data,
    })


@api_view(['GET'])
@permission_classes([HasSessionOrIsAuthenticated])
def export_costs_csv(request):
    """
    Export cost data as CSV file

    Query params:
        - currency: Currency filter (default: EUR)
        - days: Number of days to look back (default: 30)
        - subscription: Filter by subscription name (optional)
        - format: daily_summary (default) or detailed
    """
    currency = request.query_params.get('currency', 'EUR')
    days = int(request.query_params.get('days', 30))
    subscription = request.query_params.get('subscription')
    export_format = request.query_params.get('format', 'daily_summary')

    # Build base filters
    base_filters = {
        'billing_currency': currency,
    }
    if subscription:
        base_filters['sub_account_name'] = subscription

    # Check what date range actually exists
    date_range = CostRecord.objects.filter(**base_filters).aggregate(
        min_date=Min('billing_period_start'),
        max_date=Max('billing_period_start')
    )

    # Smart date range selection
    end_date = timezone.now().date()
    start_date = end_date - timedelta(days=days)

    if date_range['min_date'] and date_range['max_date']:
        if start_date > date_range['max_date'] or end_date < date_range['min_date']:
            end_date = date_range['max_date']
            start_date = max(date_range['min_date'], end_date - timedelta(days=days))

    # Build final filters
    filters = {
        **base_filters,
        'billing_period_start__gte': start_date,
        'billing_period_start__lte': end_date,
    }

    # Create HTTP response with CSV headers
    response = HttpResponse(content_type='text/csv')
    filename = f'azure_costs_{start_date}_{end_date}.csv'
    response['Content-Disposition'] = f'attachment; filename="{filename}"'

    writer = csv.writer(response)

    if export_format == 'daily_summary':
        # Daily summary export
        writer.writerow(['Date', 'Total Cost', 'Usage Cost', 'Purchase Cost', 'Record Count'])

        daily_costs = CostRecord.objects.filter(**filters).annotate(
            usage_date=TruncDate('charge_period_start')
        ).values('usage_date').annotate(
            total_cost=Sum('billed_cost'),
            usage_cost=Sum('billed_cost', filter=Q(charge_category='Usage')),
            purchase_cost=Sum('billed_cost', filter=Q(charge_category='Purchase')),
            record_count=Count('id'),
        ).order_by('usage_date')

        for record in daily_costs:
            writer.writerow([
                record['usage_date'],
                f"{float(record['total_cost'] or 0):.2f}",
                f"{float(record['usage_cost'] or 0):.2f}",
                f"{float(record['purchase_cost'] or 0):.2f}",
                record['record_count'],
            ])

    elif export_format == 'by_service':
        # Service breakdown export
        writer.writerow(['Service Name', 'Total Cost', 'Record Count', 'Percentage'])

        total = CostRecord.objects.filter(**filters).aggregate(
            total=Sum('billed_cost')
        )['total'] or 1

        services = CostRecord.objects.filter(**filters).values('service_name').annotate(
            total_cost=Sum('billed_cost'),
            record_count=Count('id'),
        ).order_by('-total_cost')

        for service in services:
            cost = float(service['total_cost'] or 0)
            percentage = (cost / float(total)) * 100
            writer.writerow([
                service['service_name'] or 'Unknown',
                f"{cost:.2f}",
                service['record_count'],
                f"{percentage:.1f}%",
            ])

    elif export_format == 'by_resource':
        # Resource breakdown export
        writer.writerow(['Resource Name', 'Resource Type', 'Service', 'Resource Group', 'Total Cost', 'Record Count'])

        resources = CostRecord.objects.filter(**filters).values(
            'resource_name', 'resource_type', 'service_name', 'resource_group_name'
        ).annotate(
            total_cost=Sum('billed_cost'),
            record_count=Count('id'),
        ).order_by('-total_cost')[:1000]  # Limit to top 1000 resources

        for resource in resources:
            writer.writerow([
                resource['resource_name'] or 'Unknown',
                resource['resource_type'] or 'Unknown',
                resource['service_name'] or 'Unknown',
                resource['resource_group_name'] or 'Unknown',
                f"{float(resource['total_cost'] or 0):.2f}",
                resource['record_count'],
            ])

    return response
