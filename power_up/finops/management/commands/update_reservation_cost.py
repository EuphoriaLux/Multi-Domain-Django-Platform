# power_up/finops/management/commands/update_reservation_cost.py
"""
Management command to manually update reservation cost with actual value from Azure Portal.

This is useful when the Azure Retail Prices API returns different pricing than
what the CSP partner actually charges (which is common in CSP Partner Led subscriptions).
"""

from django.core.management.base import BaseCommand
from power_up.finops.models import ReservationCost
from decimal import Decimal


class Command(BaseCommand):
    help = 'Manually update reservation cost with actual value from Azure Portal'

    def add_arguments(self, parser):
        parser.add_argument(
            '--reservation-id',
            type=str,
            required=True,
            help='Reservation ID (short form, e.g., f54668b3-19a5-47ae-a943-6b0b44e06358)'
        )
        parser.add_argument(
            '--monthly-cost',
            type=float,
            required=True,
            help='Actual monthly cost from Azure Portal (e.g., 24.83)'
        )
        parser.add_argument(
            '--source',
            type=str,
            default='Azure Portal (Manual)',
            help='Source of the cost data'
        )
        parser.add_argument(
            '--currency',
            type=str,
            default='EUR',
            help='Currency code'
        )

    def handle(self, *args, **options):
        reservation_id = options['reservation_id']
        monthly_cost = options['monthly_cost']
        source = options['source']
        currency = options['currency']

        # Find the reservation
        try:
            reservation = ReservationCost.objects.get(
                reservation_id__contains=reservation_id
            )
        except ReservationCost.DoesNotExist:
            self.stdout.write(self.style.ERROR(f'Reservation not found: {reservation_id}'))
            return
        except ReservationCost.MultipleObjectsReturned:
            self.stdout.write(self.style.ERROR(f'Multiple reservations found with ID: {reservation_id}'))
            return

        # Calculate total cost from monthly (reverse amortization)
        total_cost = monthly_cost * reservation.term_months

        # Store old values for comparison
        old_monthly = float(reservation.monthly_amortization)
        old_total = float(reservation.total_cost)
        old_source = reservation.pricing_source

        # Update with actual values
        reservation.monthly_amortization = Decimal(str(monthly_cost))
        reservation.total_cost = Decimal(str(total_cost))
        reservation.currency = currency
        reservation.is_estimate = False  # Mark as actual (not estimated)
        reservation.pricing_source = source
        reservation.save()

        self.stdout.write(self.style.SUCCESS('\n[OK] Reservation cost updated!'))
        self.stdout.write(f'\nReservation: {reservation.reservation_name}')
        self.stdout.write(f'SKU: {reservation.sku_name}')
        self.stdout.write(f'Term: {reservation.term_months} months')
        self.stdout.write(f'\nOld values:')
        self.stdout.write(f'  Monthly: {currency} {old_monthly:.2f}')
        self.stdout.write(f'  Total: {currency} {old_total:.2f}')
        self.stdout.write(f'  Source: {old_source}')
        self.stdout.write(f'\nNew values:')
        self.stdout.write(f'  Monthly: {currency} {monthly_cost:.2f}')
        self.stdout.write(f'  Total: {currency} {total_cost:.2f}')
        self.stdout.write(f'  Source: {source}')
        self.stdout.write(f'  Is Estimate: {reservation.is_estimate}')
        self.stdout.write(f'\nDifference: {currency} {monthly_cost - old_monthly:.2f}/month')
