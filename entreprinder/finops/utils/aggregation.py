"""
Cost aggregation logic for pre-computing dashboard queries
"""
from django.db import models, transaction
from django.db.models import Sum, Count, Q
from django.utils import timezone
from datetime import datetime, timedelta
from decimal import Decimal
from entreprinder.finops.models import CostRecord, CostAggregation


class CostAggregator:
    """Generate pre-computed cost aggregations for faster dashboard queries"""

    @staticmethod
    def aggregate_daily(start_date=None, end_date=None, currency='EUR'):
        """
        Generate daily cost aggregations

        Args:
            start_date: Start date (default: 30 days ago)
            end_date: End date (default: today)
            currency: Currency filter (default: EUR)

        Returns:
            int: Number of aggregation records created
        """
        if not end_date:
            end_date = timezone.now().date()
        if not start_date:
            start_date = end_date - timedelta(days=30)

        records_created = 0

        # Iterate through each day
        current_date = start_date
        while current_date <= end_date:
            # Overall daily aggregation
            records_created += CostAggregator._aggregate_for_day(
                current_date, 'overall', 'Total', currency
            )

            # By subscription
            subscriptions = CostRecord.objects.filter(
                billing_period_start=current_date,
                billing_currency=currency
            ).values_list('sub_account_name', flat=True).distinct()

            for subscription in subscriptions:
                if subscription:
                    records_created += CostAggregator._aggregate_for_day(
                        current_date, 'subscription', subscription, currency
                    )

            # By service
            services = CostRecord.objects.filter(
                billing_period_start=current_date,
                billing_currency=currency
            ).values_list('service_name', flat=True).distinct()

            for service in services:
                if service:
                    records_created += CostAggregator._aggregate_for_day(
                        current_date, 'service', service, currency
                    )

            # By resource group
            resource_groups = CostRecord.objects.filter(
                billing_period_start=current_date,
                billing_currency=currency
            ).values_list('resource_group_name', flat=True).distinct()

            for rg in resource_groups:
                if rg:
                    records_created += CostAggregator._aggregate_for_day(
                        current_date, 'resource_group', rg, currency
                    )

            current_date += timedelta(days=1)

        return records_created

    @staticmethod
    def _aggregate_for_day(date, dimension_type, dimension_value, currency):
        """Generate aggregation for a single day and dimension"""
        # Build query filters
        filters = {
            'billing_period_start': date,
            'billing_currency': currency,
        }

        if dimension_type == 'subscription':
            filters['sub_account_name'] = dimension_value
        elif dimension_type == 'service':
            filters['service_name'] = dimension_value
        elif dimension_type == 'resource_group':
            filters['resource_group_name'] = dimension_value
        elif dimension_type == 'region':
            filters['region_name'] = dimension_value

        # Query cost data
        queryset = CostRecord.objects.filter(**filters)

        if not queryset.exists():
            return 0

        # Aggregate totals
        aggregates = queryset.aggregate(
            total_cost=Sum('billed_cost'),
            record_count=Count('id'),
            usage_cost=Sum('billed_cost', filter=Q(charge_category='Usage')),
            purchase_cost=Sum('billed_cost', filter=Q(charge_category='Purchase')),
            tax_cost=Sum('billed_cost', filter=Q(charge_category='Tax')),
        )

        # Get top services (for non-service dimensions)
        top_services = []
        if dimension_type != 'service':
            top_services_qs = queryset.values('service_name').annotate(
                cost=Sum('billed_cost')
            ).order_by('-cost')[:5]

            top_services = [
                {'name': item['service_name'], 'cost': float(item['cost'])}
                for item in top_services_qs
            ]

        # Get top resources
        top_resources_qs = queryset.values('resource_name').annotate(
            cost=Sum('billed_cost')
        ).order_by('-cost')[:5]

        top_resources = [
            {'name': item['resource_name'], 'cost': float(item['cost'])}
            for item in top_resources_qs if item['resource_name']
        ]

        # Create or update aggregation
        aggregation, created = CostAggregation.objects.update_or_create(
            aggregation_type='daily',
            dimension_type=dimension_type,
            dimension_value=dimension_value,
            period_start=date,
            currency=currency,
            defaults={
                'period_end': date,
                'total_cost': aggregates['total_cost'] or Decimal('0.00'),
                'record_count': aggregates['record_count'] or 0,
                'usage_cost': aggregates['usage_cost'] or Decimal('0.00'),
                'purchase_cost': aggregates['purchase_cost'] or Decimal('0.00'),
                'tax_cost': aggregates['tax_cost'] or Decimal('0.00'),
                'top_services': top_services,
                'top_resources': top_resources,
            }
        )

        return 1 if created else 0

    @staticmethod
    def aggregate_monthly(year, month, currency='EUR'):
        """
        Generate monthly cost aggregations

        Args:
            year: Year (e.g., 2025)
            month: Month (1-12)
            currency: Currency filter

        Returns:
            int: Number of aggregation records created
        """
        # Calculate month start and end
        from calendar import monthrange
        start_date = datetime(year, month, 1).date()
        last_day = monthrange(year, month)[1]
        end_date = datetime(year, month, last_day).date()

        records_created = 0

        # Overall monthly aggregation
        records_created += CostAggregator._aggregate_for_period(
            start_date, end_date, 'monthly', 'overall', 'Total', currency
        )

        # By subscription
        subscriptions = CostRecord.objects.filter(
            billing_period_start__gte=start_date,
            billing_period_start__lte=end_date,
            billing_currency=currency
        ).values_list('sub_account_name', flat=True).distinct()

        for subscription in subscriptions:
            if subscription:
                records_created += CostAggregator._aggregate_for_period(
                    start_date, end_date, 'monthly', 'subscription', subscription, currency
                )

        # By service
        services = CostRecord.objects.filter(
            billing_period_start__gte=start_date,
            billing_period_start__lte=end_date,
            billing_currency=currency
        ).values_list('service_name', flat=True).distinct()

        for service in services:
            if service:
                records_created += CostAggregator._aggregate_for_period(
                    start_date, end_date, 'monthly', 'service', service, currency
                )

        return records_created

    @staticmethod
    def _aggregate_for_period(start_date, end_date, agg_type, dimension_type, dimension_value, currency):
        """Generate aggregation for a date range"""
        # Build query filters
        filters = {
            'billing_period_start__gte': start_date,
            'billing_period_start__lte': end_date,
            'billing_currency': currency,
        }

        if dimension_type == 'subscription':
            filters['sub_account_name'] = dimension_value
        elif dimension_type == 'service':
            filters['service_name'] = dimension_value
        elif dimension_type == 'resource_group':
            filters['resource_group_name'] = dimension_value

        # Query cost data
        queryset = CostRecord.objects.filter(**filters)

        if not queryset.exists():
            return 0

        # Aggregate totals
        aggregates = queryset.aggregate(
            total_cost=Sum('billed_cost'),
            record_count=Count('id'),
            usage_cost=Sum('billed_cost', filter=Q(charge_category='Usage')),
            purchase_cost=Sum('billed_cost', filter=Q(charge_category='Purchase')),
            tax_cost=Sum('billed_cost', filter=Q(charge_category='Tax')),
        )

        # Get top services
        top_services = []
        if dimension_type != 'service':
            top_services_qs = queryset.values('service_name').annotate(
                cost=Sum('billed_cost')
            ).order_by('-cost')[:5]

            top_services = [
                {'name': item['service_name'], 'cost': float(item['cost'])}
                for item in top_services_qs
            ]

        # Get top resources
        top_resources_qs = queryset.values('resource_name').annotate(
            cost=Sum('billed_cost')
        ).order_by('-cost')[:5]

        top_resources = [
            {'name': item['resource_name'], 'cost': float(item['cost'])}
            for item in top_resources_qs if item['resource_name']
        ]

        # Create or update aggregation
        aggregation, created = CostAggregation.objects.update_or_create(
            aggregation_type=agg_type,
            dimension_type=dimension_type,
            dimension_value=dimension_value,
            period_start=start_date,
            currency=currency,
            defaults={
                'period_end': end_date,
                'total_cost': aggregates['total_cost'] or Decimal('0.00'),
                'record_count': aggregates['record_count'] or 0,
                'usage_cost': aggregates['usage_cost'] or Decimal('0.00'),
                'purchase_cost': aggregates['purchase_cost'] or Decimal('0.00'),
                'tax_cost': aggregates['tax_cost'] or Decimal('0.00'),
                'top_services': top_services,
                'top_resources': top_resources,
            }
        )

        return 1 if created else 0

    @staticmethod
    def refresh_all(days_back=30, currency='EUR'):
        """
        Refresh all aggregations for the past N days

        Args:
            days_back: Number of days to aggregate (default: 30)
            currency: Currency filter

        Returns:
            dict: Summary of aggregations created
        """
        end_date = timezone.now().date()
        start_date = end_date - timedelta(days=days_back)

        # Generate daily aggregations
        daily_count = CostAggregator.aggregate_daily(start_date, end_date, currency)

        # Generate monthly aggregations
        monthly_count = 0
        current_date = start_date
        processed_months = set()

        while current_date <= end_date:
            year_month = (current_date.year, current_date.month)
            if year_month not in processed_months:
                monthly_count += CostAggregator.aggregate_monthly(
                    current_date.year, current_date.month, currency
                )
                processed_months.add(year_month)

            current_date += timedelta(days=1)

        return {
            'daily_aggregations': daily_count,
            'monthly_aggregations': monthly_count,
            'period': f'{start_date} to {end_date}',
        }
