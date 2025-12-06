"""
Management command to populate record_hash for existing CostRecord entries
Run this after adding the record_hash field to calculate hashes for all existing records

Usage:
    python manage.py populate_record_hashes
    python manage.py populate_record_hashes --batch-size 5000
"""
from django.core.management.base import BaseCommand
from django.db import transaction
from entreprinder.finops.models import CostRecord


class Command(BaseCommand):
    help = 'Populate record_hash field for existing CostRecord entries'

    def add_arguments(self, parser):
        parser.add_argument(
            '--batch-size',
            type=int,
            default=1000,
            help='Number of records to process per batch (default: 1000)',
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Recalculate hashes even if already present',
        )

    def handle(self, *args, **options):
        batch_size = options['batch_size']
        force = options['force']

        self.stdout.write(self.style.SUCCESS('Populating record hashes for existing records'))
        self.stdout.write(f'Batch size: {batch_size}')
        self.stdout.write(f'Force recalculation: {force}')

        # Query records that need hash calculation
        if force:
            queryset = CostRecord.objects.all()
        else:
            queryset = CostRecord.objects.filter(record_hash__isnull=True)

        total_count = queryset.count()

        if total_count == 0:
            self.stdout.write(self.style.SUCCESS('✓ All records already have hashes!'))
            return

        self.stdout.write(f'Found {total_count} records without hashes')

        processed = 0
        updated = 0
        duplicates = 0

        # Process in batches
        while processed < total_count:
            # Get next batch
            batch = list(queryset[processed:processed + batch_size])

            if not batch:
                break

            # Calculate hashes for batch
            records_to_update = []
            seen_hashes = set()

            for record in batch:
                try:
                    # Calculate hash
                    record_hash = record.calculate_hash()

                    # Check for duplicates within batch
                    if record_hash in seen_hashes:
                        duplicates += 1
                        self.stdout.write(
                            self.style.WARNING(
                                f'  Warning: Duplicate hash detected for record {record.id}'
                            )
                        )
                        # Skip this duplicate
                        continue

                    seen_hashes.add(record_hash)
                    record.record_hash = record_hash
                    records_to_update.append(record)
                except Exception as e:
                    self.stderr.write(f'  Error calculating hash for record {record.id}: {str(e)}')

            # Bulk update
            if records_to_update:
                try:
                    with transaction.atomic():
                        CostRecord.objects.bulk_update(
                            records_to_update,
                            ['record_hash'],
                            batch_size=batch_size
                        )
                        updated += len(records_to_update)
                except Exception as e:
                    # If bulk update fails (e.g., duplicate hash constraint), try individual updates
                    self.stdout.write(
                        self.style.WARNING(
                            f'  Bulk update failed, trying individual updates...'
                        )
                    )
                    for record in records_to_update:
                        try:
                            record.save(update_fields=['record_hash'])
                            updated += 1
                        except Exception as save_error:
                            duplicates += 1
                            self.stderr.write(
                                f'  Error saving record {record.id}: {str(save_error)}'
                            )

            processed += len(batch)

            # Progress indicator
            progress = (processed / total_count) * 100
            self.stdout.write(
                f'  Progress: {processed}/{total_count} ({progress:.1f}%) - '
                f'{updated} updated, {duplicates} duplicates'
            )

        # Summary
        self.stdout.write(self.style.SUCCESS('\n✓ Hash population completed!'))
        self.stdout.write(f'  Total processed: {processed}')
        self.stdout.write(f'  Successfully updated: {updated}')
        self.stdout.write(f'  Duplicates detected: {duplicates}')

        if duplicates > 0:
            self.stdout.write(
                self.style.WARNING(
                    f'\n⚠ Warning: {duplicates} duplicate records detected!'
                )
            )
            self.stdout.write(
                'These duplicates were NOT updated with hashes to avoid constraint violations.'
            )
            self.stdout.write(
                'You may want to review and delete duplicate records using:'
            )
            self.stdout.write('  python manage.py shell')
            self.stdout.write('  >>> from entreprinder.finops.models import CostRecord')
            self.stdout.write('  >>> CostRecord.objects.filter(record_hash__isnull=True).count()')
