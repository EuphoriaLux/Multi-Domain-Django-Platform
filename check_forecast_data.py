import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'azureproject.settings')
django.setup()

from power_up.finops.models import CostAggregation
from datetime import date, timedelta
from django.utils import timezone

# Check daily aggregations
daily = CostAggregation.objects.filter(
    aggregation_type='daily',
    dimension_type='overall'
).order_by('period_start')

print(f"Total daily aggregations: {daily.count()}")

if daily.exists():
    first = daily.first()
    last = daily.last()
    print(f"\nDate range: {first.period_start} to {last.period_start}")
    
    # Calculate number of days
    days = (last.period_start - first.period_start).days + 1
    print(f"Days of data: {days} days")
    print(f"\nForecast requirement: 30+ days")
    print(f"Status: {'✓ Sufficient' if days >= 30 else '✗ Insufficient'}")
    
    # Show recent data
    print(f"\nRecent 5 days:")
    for agg in daily.order_by('-period_start')[:5]:
        print(f"  {agg.period_start}: EUR {agg.total_cost:.2f}")
else:
    print("No daily aggregations found!")
