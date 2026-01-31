# power_up/finops/admin.py
"""
FinOps Hub Admin Configuration for Power-Up Admin Site
"""

from django.contrib import admin
from django.utils.html import format_html
from power_up.admin import power_up_admin_site
from .models import (
    CostExport,
    CostRecord,
    CostAggregation,
    CostAnomaly,
    CostForecast,
    ReservationCost
)


class CostExportAdmin(admin.ModelAdmin):
    list_display = ['subscription_name', 'subscription_id', 'billing_period_start',
                    'billing_period_end', 'records_imported', 'import_status',
                    'needs_subscription_id', 'import_completed_at']
    list_filter = ['import_status', 'needs_subscription_id', 'subscription_name', 'billing_period_start']
    search_fields = ['blob_path', 'subscription_name', 'subscription_id']
    readonly_fields = ['blob_path', 'import_started_at', 'import_completed_at',
                       'records_imported', 'file_size_bytes']

    fieldsets = [
        ('Export Information', {
            'fields': ['blob_path', 'subscription_name', 'subscription_id',
                      'billing_period_start', 'billing_period_end', 'file_size_bytes']
        }),
        ('Import Status', {
            'fields': ['import_status', 'needs_subscription_id', 'records_imported',
                      'import_started_at', 'import_completed_at', 'error_message']
        }),
    ]


class CostRecordAdmin(admin.ModelAdmin):
    list_display = ['resource_name', 'service_name', 'billed_cost', 'billing_currency',
                    'charge_period_start', 'sub_account_name']
    list_filter = ['service_name', 'sub_account_name', 'resource_group_name',
                   'charge_category', 'billing_period_start']
    search_fields = ['resource_name', 'resource_id', 'charge_description']
    readonly_fields = ['cost_export', 'created_at']
    date_hierarchy = 'charge_period_start'

    fieldsets = [
        ('Financial Information', {
            'fields': ['billed_cost', 'effective_cost', 'list_cost', 'billing_currency']
        }),
        ('Time Period', {
            'fields': ['billing_period_start', 'billing_period_end',
                      'charge_period_start', 'charge_period_end']
        }),
        ('Subscription/Account', {
            'fields': ['sub_account_id', 'sub_account_name', 'billing_account_id',
                      'billing_account_name']
        }),
        ('Resource Details', {
            'fields': ['resource_id', 'resource_name', 'resource_type', 'resource_group_name']
        }),
        ('Service Information', {
            'fields': ['service_name', 'service_category', 'provider_name']
        }),
        ('Region', {
            'fields': ['region_id', 'region_name']
        }),
        ('SKU Details', {
            'fields': ['sku_id', 'sku_description', 'sku_meter_category', 'sku_meter_name']
        }),
        ('Charge Details', {
            'fields': ['charge_category', 'charge_description', 'charge_frequency',
                      'consumed_quantity', 'consumed_unit', 'pricing_quantity', 'pricing_unit']
        }),
        ('Tags & Metadata', {
            'fields': ['tags', 'extended_data', 'cost_export', 'created_at'],
            'classes': ['collapse']
        }),
    ]


class CostAggregationAdmin(admin.ModelAdmin):
    list_display = ['aggregation_type', 'dimension_type', 'dimension_value',
                    'period_start', 'total_cost', 'currency', 'record_count']
    list_filter = ['aggregation_type', 'dimension_type', 'currency', 'period_start']
    search_fields = ['dimension_value']
    readonly_fields = ['created_at', 'updated_at']
    date_hierarchy = 'period_start'

    fieldsets = [
        ('Aggregation Details', {
            'fields': ['aggregation_type', 'dimension_type', 'dimension_value',
                      'period_start', 'period_end']
        }),
        ('Cost Summary', {
            'fields': ['total_cost', 'currency', 'record_count', 'usage_cost',
                      'purchase_cost', 'tax_cost']
        }),
        ('Top Items', {
            'fields': ['top_services', 'top_resources'],
            'classes': ['collapse']
        }),
        ('Metadata', {
            'fields': ['created_at', 'updated_at'],
            'classes': ['collapse']
        }),
    ]


class CostAnomalyAdmin(admin.ModelAdmin):
    list_display = ['detected_date', 'severity_badge', 'anomaly_type', 'dimension_value',
                    'actual_cost', 'expected_cost', 'deviation_display', 'acknowledgment_status']
    list_filter = ['severity', 'anomaly_type', 'dimension_type', 'is_acknowledged', 'detected_date']
    search_fields = ['dimension_value', 'description']
    readonly_fields = ['created_at']
    date_hierarchy = 'detected_date'

    fieldsets = [
        ('Anomaly Information', {
            'fields': ['detected_date', 'anomaly_type', 'severity']
        }),
        ('Affected Resource', {
            'fields': ['dimension_type', 'dimension_value']
        }),
        ('Cost Analysis', {
            'fields': ['actual_cost', 'expected_cost', 'deviation_percent', 'currency']
        }),
        ('Acknowledgment', {
            'fields': ['is_acknowledged', 'acknowledged_by', 'acknowledged_at', 'resolution_notes']
        }),
        ('Details', {
            'fields': ['description', 'metadata', 'created_at'],
            'classes': ['collapse']
        }),
    ]

    def acknowledgment_status(self, obj):
        """Display acknowledgment status with badge"""
        if obj.is_acknowledged:
            return format_html(
                '<span style="background-color: #28a745; color: white; padding: 3px 8px; border-radius: 3px; font-size: 11px;">✓ ACKNOWLEDGED</span>'
            )
        return format_html(
            '<span style="background-color: #ffc107; color: #000; padding: 3px 8px; border-radius: 3px; font-size: 11px;">⚠ PENDING</span>'
        )
    acknowledgment_status.short_description = 'Status'

    def severity_badge(self, obj):
        """Display severity with color badge"""
        colors = {
            'low': '#6c757d',      # Gray
            'medium': '#ffc107',   # Yellow
            'high': '#fd7e14',     # Orange
            'critical': '#dc3545' # Red
        }
        color = colors.get(obj.severity, '#6c757d')
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 8px; border-radius: 3px; font-weight: bold;">{}</span>',
            color, obj.get_severity_display().upper()
        )
    severity_badge.short_description = 'Severity'

    def deviation_display(self, obj):
        """Display deviation percentage with color coding"""
        if obj.deviation_percent > 300:
            color = '#dc3545'  # Red
        elif obj.deviation_percent > 200:
            color = '#fd7e14'  # Orange
        elif obj.deviation_percent > 100:
            color = '#ffc107'  # Yellow
        else:
            color = '#28a745'  # Green

        return format_html(
            '<span style="color: {}; font-weight: bold;">+{:.1f}%</span>',
            color, obj.deviation_percent
        )
    deviation_display.short_description = 'Deviation'


class CostForecastAdmin(admin.ModelAdmin):
    list_display = ['forecast_date', 'dimension_type', 'dimension_value',
                    'forecast_cost', 'confidence_interval', 'currency', 'generated_at']
    list_filter = ['dimension_type', 'forecast_date', 'generated_at']
    search_fields = ['dimension_value']
    readonly_fields = ['generated_at']
    date_hierarchy = 'forecast_date'

    fieldsets = [
        ('Forecast Details', {
            'fields': ['forecast_date', 'dimension_type', 'dimension_value']
        }),
        ('Prediction', {
            'fields': ['forecast_cost', 'lower_bound', 'upper_bound', 'confidence', 'currency']
        }),
        ('Model Information', {
            'fields': ['model_type', 'training_period_start', 'training_period_end',
                      'training_days', 'metadata', 'generated_at'],
            'classes': ['collapse']
        }),
    ]

    def confidence_interval(self, obj):
        """Display confidence interval range"""
        return format_html(
            '€{:.2f} - €{:.2f}',
            obj.lower_bound, obj.upper_bound
        )
    confidence_interval.short_description = 'Confidence Interval (95%)'


class ReservationCostAdmin(admin.ModelAdmin):
    list_display = ['reservation_name', 'service_name', 'region', 'monthly_amortization',
                    'term_display', 'purchase_date', 'expiry_date', 'is_estimate_badge']
    list_filter = ['service_name', 'region', 'is_estimate', 'billing_plan', 'purchase_date']
    search_fields = ['reservation_name', 'reservation_id', 'sku_name', 'sku_description']
    readonly_fields = ['reservation_id', 'reservation_order_id', 'last_synced',
                       'created_at', 'updated_at']
    date_hierarchy = 'purchase_date'

    fieldsets = [
        ('Reservation Information', {
            'fields': ['reservation_id', 'reservation_order_id', 'reservation_name',
                      'service_name', 'region']
        }),
        ('SKU Details', {
            'fields': ['sku_name', 'sku_description']
        }),
        ('Term & Dates', {
            'fields': ['purchase_date', 'expiry_date', 'term_months', 'billing_plan']
        }),
        ('Cost Information', {
            'fields': ['total_cost', 'monthly_amortization', 'currency', 'quantity']
        }),
        ('Data Source', {
            'fields': ['is_estimate', 'pricing_source', 'last_synced'],
            'classes': ['collapse']
        }),
        ('Metadata', {
            'fields': ['metadata', 'created_at', 'updated_at'],
            'classes': ['collapse']
        }),
    ]

    def term_display(self, obj):
        """Display term in human-readable format"""
        years = obj.term_months / 12
        if years == int(years):
            return f'{int(years)} year{"s" if years > 1 else ""}'
        return f'{obj.term_months} months'
    term_display.short_description = 'Term'

    def is_estimate_badge(self, obj):
        """Display estimate status with badge"""
        if obj.is_estimate:
            return format_html(
                '<span style="background-color: #ffc107; color: #000; padding: 3px 8px; border-radius: 3px; font-size: 11px;">ESTIMATE</span>'
            )
        return format_html(
            '<span style="background-color: #28a745; color: white; padding: 3px 8px; border-radius: 3px; font-size: 11px;">CONFIRMED</span>'
        )
    is_estimate_badge.short_description = 'Status'


# Register models with power_up_admin_site
power_up_admin_site.register(CostExport, CostExportAdmin)
power_up_admin_site.register(CostRecord, CostRecordAdmin)
power_up_admin_site.register(CostAggregation, CostAggregationAdmin)
power_up_admin_site.register(CostAnomaly, CostAnomalyAdmin)
power_up_admin_site.register(CostForecast, CostForecastAdmin)
power_up_admin_site.register(ReservationCost, ReservationCostAdmin)
