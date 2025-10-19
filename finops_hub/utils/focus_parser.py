"""
FOCUS (FinOps Open Cost and Usage Specification) CSV parser
Handles 96-column Azure cost export format
"""
from decimal import Decimal, InvalidOperation
from datetime import datetime
import json


class FOCUSParser:
    """
    Parse FOCUS-formatted CSV records into Django model-friendly dictionaries
    Handles type conversion, null values, and JSON field parsing
    """

    # Column name mappings (handle BOM and normalize)
    COLUMN_MAPPINGS = {
        '\ufeffBilledCost': 'BilledCost',  # Handle BOM in first column
    }

    @staticmethod
    def normalize_column_name(col_name):
        """Normalize column names (strip BOM, whitespace)"""
        return FOCUSParser.COLUMN_MAPPINGS.get(col_name, col_name.strip())

    @staticmethod
    def parse_decimal(value, default=Decimal('0.00')):
        """Parse decimal value, return default if invalid"""
        if not value or value == '':
            return default
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError):
            return default

    @staticmethod
    def parse_datetime(value):
        """Parse ISO 8601 datetime string"""
        if not value or value == '':
            return None
        try:
            # Azure uses format: 2025-10-16T00:00:00Z
            return datetime.fromisoformat(value.replace('Z', '+00:00'))
        except (ValueError, AttributeError):
            return None

    @staticmethod
    def parse_date(value):
        """Parse ISO 8601 date string"""
        if not value or value == '':
            return None
        try:
            dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
            return dt.date()
        except (ValueError, AttributeError):
            return None

    @staticmethod
    def parse_json_tags(tags_str):
        """Parse tags from JSON string format"""
        if not tags_str or tags_str == '':
            return {}
        try:
            return json.loads(tags_str)
        except (json.JSONDecodeError, TypeError):
            return {}

    @staticmethod
    def normalize_row(row):
        """Normalize column names in a row dict"""
        normalized = {}
        for key, value in row.items():
            normalized_key = FOCUSParser.normalize_column_name(key)
            normalized[normalized_key] = value
        return normalized

    @staticmethod
    def parse_cost_record(row, calculate_hash=True):
        """
        Parse a FOCUS CSV row into a dict suitable for CostRecord model

        Args:
            row: Dictionary from csv.DictReader (raw CSV row)
            calculate_hash: Whether to calculate record_hash (default: True)

        Returns:
            dict: Parsed data for CostRecord.objects.create()
        """
        # Normalize column names (handle BOM)
        row = FOCUSParser.normalize_row(row)

        # Parse tags
        tags = FOCUSParser.parse_json_tags(row.get('Tags', ''))

        # Build parsed record
        parsed = {
            # Financial fields
            'billed_cost': FOCUSParser.parse_decimal(row.get('BilledCost')),
            'billing_currency': row.get('BillingCurrency', 'EUR'),
            'effective_cost': FOCUSParser.parse_decimal(row.get('EffectiveCost')),
            'list_cost': FOCUSParser.parse_decimal(row.get('ListCost')),

            # Time fields
            'billing_period_start': FOCUSParser.parse_date(row.get('BillingPeriodStart')),
            'billing_period_end': FOCUSParser.parse_date(row.get('BillingPeriodEnd')),
            'charge_period_start': FOCUSParser.parse_datetime(row.get('ChargePeriodStart')),
            'charge_period_end': FOCUSParser.parse_datetime(row.get('ChargePeriodEnd')),

            # Subscription/Account fields
            'billing_account_id': row.get('BillingAccountId', ''),
            'billing_account_name': row.get('BillingAccountName', ''),
            'sub_account_id': row.get('SubAccountId', ''),
            'sub_account_name': row.get('SubAccountName', ''),

            # Resource fields
            'resource_id': row.get('ResourceId', ''),
            'resource_name': row.get('ResourceName', ''),
            'resource_type': row.get('ResourceType', ''),
            'resource_group_name': row.get('x_ResourceGroupName', ''),  # x_ prefix for Azure-specific

            # Service fields
            'service_name': row.get('ServiceName', ''),
            'service_category': row.get('ServiceCategory', ''),

            # Provider/Region fields
            'provider_name': row.get('ProviderName', 'Microsoft'),
            'region_id': row.get('RegionId', ''),
            'region_name': row.get('RegionName', ''),

            # SKU fields
            'sku_id': row.get('SkuId', ''),
            'sku_description': row.get('x_SkuDescription', ''),
            'sku_meter_category': row.get('x_SkuMeterCategory', ''),
            'sku_meter_name': row.get('x_SkuMeterName', ''),

            # Charge details
            'charge_category': row.get('ChargeCategory', ''),
            'charge_description': row.get('ChargeDescription', ''),
            'charge_frequency': row.get('ChargeFrequency', ''),

            # Quantity fields
            'consumed_quantity': FOCUSParser.parse_decimal(row.get('ConsumedQuantity')),
            'consumed_unit': row.get('ConsumedUnit', ''),
            'pricing_quantity': FOCUSParser.parse_decimal(row.get('PricingQuantity')),
            'pricing_unit': row.get('PricingUnit', ''),

            # Tags
            'tags': tags,

            # Extended data (store all remaining columns not explicitly modeled)
            'extended_data': FOCUSParser.build_extended_data(row),
        }

        # Calculate hash for deduplication
        if calculate_hash:
            from finops_hub.models import CostRecord
            parsed['record_hash'] = CostRecord.generate_hash_from_dict(parsed)

        return parsed

    @staticmethod
    def build_extended_data(row):
        """
        Store non-core FOCUS fields in extended_data JSON

        Fields to preserve:
        - x_* prefixed Azure-specific fields not in main model
        - Commitment/discount fields
        - Pricing details
        - Publisher info
        """
        extended = {}

        # Azure-specific extended fields
        azure_x_fields = [
            'x_AccountId', 'x_AccountName', 'x_AccountOwnerId',
            'x_BilledCostInUsd', 'x_BilledUnitPrice',
            'x_BillingExchangeRate', 'x_BillingExchangeRateDate',
            'x_BillingProfileId', 'x_BillingProfileName',
            'x_ContractedCostInUsd', 'x_CostAllocationRuleName',
            'x_CostCenter', 'x_CustomerId', 'x_CustomerName',
            'x_EffectiveCostInUsd', 'x_EffectiveUnitPrice',
            'x_InvoiceId', 'x_InvoiceIssuerId',
            'x_InvoiceSectionId', 'x_InvoiceSectionName',
            'x_ListCostInUsd', 'x_PartnerCreditApplied',
            'x_PartnerCreditRate', 'x_PricingBlockSize',
            'x_PricingCurrency', 'x_PricingSubcategory',
            'x_PricingUnitDescription', 'x_PublisherCategory',
            'x_PublisherId', 'x_ResellerId', 'x_ResellerName',
            'x_ResourceType', 'x_ServicePeriodEnd', 'x_ServicePeriodStart',
            'x_SkuDetails', 'x_SkuIsCreditEligible',
            'x_SkuMeterId', 'x_SkuMeterSubcategory',
            'x_SkuOfferId', 'x_SkuOrderId', 'x_SkuOrderName',
            'x_SkuPartNumber', 'x_SkuRegion',
            'x_SkuServiceFamily', 'x_SkuTerm', 'x_SkuTier',
        ]

        for field in azure_x_fields:
            value = row.get(field, '')
            if value and value != '':
                extended[field] = value

        # Commitment/discount fields
        commitment_fields = [
            'CommitmentDiscountCategory', 'CommitmentDiscountId',
            'CommitmentDiscountName', 'CommitmentDiscountStatus',
            'CommitmentDiscountType',
        ]

        for field in commitment_fields:
            value = row.get(field, '')
            if value and value != '':
                extended[field] = value

        # Pricing fields
        pricing_fields = [
            'ContractedCost', 'ContractedUnitPrice',
            'ListUnitPrice', 'PricingCategory',
        ]

        for field in pricing_fields:
            value = row.get(field, '')
            if value and value != '':
                extended[field] = value

        # Publisher fields
        publisher_fields = ['PublisherName', 'InvoiceIssuerName']
        for field in publisher_fields:
            value = row.get(field, '')
            if value and value != '':
                extended[field] = value

        # SKU price info
        sku_price_fields = ['SkuPriceId']
        for field in sku_price_fields:
            value = row.get(field, '')
            if value and value != '':
                extended[field] = value

        return extended

    @staticmethod
    def validate_record(parsed_record):
        """
        Validate that a parsed record has minimum required fields

        Returns:
            tuple: (is_valid: bool, error_message: str or None)
        """
        required_fields = [
            'billed_cost',
            'billing_currency',
            'billing_period_start',
            'charge_period_start',
            'sub_account_id',
            'service_name',
        ]

        for field in required_fields:
            if not parsed_record.get(field):
                return False, f"Missing required field: {field}"

        return True, None
