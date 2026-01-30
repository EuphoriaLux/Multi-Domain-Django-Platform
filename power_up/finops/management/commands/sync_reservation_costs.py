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

                # Step 4: Query Azure Retail Prices API for pricing
                pricing = self.get_reservation_pricing(
                    service_name=metadata['service_name'],
                    sku_name=metadata['sku_name'],
                    region=metadata['region'],
                    term_years=metadata['term_years']
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
        """Find all unique reservations from FOCUS exports"""
        reservations = CostRecord.objects.filter(
            extended_data__PricingCategory='Committed',
            extended_data__CommitmentDiscountType='Reservation'
        ).values(
            'extended_data__CommitmentDiscountId',
            'extended_data__CommitmentDiscountName'
        ).distinct()

        result = []
        seen_ids = set()  # Deduplicate by reservation ID

        for res in reservations:
            commitment_id = res.get('extended_data__CommitmentDiscountId')
            commitment_name = res.get('extended_data__CommitmentDiscountName')

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
                                'name': commitment_name
                            })

        return result

    def get_reservation_metadata(self, reservation_id):
        """Query Azure Reservation API for reservation details"""
        try:
            # Use az rest to query Azure Management API
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

            # Query Azure Reservation API using Azure CLI subprocess
            # Note: We use subprocess instead of direct API calls to leverage existing az login session
            import subprocess
            import sys

            # Build command - use full path to az.cmd on Windows
            az_cmd = 'az.cmd' if sys.platform == 'win32' else 'az'

            cmd = [
                az_cmd, 'rest',
                '--method', 'get',
                '--url', f'https://management.azure.com{full_id}?api-version=2022-11-01'
            ]

            try:
                result = subprocess.run(cmd, capture_output=True, text=True, check=False)

                if result.returncode != 0:
                    self.stdout.write(self.style.WARNING(f'   [WARN] Azure API error: {result.stderr[:200]}'))
                    return None

                data = json.loads(result.stdout)
            except FileNotFoundError:
                self.stdout.write(self.style.WARNING(f'   [WARN] Azure CLI (az) not found in PATH'))
                return None
            except json.JSONDecodeError as e:
                self.stdout.write(self.style.WARNING(f'   [WARN] Invalid JSON from Azure API: {str(e)}'))
                return None
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

    def get_reservation_pricing(self, service_name, sku_name, region, term_years=3):
        """Query Azure Retail Prices API for reservation pricing"""
        try:
            url = 'https://prices.azure.com/api/retail/prices'

            # Build filter query
            # Note: SKU names in Retail API may differ from Reservation API
            # We need to search more broadly
            filter_parts = [
                f"serviceName eq '{service_name}'",
                f"armRegionName eq '{region}'",
                f"priceType eq 'Reservation'"
            ]

            # Try to match SKU components
            if 'p0' in sku_name.lower():
                filter_parts.append("(skuName eq 'P0 v3' or contains(skuName, 'P0v3'))")

            filter_query = ' and '.join(filter_parts)

            params = {
                '$filter': filter_query,
                'currencyCode': 'EUR'
            }

            response = requests.get(url, params=params, timeout=30)
            response.raise_for_status()

            data = response.json()
            items = data.get('Items', [])

            if not items:
                # Try broader search without SKU
                filter_query = f"serviceName eq '{service_name}' and armRegionName eq '{region}' and priceType eq 'Reservation'"
                params['$filter'] = filter_query

                response = requests.get(url, params=params, timeout=30)
                response.raise_for_status()
                data = response.json()
                items = data.get('Items', [])

            if not items:
                return None

            # Find matching term
            term_str = f'{term_years} year'
            for item in items:
                product_name = item.get('productName', '').lower()
                if term_str in product_name and 'linux' in product_name.lower():
                    return {
                        'total_cost': item['unitPrice'],
                        'unit_price': item['unitPrice'],
                        'currency': item['currencyCode'],
                        'product_name': item['productName']
                    }

            # Fallback: use first item
            if items:
                item = items[0]
                return {
                    'total_cost': item['unitPrice'],
                    'unit_price': item['unitPrice'],
                    'currency': item['currencyCode'],
                    'product_name': item['productName']
                }

            return None

        except Exception as e:
            self.stdout.write(self.style.ERROR(f'   [ERROR] Pricing API failed: {str(e)}'))
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
