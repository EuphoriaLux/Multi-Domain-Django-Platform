# power_up/finops/admin.py
"""
FinOps Hub Admin Configuration for Power-Up Admin Site
"""

from django.contrib import admin
from power_up.admin import power_up_admin_site
from .models import CostExport, CostRecord, CostAggregation


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


# Register models with power_up_admin_site
power_up_admin_site.register(CostExport, CostExportAdmin)
power_up_admin_site.register(CostRecord, CostRecordAdmin)
power_up_admin_site.register(CostAggregation, CostAggregationAdmin)
