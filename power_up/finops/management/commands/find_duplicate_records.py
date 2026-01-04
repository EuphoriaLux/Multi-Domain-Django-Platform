"""
Management command to find and analyze duplicate cost records
Helps identify duplicate records that don't have hashes due to uniqueness constraint

Usage:
    python manage.py find_duplicate_records
    python manage.py find_duplicate_records --delete-duplicates
"""
from django.core.management.base import BaseCommand
from django.db.models import Count
from power_up.finops.models import CostRecord


class Command(BaseCommand):
    help = 'Find and analyze duplicate cost records'

    def add_arguments(self, parser):
        parser.add_argument(
            '--delete-duplicates',
            action='store_true',
            help='Delete duplicate records (keeps the oldest one)',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be deleted without actually deleting',
        )

    def handle(self, *args, **options):
        delete_duplicates = options['delete_duplicates']
        dry_run = options['dry_run']

        self.stdout.write(self.style.SUCCESS('Analyzing duplicate cost records'))
        self.stdout.write('=' * 80)

        # Find records without hashes (these are duplicates)
        records_without_hash = CostRecord.objects.filter(record_hash__isnull=True)
        duplicate_count = records_without_hash.count()

        self.stdout.write(f'\nRecords without hash (duplicates): {duplicate_count}')

        if duplicate_count == 0:
            self.stdout.write(self.style.SUCCESS('\n✓ No duplicate records found!'))
            return

        # Analyze each duplicate
        self.stdout.write('\nAnalyzing duplicates:\n')

        for idx, duplicate in enumerate(records_without_hash, 1):
            self.stdout.write(f'\n--- Duplicate #{idx} (ID: {duplicate.id}) ---')
            self.stdout.write(f'  Subscription: {duplicate.sub_account_name}')
            self.stdout.write(f'  Resource: {duplicate.resource_name or "N/A"}')
            self.stdout.write(f'  Service: {duplicate.service_name}')
            self.stdout.write(f'  Charge Period: {duplicate.charge_period_start} to {duplicate.charge_period_end}')
            self.stdout.write(f'  Cost: {duplicate.billed_cost} {duplicate.billing_currency}')
            self.stdout.write(f'  Imported: {duplicate.created_at}')

            # Try to calculate what its hash would be
            try:
                would_be_hash = duplicate.calculate_hash()
                self.stdout.write(f'  Would-be Hash: {would_be_hash[:16]}...')

                # Find the original record with this hash
                original = CostRecord.objects.filter(record_hash=would_be_hash).first()
                if original:
                    self.stdout.write(self.style.WARNING('  → DUPLICATE OF:'))
                    self.stdout.write(f'     Original ID: {original.id}')
                    self.stdout.write(f'     Original Imported: {original.created_at}')
                    self.stdout.write(f'     Original Export: {original.cost_export.blob_path}')

                    # Compare key fields
                    same_fields = []
                    diff_fields = []

                    field_checks = [
                        ('sub_account_id', 'Subscription ID'),
                        ('resource_id', 'Resource ID'),
                        ('billed_cost', 'Cost'),
                        ('charge_period_start', 'Period Start'),
                        ('service_name', 'Service'),
                    ]

                    for field, label in field_checks:
                        dup_val = getattr(duplicate, field)
                        orig_val = getattr(original, field)
                        if dup_val == orig_val:
                            same_fields.append(label)
                        else:
                            diff_fields.append(f"{label}: {dup_val} vs {orig_val}")

                    if same_fields:
                        self.stdout.write(f'     Same: {", ".join(same_fields)}')
                    if diff_fields:
                        self.stdout.write(self.style.WARNING(f'     Different: {", ".join(diff_fields)}'))

            except Exception as e:
                self.stdout.write(self.style.ERROR(f'  Error calculating hash: {str(e)}'))

        # Deletion logic
        if delete_duplicates:
            self.stdout.write('\n' + '=' * 80)
            if dry_run:
                self.stdout.write(self.style.WARNING('\n[DRY RUN] Would delete the following records:'))
            else:
                self.stdout.write(self.style.WARNING('\n[DELETING] Removing duplicate records...'))

            deleted_count = 0
            for duplicate in records_without_hash:
                try:
                    dup_id = duplicate.id
                    dup_cost = duplicate.billed_cost
                    dup_service = duplicate.service_name

                    if not dry_run:
                        duplicate.delete()
                        deleted_count += 1

                    self.stdout.write(
                        f'  {"[Would delete]" if dry_run else "[Deleted]"} ID {dup_id}: '
                        f'{dup_service} - {dup_cost}'
                    )

                except Exception as e:
                    self.stdout.write(self.style.ERROR(f'  Error deleting record {duplicate.id}: {str(e)}'))

            if dry_run:
                self.stdout.write(f'\n[DRY RUN] Would have deleted {duplicate_count} duplicate records')
            else:
                self.stdout.write(self.style.SUCCESS(f'\n✓ Deleted {deleted_count} duplicate records'))

        else:
            # Show instructions
            self.stdout.write('\n' + '=' * 80)
            self.stdout.write('\nTo delete these duplicates, run:')
            self.stdout.write(self.style.WARNING('  python manage.py find_duplicate_records --delete-duplicates'))
            self.stdout.write('\nOr to preview what would be deleted:')
            self.stdout.write(self.style.WARNING('  python manage.py find_duplicate_records --delete-duplicates --dry-run'))

        self.stdout.write('\n' + '=' * 80)
