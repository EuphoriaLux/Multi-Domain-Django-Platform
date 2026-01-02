"""
Management command to clean up orphaned blob storage folders.

When users are deleted, their storage folders (users/{user_id}/) may remain
in Azure Blob Storage. This command identifies and removes those orphaned folders.

Usage:
    # Dry run - see what would be deleted
    python manage.py cleanup_orphan_storage --dry-run

    # Actually delete orphaned folders
    python manage.py cleanup_orphan_storage

    # Verbose output
    python manage.py cleanup_orphan_storage --dry-run -v 2
"""

from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from crush_lu.storage import list_user_storage_folders, delete_user_storage


class Command(BaseCommand):
    help = 'Clean up orphaned blob storage folders for deleted users'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be deleted without making changes',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        verbosity = options['verbosity']

        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN - No changes will be made\n'))

        # Get all user IDs from storage
        self.stdout.write('Scanning blob storage for user folders...')
        storage_user_ids = list_user_storage_folders()

        if not storage_user_ids:
            self.stdout.write(self.style.SUCCESS('No user storage folders found.'))
            return

        self.stdout.write(f'Found {len(storage_user_ids)} user folder(s) in storage.\n')

        # Get all existing user IDs from database
        self.stdout.write('Checking database for existing users...')
        db_user_ids = set(User.objects.values_list('id', flat=True))
        self.stdout.write(f'Found {len(db_user_ids)} user(s) in database.\n')

        # Find orphaned folders (in storage but not in DB)
        orphan_user_ids = storage_user_ids - db_user_ids

        if not orphan_user_ids:
            self.stdout.write(self.style.SUCCESS(
                'No orphaned storage folders found. All folders belong to existing users.'
            ))
            return

        self.stdout.write(self.style.WARNING(
            f'\nFound {len(orphan_user_ids)} orphaned folder(s):\n'
        ))

        deleted_count = 0
        error_count = 0
        total_blobs_deleted = 0

        for user_id in sorted(orphan_user_ids):
            if dry_run:
                self.stdout.write(f'  Would delete: users/{user_id}/')
                deleted_count += 1
            else:
                try:
                    success, blobs_deleted = delete_user_storage(user_id)
                    if success:
                        deleted_count += 1
                        total_blobs_deleted += blobs_deleted
                        self.stdout.write(self.style.SUCCESS(
                            f'  Deleted: users/{user_id}/ ({blobs_deleted} blob(s))'
                        ))
                    else:
                        error_count += 1
                        self.stdout.write(self.style.ERROR(
                            f'  Failed: users/{user_id}/'
                        ))
                except Exception as e:
                    error_count += 1
                    self.stdout.write(self.style.ERROR(
                        f'  Error deleting users/{user_id}/: {str(e)}'
                    ))

        # Summary
        self.stdout.write('\n' + '=' * 50)
        if dry_run:
            self.stdout.write(self.style.WARNING(
                f'Would delete {deleted_count} orphaned folder(s)'
            ))
            self.stdout.write(self.style.WARNING(
                '\nDRY RUN - No changes were made'
            ))
            self.stdout.write('Run without --dry-run to delete the folders')
        else:
            self.stdout.write(self.style.SUCCESS(
                f'Deleted {deleted_count} orphaned folder(s) ({total_blobs_deleted} total blob(s))'
            ))
            if error_count:
                self.stdout.write(self.style.ERROR(f'Errors: {error_count}'))
