"""
Management command to import cost data from a local CSV file (for testing)
Usage:
    python manage.py import_local_csv part_0_0001.csv
    python manage.py import_local_csv part_0_0001.csv --subscription "PartnerLed-test"
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from finops_hub.models import CostExport, CostRecord
from finops_hub.utils.focus_parser import FOCUSParser
import csv
import traceback
from datetime import datetime, date


class Command(BaseCommand):
    help = 'Import Azure cost data from a local CSV file (for testing)'

    def add_arguments(self, parser):
        parser.add_argument(
            'csv_file',
            type=str,
            help='Path to CSV file',
        )
        parser.add_argument(
            '--subscription',
            type=str,
            default='Test-Subscription',
            help='Subscription name (default: Test-Subscription)',
        )
        parser.add_argument(
            '--batch-size',
            type=int,
            default=100,
            help='Number of records to insert per batch (default: 100)',
        )

    def handle(self, *args, **options):
        csv_file = options.get('csv_file')
        subscription_name = options.get('subscription', 'Test-Subscription')
        batch_size = options.get('batch_size', 100)

        self.stdout.write(self.style.SUCCESS('Starting Local CSV Import'))
        self.stdout.write(f'CSV file: {csv_file}')
        self.stdout.write(f'Subscription: {subscription_name}')
        self.stdout.write(f'Batch size: {batch_size}')

        try:
            # Read CSV file
            self.stdout.write('Opening CSV file...')

            # Create or get CostExport record
            today = date.today()
            cost_export, created = CostExport.objects.get_or_create(
                blob_path=f'local/{csv_file}',
                defaults={
                    'subscription_name': subscription_name,
                    'billing_period_start': today,
                    'billing_period_end': today,
                    'import_status': 'pending',
                }
            )

            # Mark as processing
            cost_export.import_status = 'processing'
            cost_export.save()

            # Parse CSV
            records_imported = 0
            parser = FOCUSParser()
            batch = []

            with open(csv_file, 'r', encoding='utf-8-sig') as f:
                csv_reader = csv.DictReader(f)

                self.stdout.write(f'CSV columns: {len(csv_reader.fieldnames)} fields')
                self.stdout.write('Processing rows...')

                for idx, row in enumerate(csv_reader, 1):
                    try:
                        # Parse record
                        parsed = parser.parse_cost_record(row)

                        # Validate
                        is_valid, error_msg = parser.validate_record(parsed)
                        if not is_valid:
                            self.stderr.write(f'  Row {idx}: Skipping invalid record - {error_msg}')
                            continue

                        batch.append(parsed)

                        # Batch insert
                        if len(batch) >= batch_size:
                            cost_records = [
                                CostRecord(cost_export=cost_export, **record)
                                for record in batch
                            ]

                            with transaction.atomic():
                                CostRecord.objects.bulk_create(cost_records, batch_size=batch_size)

                            records_imported += len(batch)
                            self.stdout.write(f'  Imported {records_imported} records...', ending='')
                            self.stdout.flush()
                            batch = []

                    except Exception as e:
                        self.stderr.write(f'  Row {idx}: Failed to parse - {str(e)}')
                        continue

                # Insert remaining records
                if batch:
                    cost_records = [
                        CostRecord(cost_export=cost_export, **record)
                        for record in batch
                    ]

                    with transaction.atomic():
                        CostRecord.objects.bulk_create(cost_records, batch_size=batch_size)

                    records_imported += len(batch)

            # Mark as completed
            cost_export.mark_completed(records_imported)

            self.stdout.write(self.style.SUCCESS(
                f'\n✓ Import completed! Total records imported: {records_imported}'
            ))

            # Show summary
            self.stdout.write('\nSummary:')
            self.stdout.write(f'  Subscription: {subscription_name}')
            self.stdout.write(f'  Records: {records_imported}')
            self.stdout.write(f'  Export ID: {cost_export.id}')

            # Show sample data
            self.stdout.write('\nSample records:')
            sample_records = CostRecord.objects.filter(cost_export=cost_export).order_by('-billed_cost')[:5]
            for record in sample_records:
                self.stdout.write(
                    f'  - {record.resource_name or "N/A"} | '
                    f'{record.service_name} | '
                    f'{record.billed_cost} {record.billing_currency}'
                )

        except FileNotFoundError:
            raise CommandError(f'CSV file not found: {csv_file}')
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'\n✗ Import failed: {str(e)}'))
            self.stderr.write(traceback.format_exc())
            raise CommandError(f'Import failed: {str(e)}')
