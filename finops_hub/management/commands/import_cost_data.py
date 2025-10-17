"""
Management command to import Azure cost data from Blob Storage
Usage:
    python manage.py import_cost_data                    # Import all unprocessed exports
    python manage.py import_cost_data --subscription PartnerLed-power_up  # Filter by subscription
    python manage.py import_cost_data --force             # Re-import all (ignore processed status)
    python manage.py import_cost_data --limit 5           # Process only first 5 exports
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from finops_hub.models import CostExport, CostRecord
from finops_hub.utils.blob_reader import AzureCostBlobReader
from finops_hub.utils.focus_parser import FOCUSParser
import traceback


class Command(BaseCommand):
    help = 'Import Azure cost data from Blob Storage msexports container'

    def add_arguments(self, parser):
        parser.add_argument(
            '--subscription',
            type=str,
            help='Filter by subscription name (e.g., PartnerLed-power_up)',
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Re-import already processed exports',
        )
        parser.add_argument(
            '--limit',
            type=int,
            help='Maximum number of exports to process',
        )
        parser.add_argument(
            '--batch-size',
            type=int,
            default=1000,
            help='Number of records to insert per batch (default: 1000)',
        )
        parser.add_argument(
            '--skip-aggregation',
            action='store_true',
            help='Skip automatic aggregation refresh after import',
        )

    def handle(self, *args, **options):
        subscription_filter = options.get('subscription')
        force_reimport = options.get('force', False)
        limit = options.get('limit')
        batch_size = options.get('batch_size', 1000)
        skip_aggregation = options.get('skip_aggregation', False)

        self.stdout.write(self.style.SUCCESS('Starting Azure Cost Data Import'))
        self.stdout.write(f'Subscription filter: {subscription_filter or "All"}')
        self.stdout.write(f'Force re-import: {force_reimport}')
        self.stdout.write(f'Batch size: {batch_size}')
        self.stdout.write(f'Skip aggregation: {skip_aggregation}')

        try:
            # Initialize Azure Blob reader
            self.stdout.write('Connecting to Azure Blob Storage...')
            blob_reader = AzureCostBlobReader()

            # List available cost exports
            self.stdout.write('Scanning for cost export files...')
            exports = blob_reader.list_cost_exports(
                subscription_filter=subscription_filter
            )

            if not exports:
                self.stdout.write(self.style.WARNING('No cost export files found.'))
                return

            self.stdout.write(self.style.SUCCESS(f'Found {len(exports)} cost export file(s)'))

            # Filter out already processed exports (unless force)
            if not force_reimport:
                processed_paths = set(
                    CostExport.objects.filter(
                        import_status='completed'
                    ).values_list('blob_path', flat=True)
                )
                exports = [e for e in exports if e['blob_path'] not in processed_paths]

                self.stdout.write(f'{len(exports)} unprocessed export(s) to import')

            # Apply limit if specified
            if limit:
                exports = exports[:limit]
                self.stdout.write(f'Processing first {limit} export(s)')

            # Process each export
            total_records_imported = 0
            total_duplicates_skipped = 0
            for idx, export_meta in enumerate(exports, 1):
                self.stdout.write(f'\n[{idx}/{len(exports)}] Processing: {export_meta["blob_path"]}')

                try:
                    result = self.process_export(blob_reader, export_meta, batch_size, force_reimport)
                    total_records_imported += result['records_imported']
                    total_duplicates_skipped += result['duplicates_skipped']
                    self.stdout.write(self.style.SUCCESS(
                        f'  ✓ Imported {result["records_imported"]} records '
                        f'({result["duplicates_skipped"]} duplicates skipped)'
                    ))
                except Exception as e:
                    self.stdout.write(self.style.ERROR(f'  ✗ Failed: {str(e)}'))
                    self.stderr.write(traceback.format_exc())
                    continue

            self.stdout.write(self.style.SUCCESS(
                f'\n✓ Import completed!'
            ))
            self.stdout.write(f'  Total records imported: {total_records_imported}')
            self.stdout.write(f'  Total duplicates skipped: {total_duplicates_skipped}')

            # Auto-refresh aggregations if records were imported
            if total_records_imported > 0 and not skip_aggregation:
                self.stdout.write('\n[Refreshing cost aggregations...]')
                try:
                    from finops_hub.utils.aggregation import CostAggregator
                    result = CostAggregator.refresh_all(days_back=60, currency='EUR')
                    self.stdout.write(self.style.SUCCESS(
                        f'  ✓ Daily aggregations: {result["daily_aggregations"]}'
                    ))
                    self.stdout.write(self.style.SUCCESS(
                        f'  ✓ Monthly aggregations: {result["monthly_aggregations"]}'
                    ))
                    self.stdout.write(self.style.SUCCESS(
                        f'  ✓ Period: {result["period"]}'
                    ))
                except Exception as e:
                    self.stdout.write(self.style.WARNING(
                        f'  ⚠ Aggregation refresh failed: {str(e)}'
                    ))

        except Exception as e:
            raise CommandError(f'Import failed: {str(e)}')

    def process_export(self, blob_reader, export_meta, batch_size, force_reimport=False):
        """
        Process a single cost export file with duplicate detection and update handling

        Args:
            blob_reader: AzureCostBlobReader instance
            export_meta: Export metadata dict from list_cost_exports()
            batch_size: Number of records to batch insert
            force_reimport: Whether to force re-import (deletes old data)

        Returns:
            dict: {
                'records_imported': int,
                'duplicates_skipped': int,
                'is_update': bool
            }
        """
        blob_path = export_meta['blob_path']
        subscription_name = export_meta['subscription_name']
        date_range = export_meta['date_range']

        # Parse date range
        start_date, end_date = blob_reader.parse_date_range(date_range)
        if not start_date or not end_date:
            raise ValueError(f'Invalid date range: {date_range}')

        # Check if we already have exports for this billing period (update detection)
        existing_exports = CostExport.objects.filter(
            subscription_name=subscription_name,
            billing_period_start=start_date,
            billing_period_end=end_date,
            import_status='completed'
        ).exclude(blob_path=blob_path)

        is_update = existing_exports.exists()

        if is_update or force_reimport:
            # This is an update or forced re-import - handle old data
            if existing_exports.exists():
                self.stdout.write(f'  → Detected update for existing period, handling old data...')
                for old_export in existing_exports:
                    old_record_count = old_export.records.count()
                    # Delete old cost records
                    old_export.records.all().delete()
                    # Mark old export as superseded
                    old_export.import_status = 'superseded'
                    old_export.error_message = f'Superseded by {blob_path}'
                    old_export.save()
                    self.stdout.write(f'  → Removed {old_record_count} old records from superseded export')

        # Create or get CostExport record
        cost_export, created = CostExport.objects.get_or_create(
            blob_path=blob_path,
            defaults={
                'subscription_name': subscription_name,
                'billing_period_start': start_date,
                'billing_period_end': end_date,
                'file_size_bytes': export_meta['size'],
                'import_status': 'pending',
            }
        )

        # Mark as processing
        cost_export.import_status = 'processing'
        cost_export.save()

        # Track subscription ID (will be extracted from first valid record)
        subscription_id_found = None

        try:
            # Stream and parse CSV records in batches
            records_imported = 0
            duplicates_skipped = 0
            parser = FOCUSParser()

            # Get existing hashes for this export to avoid re-checking database constantly
            # (only relevant if force_reimport, otherwise hashes should be unique across exports)
            existing_hashes = set()
            if not is_update and not force_reimport:
                # Pre-load existing hashes for duplicate detection (memory efficient for large imports)
                # We check in batches to avoid loading millions of hashes
                pass  # Will check per-batch below

            for batch in blob_reader.stream_csv_records(blob_path, batch_size=batch_size):
                # Parse batch
                parsed_records = []
                batch_hashes = []

                for row in batch:
                    try:
                        parsed = parser.parse_cost_record(row, calculate_hash=True)

                        # Extract subscription ID from first valid record
                        if not subscription_id_found and parsed.get('sub_account_id'):
                            subscription_id_found = parsed['sub_account_id']

                        # Validate
                        is_valid, error_msg = parser.validate_record(parsed)
                        if not is_valid:
                            self.stderr.write(f'  Warning: Skipping invalid record - {error_msg}')
                            continue

                        parsed_records.append(parsed)
                        batch_hashes.append(parsed['record_hash'])
                    except Exception as e:
                        self.stderr.write(f'  Warning: Failed to parse record - {str(e)}')
                        continue

                # Check for existing hashes in database (batch query)
                if batch_hashes:
                    existing_in_db = set(
                        CostRecord.objects.filter(
                            record_hash__in=batch_hashes
                        ).values_list('record_hash', flat=True)
                    )

                    # Filter out duplicates
                    unique_records = []
                    for record in parsed_records:
                        if record['record_hash'] in existing_in_db:
                            duplicates_skipped += 1
                        else:
                            unique_records.append(record)

                    # Batch insert unique records only
                    if unique_records:
                        cost_records = [
                            CostRecord(cost_export=cost_export, **record)
                            for record in unique_records
                        ]

                        with transaction.atomic():
                            try:
                                CostRecord.objects.bulk_create(
                                    cost_records,
                                    batch_size=batch_size,
                                    ignore_conflicts=True  # Skip duplicates at DB level
                                )
                                records_imported += len(cost_records)
                            except Exception as e:
                                # If bulk_create fails, try individual inserts (slower but more resilient)
                                self.stdout.write(f'  Warning: Bulk insert failed, trying individual inserts...')
                                for record_obj in cost_records:
                                    try:
                                        record_obj.save()
                                        records_imported += 1
                                    except Exception:
                                        duplicates_skipped += 1

                    # Progress indicator
                    if records_imported % (batch_size * 10) == 0:
                        self.stdout.write(
                            f'  ... {records_imported} records imported ({duplicates_skipped} duplicates)',
                            ending=''
                        )
                        self.stdout.flush()

            # Save subscription ID if found
            if subscription_id_found:
                cost_export.subscription_id = subscription_id_found
                cost_export.save()
                self.stdout.write(f'  → Extracted subscription ID: {subscription_id_found}')

            # Mark as completed
            cost_export.mark_completed(records_imported)

            # Flag if no records were imported (needs subscription ID)
            if records_imported == 0 and not cost_export.subscription_id:
                cost_export.needs_subscription_id = True
                cost_export.save()
                self.stdout.write(self.style.WARNING(
                    '  ⚠ No records imported. Please add subscription ID via the import dashboard.'
                ))

            return {
                'records_imported': records_imported,
                'duplicates_skipped': duplicates_skipped,
                'is_update': is_update
            }

        except Exception as e:
            # Mark as failed
            cost_export.mark_failed(e)
            raise
