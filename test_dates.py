"""
Quick script to check date ranges in imported cost data
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'azureproject.settings')
django.setup()

from finops_hub.models import CostRecord
from django.db.models import Min, Max, Count

# Get date range
date_stats = CostRecord.objects.aggregate(
    min_date=Min('billing_period_start'),
    max_date=Max('billing_period_start'),
    total_records=Count('id')
)

print("=== Cost Data Date Range ===")
print(f"Earliest date: {date_stats['min_date']}")
print(f"Latest date: {date_stats['max_date']}")
print(f"Total records: {date_stats['total_records']}")

# Get records per day
daily_counts = CostRecord.objects.values('billing_period_start').annotate(
    count=Count('id')
).order_by('billing_period_start')

print("\n=== Records per Day ===")
for day in daily_counts:
    print(f"{day['billing_period_start']}: {day['count']} records")

# Check today's date
from django.utils import timezone
print(f"\n=== Today's Date ===")
print(f"Today: {timezone.now().date()}")
