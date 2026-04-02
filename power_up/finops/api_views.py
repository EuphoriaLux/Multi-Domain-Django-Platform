# power_up/finops/api_views.py
"""
REST API views for FinOps Hub
"""

from rest_framework import viewsets, status
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.http import HttpResponse
from django.db.models import Sum, Count, Q, Avg
from django.db.models.functions import Lower, TruncDate
from django.utils import timezone
from datetime import timedelta
import csv

from .models import CostExport, CostRecord, CostAggregation, CostAnomaly
from .utils.date_helpers import resolve_date_range
from .serializers import (
    CostExportSerializer,
    CostRecordSerializer,
    CostRecordSummarySerializer,
    CostAggregationSerializer
)
from .permissions import HasSessionOrIsAuthenticated


class CostExportViewSet(viewsets.ReadOnlyModelViewSet):
    """API endpoint for cost export metadata"""
    queryset = CostExport.objects.all()
    serializer_class = CostExportSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ['subscription_name', 'import_status']
    ordering_fields = ['billing_period_start', 'import_completed_at']
    ordering = ['-billing_period_start']


class CostRecordViewSet(viewsets.ReadOnlyModelViewSet):
    """API endpoint for detailed cost records"""
    queryset = CostRecord.objects.select_related('cost_export')
    permission_classes = [IsAuthenticated]
    filterset_fields = [
        'service_name', 'sub_account_name', 'resource_group_name',
        'charge_category', 'billing_period_start'
    ]
    ordering_fields = ['charge_period_start', 'billed_cost']
    ordering = ['-charge_period_start']

    def get_serializer_class(self):
        if self.action == 'list':
            return CostRecordSummarySerializer
        return CostRecordSerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        start_date = self.request.query_params.get('start_date')
        end_date = self.request.query_params.get('end_date')
        if start_date:
            queryset = queryset.filter(billing_period_start__gte=start_date)
        if end_date:
            queryset = queryset.filter(billing_period_start__lte=end_date)
        return queryset


class CostAggregationViewSet(viewsets.ReadOnlyModelViewSet):
    """API endpoint for pre-computed cost aggregations"""
    queryset = CostAggregation.objects.all()
    serializer_class = CostAggregationSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ['aggregation_type', 'dimension_type', 'dimension_value', 'currency']
    ordering_fields = ['period_start', 'total_cost']
    ordering = ['-period_start']

    def get_queryset(self):
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
    """Get overall cost summary"""
    currency = request.query_params.get('currency', 'EUR')

    date_info = resolve_date_range(
        request.query_params,
        queryset=CostRecord.objects.filter(billing_currency=currency),
        date_field='billing_period_start',
    )
    start_date = date_info['start_date']
    end_date = date_info['end_date']
    days = date_info['days']

    records = CostRecord.objects.filter(
        billing_period_start__gte=start_date,
        billing_period_start__lte=end_date,
        billing_currency=currency
    )

    summary = records.aggregate(
        total_cost=Sum('billed_cost'),
        total_records=Count('id'),
        avg_cost_per_day=Avg('billed_cost'),
        usage_cost=Sum('billed_cost', filter=Q(charge_category='Usage')),
        purchase_cost=Sum('billed_cost', filter=Q(charge_category='Purchase')),
    )

    top_subscriptions = records.values('sub_account_name').annotate(
        cost=Sum('billed_cost')
    ).order_by('-cost')[:5]

    top_services = records.values('service_name').annotate(
        cost=Sum('billed_cost')
    ).order_by('-cost')[:10]

    top_resource_groups = records.annotate(
        rg_lower=Lower('resource_group_name')
    ).values('rg_lower').annotate(
        cost=Sum('billed_cost')
    ).order_by('-cost')[:10]

    daily_costs = records.annotate(
        usage_date=TruncDate('charge_period_start')
    ).values('usage_date').annotate(
        cost=Sum('billed_cost')
    ).order_by('usage_date')

    return Response({
        'period': {'start': start_date, 'end': end_date, 'days': days},
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
        'top_resource_groups': [
            {'resource_group_name': rg['rg_lower'], 'cost': rg['cost']}
            for rg in top_resource_groups
        ],
        'daily_trend': list(daily_costs),
    })


@api_view(['GET'])
@permission_classes([HasSessionOrIsAuthenticated])
def costs_by_subscription(request):
    """Get costs broken down by subscription"""
    currency = request.query_params.get('currency', 'EUR')

    date_info = resolve_date_range(
        request.query_params,
        queryset=CostAggregation.objects.filter(dimension_type='subscription', currency=currency),
        date_field='period_start',
    )
    start_date = date_info['start_date']
    end_date = date_info['end_date']

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
    """Get costs broken down by Azure service"""
    currency = request.query_params.get('currency', 'EUR')
    subscription = request.query_params.get('subscription')

    base_filters = {'billing_currency': currency}
    if subscription:
        base_filters['sub_account_name'] = subscription

    date_info = resolve_date_range(
        request.query_params,
        queryset=CostRecord.objects.filter(**base_filters),
        date_field='billing_period_start',
    )
    start_date = date_info['start_date']
    end_date = date_info['end_date']

    filters = {
        **base_filters,
        'billing_period_start__gte': start_date,
        'billing_period_start__lte': end_date,
    }

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
    """Get costs broken down by resource group"""
    currency = request.query_params.get('currency', 'EUR')
    subscription = request.query_params.get('subscription')

    base_filters = {'billing_currency': currency}
    if subscription:
        base_filters['sub_account_name'] = subscription

    date_info = resolve_date_range(
        request.query_params,
        queryset=CostRecord.objects.filter(**base_filters),
        date_field='billing_period_start',
    )
    start_date = date_info['start_date']
    end_date = date_info['end_date']

    filters = {
        **base_filters,
        'billing_period_start__gte': start_date,
        'billing_period_start__lte': end_date,
    }

    resource_groups = CostRecord.objects.filter(**filters).annotate(
        rg_lower=Lower('resource_group_name')
    ).values('rg_lower').annotate(
        total_cost=Sum('billed_cost'),
        record_count=Count('id'),
    ).order_by('-total_cost')

    return Response({
        'period': {'start': start_date, 'end': end_date},
        'currency': currency,
        'subscription': subscription,
        'resource_groups': [
            {'resource_group_name': rg['rg_lower'], 'total_cost': rg['total_cost'], 'record_count': rg['record_count']}
            for rg in resource_groups
        ],
    })


@api_view(['GET'])
@permission_classes([HasSessionOrIsAuthenticated])
def cost_trend(request):
    """Get daily cost trend data"""
    currency = request.query_params.get('currency', 'EUR')
    subscription = request.query_params.get('subscription')
    service = request.query_params.get('service')

    base_filters = {'billing_currency': currency}
    if subscription:
        base_filters['sub_account_name'] = subscription
    if service:
        base_filters['service_name'] = service

    date_info = resolve_date_range(
        request.query_params,
        queryset=CostRecord.objects.filter(**base_filters),
    )
    start_date = date_info['start_date']
    end_date = date_info['end_date']

    daily_costs = CostRecord.objects.filter(**base_filters).annotate(
        usage_date=TruncDate('charge_period_start')
    ).filter(
        usage_date__gte=start_date,
        usage_date__lte=end_date
    ).values('usage_date').annotate(
        total_cost=Sum('billed_cost'),
        usage_cost=Sum('billed_cost', filter=Q(charge_category='Usage')),
        purchase_cost=Sum('billed_cost', filter=Q(charge_category='Purchase')),
        record_count=Count('id'),
    ).order_by('usage_date')

    return Response({
        'period': {'start': start_date, 'end': end_date},
        'currency': currency,
        'filters': {'subscription': subscription, 'service': service},
        'daily_costs': list(daily_costs),
    })


@api_view(['GET'])
@permission_classes([HasSessionOrIsAuthenticated])
def export_status(request):
    """Get status of cost data imports"""
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
def cost_anomalies(request):
    """Get recent cost anomalies"""
    days = int(request.query_params.get('days', 30))
    severity = request.query_params.get('severity')
    acknowledged = request.query_params.get('acknowledged')

    start_date = timezone.now().date() - timedelta(days=days)

    queryset = CostAnomaly.objects.filter(detected_date__gte=start_date)

    if severity:
        queryset = queryset.filter(severity=severity)
    if acknowledged is not None:
        queryset = queryset.filter(is_acknowledged=acknowledged.lower() == 'true')

    anomalies = queryset.order_by('-detected_date', '-severity')[:100]

    data = [{
        'id': a.id,
        'detected_date': a.detected_date.isoformat(),
        'severity': a.severity,
        'type': a.anomaly_type,
        'dimension_type': a.dimension_type,
        'dimension_value': a.dimension_value,
        'actual_cost': float(a.actual_cost),
        'expected_cost': float(a.expected_cost),
        'deviation_percent': float(a.deviation_percent),
        'description': a.description,
        'is_acknowledged': a.is_acknowledged,
    } for a in anomalies]

    return Response({
        'success': True,
        'count': len(data),
        'anomalies': data
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def acknowledge_anomaly(request, anomaly_id):
    """Acknowledge a cost anomaly (staff only)"""
    if not request.user.is_staff:
        return Response(
            {'success': False, 'error': 'Staff permission required'},
            status=status.HTTP_403_FORBIDDEN
        )

    try:
        anomaly = CostAnomaly.objects.get(id=anomaly_id)
        anomaly.is_acknowledged = True
        anomaly.acknowledged_by = request.user.username
        anomaly.acknowledged_at = timezone.now()
        anomaly.save()

        return Response({
            'success': True,
            'message': 'Anomaly acknowledged successfully'
        })
    except CostAnomaly.DoesNotExist:
        return Response(
            {'success': False, 'error': 'Anomaly not found'},
            status=status.HTTP_404_NOT_FOUND
        )


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def export_costs_csv(request):
    """Export cost data as CSV file."""
    currency = request.query_params.get('currency', 'EUR')
    subscription = request.query_params.get('subscription')
    export_format = request.query_params.get('format', 'daily_summary')

    base_filters = {'billing_currency': currency}
    if subscription:
        base_filters['sub_account_name'] = subscription

    date_info = resolve_date_range(
        request.query_params,
        queryset=CostRecord.objects.filter(**base_filters),
        date_field='billing_period_start',
    )
    start_date = date_info['start_date']
    end_date = date_info['end_date']

    filters = {
        **base_filters,
        'billing_period_start__gte': start_date,
        'billing_period_start__lte': end_date,
    }

    response = HttpResponse(content_type='text/csv')
    filename = f'azure_costs_{start_date}_{end_date}.csv'
    response['Content-Disposition'] = f'attachment; filename="{filename}"'

    writer = csv.writer(response)

    if export_format == 'daily_summary':
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
        writer.writerow(['Service Name', 'Total Cost', 'Record Count', 'Percentage'])
        total = CostRecord.objects.filter(**filters).aggregate(total=Sum('billed_cost'))['total'] or 1
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
        writer.writerow(['Resource Name', 'Resource Type', 'Service', 'Resource Group', 'Total Cost', 'Record Count'])
        resources = CostRecord.objects.filter(**filters).values(
            'resource_name', 'resource_type', 'service_name', 'resource_group_name'
        ).annotate(
            total_cost=Sum('billed_cost'),
            record_count=Count('id'),
        ).order_by('-total_cost')[:1000]

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
