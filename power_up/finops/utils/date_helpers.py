"""
Shared date range resolution for FinOps views and API endpoints.

Eliminates ~200 lines of duplicated date range + bounds-clamping code.
"""

from datetime import timedelta
from django.db.models import Min, Max
from django.db.models.functions import TruncDate
from django.utils import timezone


def resolve_date_range(
    request_params,
    queryset=None,
    date_field='charge_period_start',
    default_days=30,
    use_month_filter=False,
):
    """
    Resolve start_date, end_date, and days from request parameters,
    clamping to actual data bounds when the requested range falls outside.

    Args:
        request_params: dict-like (request.GET or request.query_params)
        queryset: Optional queryset to detect min/max dates. If None, uses
                  CostRecord.objects.all().
        date_field: The datetime field to aggregate on (default: charge_period_start)
        default_days: Fallback number of days (default: 30)
        use_month_filter: If True, check for 'month' param (YYYY-MM format)

    Returns:
        dict with keys: start_date, end_date, days, month_filter
    """
    from power_up.finops.models import CostRecord

    if queryset is None:
        queryset = CostRecord.objects.all()

    month_filter = None

    if use_month_filter:
        month_filter = request_params.get('month')

    if month_filter:
        try:
            year, month = map(int, month_filter.split('-'))
            start_date = timezone.datetime(year, month, 1).date()
            if month == 12:
                end_date = timezone.datetime(year + 1, 1, 1).date() - timedelta(days=1)
            else:
                end_date = timezone.datetime(year, month + 1, 1).date() - timedelta(days=1)
            days = (end_date - start_date).days + 1
        except (ValueError, AttributeError):
            end_date = timezone.now().date()
            start_date = end_date.replace(day=1)
            days = (end_date - start_date).days + 1
            month_filter = f"{end_date.year}-{end_date.month:02d}"
    else:
        try:
            days = int(request_params.get('days', default_days))
            days = max(1, min(3650, days))
        except (ValueError, TypeError):
            days = default_days

        end_date = timezone.now().date()
        start_date = end_date - timedelta(days=days)

        # Clamp to actual data bounds
        use_trunc = date_field == 'charge_period_start'
        if use_trunc:
            date_range = queryset.aggregate(
                min_date=Min(TruncDate(date_field)),
                max_date=Max(TruncDate(date_field))
            )
        else:
            date_range = queryset.aggregate(
                min_date=Min(date_field),
                max_date=Max(date_field)
            )

        if date_range['min_date'] and date_range['max_date']:
            if start_date > date_range['max_date'] or end_date < date_range['min_date']:
                end_date = date_range['max_date']
                start_date = end_date - timedelta(days=days - 1)
                if start_date < date_range['min_date']:
                    start_date = date_range['min_date']

    return {
        'start_date': start_date,
        'end_date': end_date,
        'days': days,
        'month_filter': month_filter,
    }
