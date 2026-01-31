# power_up/finops/models.py
"""
FinOps Hub Models - Azure Cost Analytics

These models track Azure cost data imported from FOCUS exports.
db_table meta options preserve the original 'finops_hub_*' table names.
"""

from django.db import models
from django.utils import timezone
import json
import hashlib


class CostExport(models.Model):
    """Track processed Azure cost export files to avoid re-processing"""
    blob_path = models.CharField(max_length=500, unique=True, db_index=True)
    subscription_name = models.CharField(max_length=200, db_index=True)
    subscription_id = models.CharField(max_length=100, null=True, blank=True, db_index=True)
    billing_period_start = models.DateField(db_index=True)
    billing_period_end = models.DateField()
    file_size_bytes = models.BigIntegerField(null=True, blank=True)
    records_imported = models.IntegerField(default=0)
    import_started_at = models.DateTimeField(auto_now_add=True)
    import_completed_at = models.DateTimeField(null=True, blank=True)
    import_status = models.CharField(
        max_length=20,
        choices=[
            ('pending', 'Pending'),
            ('processing', 'Processing'),
            ('completed', 'Completed'),
            ('failed', 'Failed'),
            ('superseded', 'Superseded'),
        ],
        default='pending',
        db_index=True
    )
    error_message = models.TextField(null=True, blank=True)
    needs_subscription_id = models.BooleanField(default=False, db_index=True)

    # Auto-detection fields for updated exports
    blob_last_modified = models.DateTimeField(null=True, blank=True, db_index=True)
    blob_etag = models.CharField(max_length=100, null=True, blank=True)

    class Meta:
        db_table = 'finops_hub_costexport'
        ordering = ['-billing_period_start', '-import_started_at']
        indexes = [
            models.Index(fields=['subscription_name', 'billing_period_start']),
            models.Index(fields=['import_status', 'import_completed_at']),
        ]

    def __str__(self):
        return f"{self.subscription_name} ({self.billing_period_start} to {self.billing_period_end})"

    def mark_completed(self, records_count):
        """Mark export as successfully imported"""
        self.records_imported = records_count
        self.import_completed_at = timezone.now()
        self.import_status = 'completed'
        self.save()

    def mark_failed(self, error):
        """Mark export as failed with error message"""
        self.error_message = str(error)
        self.import_status = 'failed'
        self.import_completed_at = timezone.now()
        self.save()

    def is_incomplete(self):
        """Check if export is complete but has no records (needs subscription ID)"""
        return (
            self.import_status == 'completed' and
            self.records_imported == 0 and
            not self.subscription_id
        )

    @classmethod
    def get_incomplete_exports(cls):
        """Get all exports that completed but imported 0 records"""
        return cls.objects.filter(
            import_status='completed',
            records_imported=0
        ).exclude(
            subscription_id__isnull=False
        )

    def has_been_updated(self, blob_last_modified, blob_etag=None):
        """Check if the blob has been updated since last import"""
        if not self.blob_last_modified:
            return True
        if blob_last_modified and blob_last_modified > self.blob_last_modified:
            return True
        if blob_etag and self.blob_etag and blob_etag != self.blob_etag:
            return True
        return False

    def update_blob_metadata(self, blob_last_modified, blob_etag=None, file_size=None):
        """Update blob metadata for change tracking"""
        self.blob_last_modified = blob_last_modified
        if blob_etag:
            self.blob_etag = blob_etag
        if file_size:
            self.file_size_bytes = file_size
        self.save()


class CostRecord(models.Model):
    """Store individual cost records from FOCUS exports"""
    cost_export = models.ForeignKey(CostExport, on_delete=models.CASCADE, related_name='records')

    # Financial fields
    billed_cost = models.DecimalField(max_digits=12, decimal_places=4, db_index=True)
    billing_currency = models.CharField(max_length=10, db_index=True)
    effective_cost = models.DecimalField(max_digits=12, decimal_places=4, default=0)
    list_cost = models.DecimalField(max_digits=12, decimal_places=4, default=0)

    # Time fields
    billing_period_start = models.DateField(db_index=True)
    billing_period_end = models.DateField()
    charge_period_start = models.DateTimeField(db_index=True)
    charge_period_end = models.DateTimeField()

    # Subscription/Account fields
    billing_account_id = models.CharField(max_length=200, db_index=True)
    billing_account_name = models.CharField(max_length=200, null=True, blank=True)
    sub_account_id = models.CharField(max_length=200, db_index=True)
    sub_account_name = models.CharField(max_length=200, db_index=True)

    # Resource fields
    resource_id = models.TextField(db_index=True)
    resource_name = models.CharField(max_length=300, db_index=True, null=True, blank=True)
    resource_type = models.CharField(max_length=200, db_index=True, null=True, blank=True)
    resource_group_name = models.CharField(max_length=200, db_index=True, null=True, blank=True)

    # Service fields
    service_name = models.CharField(max_length=200, db_index=True)
    service_category = models.CharField(max_length=100, db_index=True, null=True, blank=True)

    # Provider/Region fields
    provider_name = models.CharField(max_length=100, default='Microsoft')
    region_id = models.CharField(max_length=100, db_index=True, null=True, blank=True)
    region_name = models.CharField(max_length=100, null=True, blank=True)

    # SKU fields
    sku_id = models.CharField(max_length=100, null=True, blank=True)
    sku_description = models.TextField(null=True, blank=True)
    sku_meter_category = models.CharField(max_length=200, null=True, blank=True)
    sku_meter_name = models.CharField(max_length=200, null=True, blank=True)

    # Charge details
    charge_category = models.CharField(max_length=50, db_index=True)
    charge_description = models.TextField(null=True, blank=True)
    charge_frequency = models.CharField(max_length=50, null=True, blank=True)

    # Quantity fields
    consumed_quantity = models.DecimalField(max_digits=20, decimal_places=6, default=0)
    consumed_unit = models.CharField(max_length=50, null=True, blank=True)
    pricing_quantity = models.DecimalField(max_digits=20, decimal_places=6, default=0)
    pricing_unit = models.CharField(max_length=50, null=True, blank=True)

    # Tags (JSON field for flexible tag storage)
    tags = models.JSONField(default=dict, blank=True)

    # Extended fields
    extended_data = models.JSONField(default=dict, blank=True)

    # Deduplication hash
    record_hash = models.CharField(max_length=64, unique=True, db_index=True, null=True)

    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'finops_hub_costrecord'
        ordering = ['-charge_period_start']
        indexes = [
            models.Index(fields=['billing_period_start', 'sub_account_name']),
            models.Index(fields=['service_name', 'billing_period_start']),
            models.Index(fields=['resource_group_name', 'billing_period_start']),
            models.Index(fields=['charge_period_start', 'billed_cost']),
            models.Index(fields=['resource_name', 'service_name']),
        ]

    def __str__(self):
        return f"{self.resource_name or 'Unknown'} - {self.service_name} - {self.billed_cost} {self.billing_currency}"

    def calculate_hash(self):
        """Calculate unique hash for this cost record based on key fields."""
        key_data = {
            'sub_account_id': self.sub_account_id or '',
            'resource_id': self.resource_id or '',
            'charge_period_start': str(self.charge_period_start) if self.charge_period_start else '',
            'charge_period_end': str(self.charge_period_end) if self.charge_period_end else '',
            'billed_cost': str(self.billed_cost),
            'billing_currency': self.billing_currency or '',
            'service_name': self.service_name or '',
            'charge_category': self.charge_category or '',
            'consumed_quantity': str(self.consumed_quantity),
        }
        hash_input = json.dumps(key_data, sort_keys=True, ensure_ascii=True)
        return hashlib.sha256(hash_input.encode('utf-8')).hexdigest()

    @staticmethod
    def generate_hash_from_dict(data):
        """Generate hash from parsed record dictionary (before model creation)."""
        key_data = {
            'sub_account_id': data.get('sub_account_id', ''),
            'resource_id': data.get('resource_id', ''),
            'charge_period_start': str(data.get('charge_period_start', '')),
            'charge_period_end': str(data.get('charge_period_end', '')),
            'billed_cost': str(data.get('billed_cost', 0)),
            'billing_currency': data.get('billing_currency', ''),
            'service_name': data.get('service_name', ''),
            'charge_category': data.get('charge_category', ''),
            'consumed_quantity': str(data.get('consumed_quantity', 0)),
        }
        hash_input = json.dumps(key_data, sort_keys=True, ensure_ascii=True)
        return hashlib.sha256(hash_input.encode('utf-8')).hexdigest()


class CostAggregation(models.Model):
    """Pre-computed cost aggregations for faster dashboard queries"""
    AGGREGATION_TYPES = [
        ('daily', 'Daily'),
        ('weekly', 'Weekly'),
        ('monthly', 'Monthly'),
    ]

    DIMENSION_TYPES = [
        ('overall', 'Overall'),
        ('subscription', 'By Subscription'),
        ('service', 'By Service'),
        ('resource_group', 'By Resource Group'),
        ('region', 'By Region'),
        ('resource', 'By Resource'),
    ]

    aggregation_type = models.CharField(max_length=20, choices=AGGREGATION_TYPES, db_index=True)
    dimension_type = models.CharField(max_length=50, choices=DIMENSION_TYPES, db_index=True)
    dimension_value = models.CharField(max_length=300, db_index=True)

    period_start = models.DateField(db_index=True)
    period_end = models.DateField()

    total_cost = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=10, default='EUR')

    record_count = models.IntegerField(default=0)

    # Breakdown by charge category
    usage_cost = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    purchase_cost = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    tax_cost = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    # Additional metadata
    top_services = models.JSONField(default=list, blank=True)
    top_resources = models.JSONField(default=list, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'finops_hub_costaggregation'
        ordering = ['-period_start', 'dimension_type', 'dimension_value']
        unique_together = [
            ['aggregation_type', 'dimension_type', 'dimension_value', 'period_start', 'currency']
        ]
        indexes = [
            models.Index(fields=['aggregation_type', 'period_start']),
            models.Index(fields=['dimension_type', 'dimension_value', 'period_start']),
        ]

    def __str__(self):
        return f"{self.get_aggregation_type_display()} - {self.dimension_value} ({self.period_start}): {self.total_cost} {self.currency}"


class CostAnomaly(models.Model):
    """
    Detected cost anomalies for alerting and monitoring.

    Anomalies are detected using statistical methods (2σ rule) and
    rule-based thresholds to identify unusual cost spikes.
    """

    SEVERITY_CHOICES = [
        ('low', 'Low'),
        ('medium', 'Medium'),
        ('high', 'High'),
        ('critical', 'Critical'),
    ]

    ANOMALY_TYPES = [
        ('spike', 'Cost Spike'),
        ('sudden_service', 'New Service Detected'),
        ('unusual_resource', 'Unusual Resource Cost'),
        ('sustained_increase', 'Sustained Cost Increase'),
    ]

    # When and what
    detected_date = models.DateField(db_index=True)
    anomaly_type = models.CharField(max_length=50, choices=ANOMALY_TYPES)
    severity = models.CharField(max_length=20, choices=SEVERITY_CHOICES, db_index=True)

    # What triggered it
    dimension_type = models.CharField(max_length=50)  # subscription, service, resource
    dimension_value = models.CharField(max_length=300)

    # Cost details
    actual_cost = models.DecimalField(max_digits=12, decimal_places=2)
    expected_cost = models.DecimalField(max_digits=12, decimal_places=2)
    deviation_percent = models.DecimalField(max_digits=10, decimal_places=2, help_text="Percentage deviation from expected cost (supports extreme spikes up to 99,999,999.99%)")
    currency = models.CharField(max_length=10, default='EUR')

    # Context
    description = models.TextField()
    metadata = models.JSONField(default=dict, blank=True)

    # Status tracking
    is_acknowledged = models.BooleanField(default=False, db_index=True)
    acknowledged_by = models.CharField(max_length=100, null=True, blank=True)
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    resolution_notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'finops_hub_costanomaly'
        ordering = ['-detected_date', '-severity']
        indexes = [
            models.Index(fields=['detected_date', 'severity']),
            models.Index(fields=['dimension_type', 'dimension_value']),
            models.Index(fields=['is_acknowledged', 'detected_date']),
        ]

    def __str__(self):
        return f"{self.get_severity_display()}: {self.dimension_value} on {self.detected_date}"


class CostForecast(models.Model):
    """
    Cost forecasts for budget planning and prediction.

    Uses linear regression with weekly seasonality adjustment to predict
    future costs based on historical data patterns.
    """

    # When this forecast is for
    forecast_date = models.DateField(db_index=True)
    generated_at = models.DateTimeField(auto_now_add=True)

    # What dimension (overall, subscription, service)
    dimension_type = models.CharField(max_length=50)
    dimension_value = models.CharField(max_length=300)

    # Forecast values
    forecast_cost = models.DecimalField(max_digits=12, decimal_places=2)
    lower_bound = models.DecimalField(max_digits=12, decimal_places=2)  # 95% CI
    upper_bound = models.DecimalField(max_digits=12, decimal_places=2)  # 95% CI
    confidence = models.DecimalField(max_digits=5, decimal_places=2)  # 0-100%
    currency = models.CharField(max_length=10, default='EUR')

    # Model metadata
    model_type = models.CharField(max_length=50, default='linear_regression')
    training_period_start = models.DateField()
    training_period_end = models.DateField()
    training_days = models.IntegerField()

    # Model accuracy metrics
    metadata = models.JSONField(default=dict, blank=True)  # R², RMSE, slope, intercept

    class Meta:
        db_table = 'finops_hub_costforecast'
        ordering = ['forecast_date']
        unique_together = [
            ['forecast_date', 'dimension_type', 'dimension_value']
        ]
        indexes = [
            models.Index(fields=['forecast_date', 'dimension_type']),
            models.Index(fields=['generated_at', 'dimension_type']),
        ]

    def __str__(self):
        return f"Forecast for {self.dimension_value} on {self.forecast_date}: {self.forecast_cost} {self.currency}"


class ReservationCost(models.Model):
    """
    Azure Reservation purchase costs and amortization.

    Tracks reservation purchases that don't appear in CSP Partner Led exports.
    Pricing is fetched from Azure Retail Prices API and amortized monthly.
    """

    # Reservation identification
    reservation_id = models.CharField(max_length=100, unique=True, db_index=True)
    reservation_order_id = models.CharField(max_length=100, db_index=True)
    reservation_name = models.CharField(max_length=200)

    # SKU and location
    sku_name = models.CharField(max_length=200)
    sku_description = models.TextField(blank=True)
    service_name = models.CharField(max_length=200, db_index=True)
    region = models.CharField(max_length=50, db_index=True)

    # Purchase details
    purchase_date = models.DateField(db_index=True)
    expiry_date = models.DateField()
    term_months = models.IntegerField()  # 12 for 1-year, 36 for 3-year
    billing_plan = models.CharField(max_length=20, default='Monthly')  # Monthly, Upfront
    quantity = models.IntegerField(default=1)

    # Cost details
    total_cost = models.DecimalField(max_digits=12, decimal_places=2)
    monthly_amortization = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=10, default='EUR')

    # Data source tracking
    is_estimate = models.BooleanField(default=True, db_index=True)  # True = from Retail API, False = from invoice
    pricing_source = models.CharField(max_length=50, default='Azure Retail Prices API')
    last_synced = models.DateTimeField(auto_now=True)

    # Metadata
    metadata = models.JSONField(default=dict, blank=True)  # Store raw API response
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'finops_hub_reservationcost'
        ordering = ['-purchase_date']
        indexes = [
            models.Index(fields=['reservation_id', 'is_estimate']),
            models.Index(fields=['service_name', 'region']),
            models.Index(fields=['purchase_date', 'expiry_date']),
        ]

    def __str__(self):
        return f"{self.reservation_name} - {self.monthly_amortization} {self.currency}/month"

    def is_active(self, check_date=None):
        """Check if reservation is active on given date"""
        from django.utils import timezone
        if check_date is None:
            check_date = timezone.now().date()
        return self.purchase_date <= check_date <= self.expiry_date

    def get_amortized_cost_for_period(self, start_date, end_date):
        """
        Calculate amortized cost for a specific date range.

        Uses monthly charge divided by actual days in each month (28-31),
        matching how Microsoft attributes reservation costs in billing.

        This ensures full months always total to exactly the monthly_amortization amount.
        Example: February (28 days) = €24.83/28/day, January (31 days) = €24.83/31/day
        """
        from datetime import timedelta
        from calendar import monthrange
        from decimal import Decimal

        # Find overlap between reservation period and requested period
        overlap_start = max(self.purchase_date, start_date)
        overlap_end = min(self.expiry_date, end_date)

        if overlap_start > overlap_end:
            return 0  # No overlap

        # Calculate cost day-by-day, using the correct daily rate for each month
        total_cost = Decimal('0.00')
        current_date = overlap_start

        while current_date <= overlap_end:
            # Get the number of days in this specific month
            days_in_current_month = monthrange(current_date.year, current_date.month)[1]

            # Calculate daily rate for this month
            daily_rate = self.monthly_amortization / days_in_current_month

            # Add this day's cost
            total_cost += daily_rate

            # Move to next day
            current_date += timedelta(days=1)

        return float(total_cost)
