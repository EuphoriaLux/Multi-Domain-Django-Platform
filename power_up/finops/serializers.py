# power_up/finops/serializers.py
"""
FinOps Hub REST API Serializers
"""

from rest_framework import serializers
from .models import CostExport, CostRecord, CostAggregation


class CostExportSerializer(serializers.ModelSerializer):
    """Serializer for cost export metadata"""

    class Meta:
        model = CostExport
        fields = [
            'id', 'blob_path', 'subscription_name', 'billing_period_start',
            'billing_period_end', 'file_size_bytes', 'records_imported',
            'import_started_at', 'import_completed_at', 'import_status',
            'error_message'
        ]
        read_only_fields = fields


class CostRecordSerializer(serializers.ModelSerializer):
    """Serializer for individual cost records"""

    class Meta:
        model = CostRecord
        fields = [
            'id', 'billed_cost', 'billing_currency', 'effective_cost', 'list_cost',
            'billing_period_start', 'billing_period_end', 'charge_period_start',
            'charge_period_end', 'sub_account_name', 'resource_name', 'resource_type',
            'resource_group_name', 'service_name', 'service_category', 'region_name',
            'charge_category', 'charge_description', 'consumed_quantity',
            'consumed_unit', 'tags'
        ]
        read_only_fields = fields


class CostRecordSummarySerializer(serializers.ModelSerializer):
    """Lightweight serializer for cost record lists"""

    class Meta:
        model = CostRecord
        fields = [
            'id', 'resource_name', 'service_name', 'billed_cost',
            'billing_currency', 'charge_period_start', 'sub_account_name'
        ]
        read_only_fields = fields


class CostAggregationSerializer(serializers.ModelSerializer):
    """Serializer for cost aggregations"""

    class Meta:
        model = CostAggregation
        fields = [
            'id', 'aggregation_type', 'dimension_type', 'dimension_value',
            'period_start', 'period_end', 'total_cost', 'currency',
            'record_count', 'usage_cost', 'purchase_cost', 'tax_cost',
            'top_services', 'top_resources', 'created_at', 'updated_at'
        ]
        read_only_fields = fields
