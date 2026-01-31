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
from power_up.finops.models import CostExport, CostRecord
from power_up.finops.utils.blob_reader import AzureCostBlobReader
from power_up.finops.utils.focus_parser import FOCUSParser
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

            # Filter exports intelligently (unless force)
            if not force_reimport:
                exports_to_import = []
                updated_exports = []

                for export in exports:
                    blob_path = export['blob_path']

                    # Check if we've processed this export before
                    try:
                        existing_export = CostExport.objects.get(
                            blob_path=blob_path,
                            import_status='completed'
                        )

                        # Check if blob has been updated since last import
                        if existing_export.has_been_updated(
                            blob_last_modified=export['last_modified'],
                            blob_etag=export.get('etag')
                        ):
                            self.stdout.write(
                                f'  🔄 Detected update: {blob_path}'
                            )
                            self.stdout.write(
                                f'     Last imported: {existing_export.blob_last_modified}, '
                                f'Now: {export["last_modified"]}'
                            )
                            exports_to_import.append(export)
                            updated_exports.append(existing_export)
                        # else: Skip, already up-to-date

                    except CostExport.DoesNotExist:
                        # New export, add it
                        exports_to_import.append(export)

                exports = exports_to_import
                self.stdout.write(
                    f'{len(exports)} export(s) to import '
                    f'({len(updated_exports)} updated, {len(exports) - len(updated_exports)} new)'
                )

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
                        f'  [OK] Imported {result["records_imported"]} records '
                        f'({result["duplicates_skipped"]} duplicates skipped)'
                    ))
                except Exception as e:
                    self.stdout.write(self.style.ERROR(f'  [ERROR] Failed: {str(e)}'))
                    self.stderr.write(traceback.format_exc())
                    continue

            self.stdout.write(self.style.SUCCESS(
                f'\n[OK] Import completed!'
            ))
            self.stdout.write(f'  Total records imported: {total_records_imported}')
            self.stdout.write(f'  Total duplicates skipped: {total_duplicates_skipped}')

            # Auto-refresh aggregations if records were imported
            if total_records_imported > 0 and not skip_aggregation:
                self.stdout.write('\n[Refreshing cost aggregations...]')
                try:
                    from power_up.finops.utils.aggregation import CostAggregator
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
        # IMPORTANT: Multi-part exports (part_0, part_1, etc.) are ADDITIVE, not replacements
        # Only consider it an update if the export GUID changed (not just part number)
        import re
        current_export_guid = self._extract_export_guid(blob_path)

        existing_exports = CostExport.objects.filter(
            subscription_name=subscription_name,
            billing_period_start=start_date,
            billing_period_end=end_date,
            import_status='completed'
        ).exclude(blob_path=blob_path)

        # Filter to only exports with DIFFERENT export GUIDs (true updates, not multi-part)
        exports_to_supersede = []
        for exp in existing_exports:
            exp_guid = self._extract_export_guid(exp.blob_path)
            if exp_guid != current_export_guid:
                # Different GUID = true replacement (Azure regenerated the export)
                exports_to_supersede.append(exp)
            # else: Same GUID, different part number = complementary file, keep both

        is_update = len(exports_to_supersede) > 0

        if is_update or force_reimport:
            # This is an update or forced re-import - handle old data
            if exports_to_supersede:
                self.stdout.write(f'  -> Detected update for existing period (different export GUID), handling old data...')
                for old_export in exports_to_supersede:
                    old_record_count = old_export.records.count()
                    # Delete old cost records
                    old_export.records.all().delete()
                    # Mark old export as superseded
                    old_export.import_status = 'superseded'
                    old_export.error_message = f'Superseded by {blob_path}'
                    old_export.save()
                    self.stdout.write(f'  -> Removed {old_record_count} old records from superseded export')

        # Create or get CostExport record
        cost_export, created = CostExport.objects.get_or_create(
            blob_path=blob_path,
            defaults={
                'subscription_name': subscription_name,
                'billing_period_start': start_date,
                'billing_period_end': end_date,
                'file_size_bytes': export_meta['size'],
                'import_status': 'pending',
                'blob_last_modified': export_meta.get('last_modified'),
                'blob_etag': export_meta.get('etag'),
            }
        )

        # Mark as processing and update blob metadata
        cost_export.import_status = 'processing'
        cost_export.update_blob_metadata(
            blob_last_modified=export_meta.get('last_modified'),
            blob_etag=export_meta.get('etag'),
            file_size=export_meta['size']
        )

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
                self.stdout.write(f'  -> Extracted subscription ID: {subscription_id_found}')

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

    def _extract_export_guid(self, blob_path):
        """
        Extract the export GUID from blob path to identify multi-part exports.

        Azure Cost Export path format:
        subscriptions/{sub-id}/{export-name}/{date-range}/{GUID}/part_{N}_0001.csv.gz

        The GUID identifies the export run. Different part numbers (part_0, part_1, etc.)
        with the SAME GUID are complementary files from the same export.
        Different GUIDs mean Azure regenerated the export (true update).

        Args:
            blob_path: Full blob path

        Returns:
            str: Export GUID or None if not found

        Example:
            Input: "subscriptions/.../20260101-20260131/7946d592-03d8-4ce0-bfca-af3abfa49d71/part_0_0001.csv.gz"
            Output: "7946d592-03d8-4ce0-bfca-af3abfa49d71"
        """
        import re
        # Match GUID pattern (8-4-4-4-12 hex characters)
        match = re.search(r'([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})', blob_path)
        return match.group(1) if match else None
