# power_up/finops/management/commands/sync_reservation_costs.py
"""
Management command to sync Azure Reservation costs from Retail Prices API.

This command detects reservations from FOCUS exports (CommitmentDiscountId),
queries Azure Reservation API for metadata, then fetches pricing from the
public Azure Retail Prices API to calculate monthly amortization.
"""

from django.core.management.base import BaseCommand
from django.utils import timezone
from power_up.finops.models import CostRecord, ReservationCost
import requests
import json
from decimal import Decimal
from datetime import datetime
import os


class Command(BaseCommand):
    help = 'Sync reservation costs from Azure Retail Prices API'

    def add_arguments(self, parser):
        parser.add_argument(
            '--force-refresh',
            action='store_true',
            help='Force refresh all reservations even if recently synced'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be synced without saving to database'
        )

    def handle(self, *args, **options):
        force_refresh = options['force_refresh']
        dry_run = options['dry_run']

        self.stdout.write('[OK] Starting reservation cost sync...')

        # Step 1: Find all unique reservations in FOCUS exports
        reservations = self.find_reservations()

        if not reservations:
            self.stdout.write(self.style.WARNING('No reservations found in FOCUS exports'))
            return

        self.stdout.write(f'[OK] Found {len(reservations)} unique reservation(s)')

        # Step 2: Process each reservation
        synced_count = 0
        skipped_count = 0
        error_count = 0

        for res in reservations:
            reservation_id = res['id']
            reservation_name = res['name']
            focus_data = res['focus_data']

            self.stdout.write(f'\n-> Processing: {reservation_name}')

            # Check if already synced recently (skip if < 24 hours and not forced)
            if not force_refresh:
                existing = ReservationCost.objects.filter(
                    reservation_id=reservation_id
                ).first()

                if existing:
                    hours_since_sync = (timezone.now() - existing.last_synced).total_seconds() / 3600
                    if hours_since_sync < 24:
                        self.stdout.write(f'   [SKIP] Last synced {hours_since_sync:.1f} hours ago')
                        skipped_count += 1
                        continue

            try:
                # Step 3: Query Azure Reservation API for metadata
                metadata = self.get_reservation_metadata(reservation_id)

                if not metadata:
                    self.stdout.write(self.style.ERROR(f'   [ERROR] Failed to fetch metadata'))
                    error_count += 1
                    continue

                # Step 4: Query Azure Retail Prices API for pricing using FOCUS data
                pricing = self.get_reservation_pricing(
                    service_name=metadata['service_name'],
                    region=metadata['region'],
                    term_years=metadata['term_years'],
                    focus_data=focus_data  # Pass FOCUS data for better matching
                )

                if not pricing:
                    self.stdout.write(self.style.WARNING(f'   [WARN] No pricing found in Retail Prices API'))
                    error_count += 1
                    continue

                # Step 5: Calculate monthly amortization
                monthly_cost = pricing['total_cost'] / (metadata['term_years'] * 12)

                self.stdout.write(f'   Total: EUR {pricing["total_cost"]:.2f}')
                self.stdout.write(f'   Monthly: EUR {monthly_cost:.2f}')
                self.stdout.write(f'   Term: {metadata["term_years"]} years')

                # Step 6: Save to database
                if not dry_run:
                    ReservationCost.objects.update_or_create(
                        reservation_id=reservation_id,
                        defaults={
                            'reservation_order_id': metadata['reservation_order_id'],
                            'reservation_name': reservation_name,
                            'sku_name': metadata['sku_name'],
                            'sku_description': metadata['sku_description'],
                            'service_name': metadata['service_name'],
                            'region': metadata['region'],
                            'purchase_date': metadata['purchase_date'],
                            'expiry_date': metadata['expiry_date'],
                            'term_months': metadata['term_years'] * 12,
                            'billing_plan': metadata['billing_plan'],
                            'quantity': metadata['quantity'],
                            'total_cost': Decimal(str(pricing['total_cost'])),
                            'monthly_amortization': Decimal(str(round(monthly_cost, 2))),
                            'currency': pricing['currency'],
                            'is_estimate': True,
                            'pricing_source': 'Azure Retail Prices API',
                            'metadata': {
                                'retail_price_per_unit': pricing['unit_price'],
                                'product_name': pricing['product_name'],
                                'synced_at': timezone.now().isoformat()
                            }
                        }
                    )
                    self.stdout.write(self.style.SUCCESS(f'   [OK] Saved to database'))
                    synced_count += 1
                else:
                    self.stdout.write(f'   [DRY RUN] Would save to database')
                    synced_count += 1

            except Exception as e:
                self.stdout.write(self.style.ERROR(f'   [ERROR] {str(e)}'))
                error_count += 1
                continue

        # Summary
        self.stdout.write(f'\n[OK] Sync completed!')
        self.stdout.write(f'   Synced: {synced_count}')
        self.stdout.write(f'   Skipped: {skipped_count}')
        self.stdout.write(f'   Errors: {error_count}')

    def find_reservations(self):
        """Find all unique reservations from FOCUS exports with extended data"""
        # Get full CostRecord objects to access extended_data
        records = CostRecord.objects.filter(
            extended_data__PricingCategory='Committed',
            extended_data__CommitmentDiscountType='Reservation'
        )

        result = []
        seen_ids = set()  # Deduplicate by reservation ID

        for record in records:
            commitment_id = record.extended_data.get('CommitmentDiscountId')
            commitment_name = record.extended_data.get('CommitmentDiscountName')

            if commitment_id and commitment_name:
                # Extract reservation ID from full path
                # Format: /providers/Microsoft.Capacity/reservationOrders/{order-id}/reservations/{reservation-id}
                parts = commitment_id.split('/')
                if 'reservations' in parts:
                    res_index = parts.index('reservations')
                    if res_index + 1 < len(parts):
                        reservation_id = parts[res_index + 1]

                        # Only add if not already seen
                        if reservation_id not in seen_ids:
                            seen_ids.add(reservation_id)
                            result.append({
                                'id': reservation_id,
                                'full_id': commitment_id,
                                'name': commitment_name,
                                'focus_data': record.extended_data  # Include FOCUS extended data
                            })

        return result

    def get_reservation_metadata(self, reservation_id):
        """Query Azure Reservation API for reservation details"""
        try:
            # First, we need the full reservation path - let's extract from FOCUS data
            # The reservation_id is just the GUID, but we need the full path from FOCUS
            records = CostRecord.objects.filter(
                extended_data__PricingCategory='Committed',
                extended_data__CommitmentDiscountType='Reservation'
            )

            full_id = None
            for record in records:
                commitment_id = record.extended_data.get('CommitmentDiscountId', '')
                if reservation_id in commitment_id:
                    full_id = commitment_id
                    break

            if not full_id:
                self.stdout.write(f'   [WARN] No FOCUS record found with reservation ID {reservation_id}')
                return None

            # Query Azure Management API using Managed Identity authentication
            # This works in both local dev (Azure CLI) and production (Managed Identity)
            access_token = self.get_azure_access_token()

            if not access_token:
                self.stdout.write(self.style.WARNING(f'   [WARN] Failed to get Azure access token'))
                return None

            # Call Azure Management API
            url = f'https://management.azure.com{full_id}?api-version=2022-11-01'
            headers = {
                'Authorization': f'Bearer {access_token}',
                'Content-Type': 'application/json'
            }

            response = requests.get(url, headers=headers, timeout=30)

            if response.status_code != 200:
                self.stdout.write(self.style.WARNING(f'   [WARN] Azure API error: {response.status_code} - {response.text[:200]}'))
                return None

            data = response.json()
            props = data.get('properties', {})

            # Extract metadata
            purchase_date_str = props.get('purchaseDate')
            expiry_date_str = props.get('expiryDate')
            term = props.get('term', 'P3Y')  # Default to 3 years

            # Parse term (P1Y = 1 year, P3Y = 3 years)
            term_years = int(term[1]) if term.startswith('P') and term.endswith('Y') else 3

            # Parse dates
            purchase_date = datetime.strptime(purchase_date_str, '%Y-%m-%d').date() if purchase_date_str else None
            expiry_date = datetime.strptime(expiry_date_str, '%Y-%m-%d').date() if expiry_date_str else None

            # Extract reservation order ID from full path
            parts = full_id.split('/')
            order_index = parts.index('reservationOrders') if 'reservationOrders' in parts else -1
            reservation_order_id = parts[order_index + 1] if order_index >= 0 and order_index + 1 < len(parts) else ''

            return {
                'reservation_order_id': reservation_order_id,
                'sku_name': data.get('sku', {}).get('name', ''),
                'sku_description': props.get('skuDescription', ''),
                'service_name': self.map_resource_type_to_service(props.get('reservedResourceType', '')),
                'region': data.get('location', ''),
                'purchase_date': purchase_date,
                'expiry_date': expiry_date,
                'term_years': term_years,
                'billing_plan': props.get('billingPlan', 'Monthly'),
                'quantity': props.get('quantity', 1)
            }

        except Exception as e:
            self.stdout.write(self.style.ERROR(f'   [ERROR] Metadata fetch failed: {str(e)}'))
            return None

    def get_reservation_pricing(self, service_name, region, term_years, focus_data):
        """
        Query Azure Retail Prices API for reservation pricing.

        Uses x_SkuMeterSubcategory from FOCUS data to match productName in API.
        This is more reliable than SKU name matching across different reservation types.
        """
        try:
            url = 'https://prices.azure.com/api/retail/prices'

            # Extract matching fields from FOCUS extended data
            meter_subcategory = focus_data.get('x_SkuMeterSubcategory')  # e.g., "Azure App Service Premium v3 Plan - Linux"
            term_months = int(focus_data.get('x_SkuTerm', term_years * 12))  # e.g., "36"

            # Extract SKU from x_SkuOrderName (format: "Product Name, SKU, Region, Term")
            sku_order_name = focus_data.get('x_SkuOrderName', '')
            focus_sku = None
            if ',' in sku_order_name:
                parts = sku_order_name.split(',')
                if len(parts) >= 2:
                    focus_sku = parts[1].strip()  # e.g., "P0v3"

            self.stdout.write(f'   Matching: {meter_subcategory} | SKU: {focus_sku} | {term_months} months')

            # Query API for all reservations of this service + region
            filter_query = f"serviceName eq '{service_name}' and armRegionName eq '{region}' and priceType eq 'Reservation'"
            params = {
                '$filter': filter_query,
                'currencyCode': 'EUR'
            }

            response = requests.get(url, params=params, timeout=30)
            response.raise_for_status()

            data = response.json()
            items = data.get('Items', [])

            if not items:
                self.stdout.write(self.style.WARNING(f'   [WARN] No reservations found for {service_name} in {region}'))
                return None

            # Strategy 1: Exact match on productName + SKU + term
            # API returns: "1 Year" or "3 Years" (note: plural for 3)
            term_str_singular = f'{term_years} Year'
            term_str_plural = f'{term_years} Years'

            for item in items:
                product_name = item.get('productName', '')
                api_sku = item.get('skuName', '')
                reservation_term = item.get('reservationTerm', '')

                # Exact match: productName AND SKU AND term all match
                if product_name == meter_subcategory and focus_sku:
                    # SKU match (case-insensitive, handle "P0v3" vs "P0 v3")
                    sku_match = (
                        api_sku.lower().replace(' ', '') == focus_sku.lower().replace(' ', '')
                    )

                    if sku_match and (reservation_term == term_str_singular or reservation_term == term_str_plural):
                        self.stdout.write(f'   [MATCH] Exact match: {product_name} | SKU: {api_sku}')
                        return {
                            'total_cost': item['unitPrice'],
                            'unit_price': item['unitPrice'],
                            'currency': item['currencyCode'],
                            'product_name': item['productName'],
                            'reservation_term': reservation_term,
                            'sku_name': item.get('skuName', 'N/A')
                        }

            # Strategy 2: Partial match (in case FOCUS has extra details)
            # Match if API productName is contained in FOCUS x_SkuMeterSubcategory
            for item in items:
                product_name = item.get('productName', '')
                reservation_term = item.get('reservationTerm', '')

                if meter_subcategory and product_name in meter_subcategory:
                    if reservation_term == term_str_singular or reservation_term == term_str_plural:
                        self.stdout.write(f'   [MATCH] Found partial match: {product_name}')
                        return {
                            'total_cost': item['unitPrice'],
                            'unit_price': item['unitPrice'],
                            'currency': item['currencyCode'],
                            'product_name': item['productName'],
                            'reservation_term': reservation_term,
                            'sku_name': item.get('skuName', 'N/A')
                        }

            # Strategy 3: Fallback - match only by term (prefer Linux)
            for item in items:
                product_name = item.get('productName', '').lower()
                reservation_term = item.get('reservationTerm', '')

                if (reservation_term == term_str_singular or reservation_term == term_str_plural):
                    if 'linux' in product_name:
                        self.stdout.write(f'   [WARN] Fallback match (term + Linux): {item["productName"]}')
                        return {
                            'total_cost': item['unitPrice'],
                            'unit_price': item['unitPrice'],
                            'currency': item['currencyCode'],
                            'product_name': item['productName'],
                            'reservation_term': reservation_term,
                            'sku_name': item.get('skuName', 'N/A')
                        }

            # Strategy 4: Last fallback - match only by term (any OS)
            for item in items:
                reservation_term = item.get('reservationTerm', '')
                if reservation_term == term_str_singular or reservation_term == term_str_plural:
                    self.stdout.write(f'   [WARN] Fallback match (term only): {item["productName"]}')
                    return {
                        'total_cost': item['unitPrice'],
                        'unit_price': item['unitPrice'],
                        'currency': item['currencyCode'],
                        'product_name': item['productName'],
                        'reservation_term': reservation_term,
                        'sku_name': item.get('skuName', 'N/A')
                    }

            self.stdout.write(self.style.WARNING(f'   [WARN] No matching reservation found'))
            return None

        except Exception as e:
            self.stdout.write(self.style.ERROR(f'   [ERROR] Pricing API failed: {str(e)}'))
            return None

    def get_azure_access_token(self):
        """
        Get Azure access token for Management API.

        Works in both environments:
        - Local dev: Uses Azure CLI credentials (az login)
        - Production: Uses Managed Identity (automatic in Azure App Service)
        """
        try:
            # Try Managed Identity first (production)
            from azure.identity import ManagedIdentityCredential, AzureCliCredential, ChainedTokenCredential

            # ChainedTokenCredential tries Managed Identity first, then Azure CLI
            credential = ChainedTokenCredential(
                ManagedIdentityCredential(),
                AzureCliCredential()
            )

            # Get token for Azure Management API
            token = credential.get_token('https://management.azure.com/.default')
            return token.token

        except ImportError:
            self.stdout.write(self.style.WARNING('   [WARN] azure-identity not installed, falling back to subprocess'))
            # Fallback to az CLI subprocess for local dev without azure-identity
            import subprocess
            import sys

            az_cmd = 'az.cmd' if sys.platform == 'win32' else 'az'

            try:
                result = subprocess.run(
                    [az_cmd, 'account', 'get-access-token', '--resource', 'https://management.azure.com'],
                    capture_output=True,
                    text=True,
                    check=False
                )

                if result.returncode == 0:
                    token_data = json.loads(result.stdout)
                    return token_data.get('accessToken')
                else:
                    self.stdout.write(self.style.WARNING(f'   [WARN] Azure CLI error: {result.stderr[:200]}'))
                    return None

            except FileNotFoundError:
                self.stdout.write(self.style.WARNING('   [WARN] Azure CLI not found - install azure-identity or run "az login"'))
                return None
            except Exception as e:
                self.stdout.write(self.style.WARNING(f'   [WARN] Token fetch failed: {str(e)}'))
                return None

        except Exception as e:
            self.stdout.write(self.style.WARNING(f'   [WARN] Authentication failed: {str(e)}'))
            return None

    @staticmethod
    def map_resource_type_to_service(resource_type):
        """Map Azure resource type to service name for Retail Prices API"""
        mapping = {
            'AppService': 'Azure App Service',
            'VirtualMachines': 'Virtual Machines',
            'SqlDatabase': 'SQL Database',
            'CosmosDb': 'Azure Cosmos DB',
            'SynapseAnalytics': 'Azure Synapse Analytics',
            'SqlDataWarehouse': 'Azure Synapse Analytics'
        }
        return mapping.get(resource_type, resource_type)
