"""
Management command to migrate existing profile photos to user-organized folder structure.

Old structure: crush_profiles/{uuid}_{filename}.jpg
New structure: users/{user_id}/photos/{uuid}.{ext}

This command:
1. Finds all CrushProfile records with photos
2. For each photo, copies it to the new location in Azure Blob Storage
3. Updates the database record with the new path
4. Optionally deletes the old file after successful migration

Usage:
    python manage.py migrate_profile_photos --dry-run     # Preview changes
    python manage.py migrate_profile_photos               # Migrate photos
    python manage.py migrate_profile_photos --delete-old  # Migrate and delete old files
"""

import os
import uuid
from django.core.management.base import BaseCommand
from django.conf import settings
from crush_lu.models import CrushProfile


class Command(BaseCommand):
    help = 'Migrate profile photos from flat structure to user-organized folders'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Preview changes without actually migrating files',
        )
        parser.add_argument(
            '--delete-old',
            action='store_true',
            help='Delete old files after successful migration',
        )
        parser.add_argument(
            '--user-id',
            type=int,
            help='Migrate only a specific user ID (for testing)',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        delete_old = options['delete_old']
        user_id = options.get('user_id')

        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN MODE - No changes will be made'))

        # Check if we're in production (Azure storage available)
        if not os.getenv('AZURE_ACCOUNT_NAME'):
            self.stdout.write(self.style.ERROR(
                'This command requires Azure storage configuration.\n'
                'Set AZURE_ACCOUNT_NAME, AZURE_ACCOUNT_KEY environment variables.'
            ))
            return

        # Import Azure storage
        try:
            from azure.storage.blob import BlobServiceClient
            from crush_lu.storage import CrushProfilePhotoStorage
        except ImportError as e:
            self.stdout.write(self.style.ERROR(f'Failed to import Azure storage: {e}'))
            return

        # Initialize storage
        storage = CrushProfilePhotoStorage()
        account_name = os.getenv('AZURE_ACCOUNT_NAME')
        account_key = os.getenv('AZURE_ACCOUNT_KEY')
        container_name = storage.azure_container

        # Create blob service client
        blob_service_client = BlobServiceClient(
            account_url=f"https://{account_name}.blob.core.windows.net",
            credential=account_key
        )
        container_client = blob_service_client.get_container_client(container_name)

        # Get profiles to migrate
        profiles = CrushProfile.objects.all()
        if user_id:
            profiles = profiles.filter(user_id=user_id)

        total_profiles = profiles.count()
        migrated_count = 0
        skipped_count = 0
        error_count = 0

        self.stdout.write(f'\nFound {total_profiles} profiles to check\n')

        for profile in profiles:
            user_id = profile.user.id
            self.stdout.write(f'\nProcessing User {user_id} ({profile.user.get_full_name() or profile.user.username})')

            for field_name in ['photo_1', 'photo_2', 'photo_3']:
                photo_field = getattr(profile, field_name)

                if not photo_field:
                    continue

                old_path = photo_field.name

                # Skip if already in new structure
                if old_path.startswith(f'users/{user_id}/'):
                    self.stdout.write(f'  {field_name}: Already migrated, skipping')
                    skipped_count += 1
                    continue

                # Generate new path
                ext = os.path.splitext(old_path)[1].lower() or '.jpg'
                new_filename = f"{uuid.uuid4().hex}{ext}"
                new_path = f"users/{user_id}/photos/{new_filename}"

                self.stdout.write(f'  {field_name}:')
                self.stdout.write(f'    Old: {old_path}')
                self.stdout.write(f'    New: {new_path}')

                if dry_run:
                    migrated_count += 1
                    continue

                try:
                    # Copy blob to new location
                    source_blob = container_client.get_blob_client(old_path)
                    dest_blob = container_client.get_blob_client(new_path)

                    # Check if source exists
                    if not source_blob.exists():
                        self.stdout.write(self.style.WARNING(f'    Source file not found, skipping'))
                        error_count += 1
                        continue

                    # Copy the blob
                    dest_blob.start_copy_from_url(source_blob.url)

                    # Wait for copy to complete
                    copy_props = dest_blob.get_blob_properties()
                    while copy_props.copy.status == 'pending':
                        import time
                        time.sleep(0.5)
                        copy_props = dest_blob.get_blob_properties()

                    if copy_props.copy.status != 'success':
                        self.stdout.write(self.style.ERROR(f'    Copy failed: {copy_props.copy.status}'))
                        error_count += 1
                        continue

                    # Update database record
                    setattr(profile, field_name, new_path)
                    profile.save(update_fields=[field_name])

                    self.stdout.write(self.style.SUCCESS(f'    Migrated successfully'))
                    migrated_count += 1

                    # Delete old file if requested
                    if delete_old:
                        try:
                            source_blob.delete_blob()
                            self.stdout.write(f'    Deleted old file')
                        except Exception as e:
                            self.stdout.write(self.style.WARNING(f'    Failed to delete old file: {e}'))

                except Exception as e:
                    self.stdout.write(self.style.ERROR(f'    Error: {e}'))
                    error_count += 1

        # Summary
        self.stdout.write('\n' + '=' * 50)
        self.stdout.write(self.style.SUCCESS(f'Migration complete!'))
        self.stdout.write(f'  Profiles checked: {total_profiles}')
        self.stdout.write(f'  Photos migrated: {migrated_count}')
        self.stdout.write(f'  Photos skipped (already migrated): {skipped_count}')
        self.stdout.write(f'  Errors: {error_count}')

        if dry_run:
            self.stdout.write(self.style.WARNING('\nThis was a dry run. Run without --dry-run to actually migrate.'))
