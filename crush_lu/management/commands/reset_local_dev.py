"""
Reset local development environment completely.

WARNING: This command ONLY works in development (Azurite mode).
It will NOT run if connected to production Azure or PostgreSQL.

This command:
1. Deletes ALL data from the PostgreSQL database (keeps schema)
2. Deletes ALL blobs from Azurite containers (media + crush-profiles-private)
3. Re-runs setup_local_dev to create fresh sample data

Usage:
    python manage.py reset_local_dev
    python manage.py reset_local_dev --skip-sample-data
    python manage.py reset_local_dev --yes  # Skip confirmation prompt
"""

from django.core.management.base import BaseCommand, CommandError
from django.core.management import call_command
from django.conf import settings
from django.contrib.auth.models import User
from django.db import connection


class Command(BaseCommand):
    help = 'Reset local development environment (database + blob storage)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--yes', '-y',
            action='store_true',
            help='Skip confirmation prompt'
        )
        parser.add_argument(
            '--skip-sample-data',
            action='store_true',
            help='Only reset, do not create sample data'
        )
        parser.add_argument(
            '--profile-count',
            type=int,
            default=30,
            help='Number of Crush.lu profiles to create (default: 30)'
        )

    def handle(self, *args, **options):
        # Safety check: Only allow in Azurite mode
        if not getattr(settings, 'AZURITE_MODE', False):
            raise CommandError(
                'This command only works in Azurite mode (USE_AZURITE=true).\n'
                'It is designed for local development only to prevent accidental '
                'deletion of production data.'
            )

        # Safety check: Must be using local PostgreSQL
        db_host = settings.DATABASES['default'].get('HOST', '')
        if db_host and 'azure' in db_host.lower():
            raise CommandError(
                'This command cannot run against Azure PostgreSQL.\n'
                'It is designed for local development only.'
            )

        self.stdout.write('\n' + '=' * 60)
        self.stdout.write(self.style.WARNING('  LOCAL DEVELOPMENT RESET'))
        self.stdout.write('=' * 60)
        self.stdout.write('\nThis will DELETE:')
        self.stdout.write('  - All data in PostgreSQL database')
        self.stdout.write('  - All blobs in Azurite containers')
        self.stdout.write('\nDatabase: ' + settings.DATABASES['default'].get('NAME', 'unknown'))
        self.stdout.write('Host: ' + (db_host or 'localhost'))
        self.stdout.write('')

        # Confirmation
        if not options['yes']:
            confirm = input('\nType "RESET" to confirm: ')
            if confirm != 'RESET':
                self.stdout.write(self.style.WARNING('Aborted.'))
                return

        self.stdout.write('')

        # Step 1: Clear Azurite blob storage
        self.stdout.write('[1/3] Clearing Azurite blob storage...')
        self.clear_azurite_storage()

        # Step 2: Clear database
        self.stdout.write('\n[2/3] Clearing database...')
        self.clear_database()

        # Step 3: Re-create sample data
        if not options['skip_sample_data']:
            self.stdout.write('\n[3/3] Creating fresh sample data...')
            self.create_sample_data(options['profile_count'])
        else:
            self.stdout.write('\n[3/3] Skipping sample data (--skip-sample-data)')

        self.stdout.write('\n' + '=' * 60)
        self.stdout.write(self.style.SUCCESS('  Reset Complete!'))
        self.stdout.write('=' * 60)

        if not options['skip_sample_data']:
            self.stdout.write('\nTest user credentials:')
            self.stdout.write('  Email: testuser1@crush.lu (through testuser30@crush.lu)')
            self.stdout.write('  Password: testuser2025')
            self.stdout.write('\nCoach credentials:')
            self.stdout.write('  Email: marie@crush.lu, thomas@crush.lu, sophie@crush.lu')
            self.stdout.write('  Password: crushcoach2025')

        self.stdout.write('\nNext steps:')
        self.stdout.write('  1. Create a superuser: python manage.py createsuperuser')
        self.stdout.write('  2. Run the server: python manage.py runserver')
        self.stdout.write('')

    def clear_azurite_storage(self):
        """Delete all blobs from Azurite containers."""
        from azure.storage.blob import BlobServiceClient

        connection_string = getattr(settings, 'AZURE_CONNECTION_STRING', None)
        if not connection_string:
            self.stdout.write(self.style.WARNING('  No Azurite connection string found'))
            return

        try:
            blob_service = BlobServiceClient.from_connection_string(connection_string)

            containers = ['media', 'crush-profiles-private']
            total_deleted = 0

            for container_name in containers:
                try:
                    container_client = blob_service.get_container_client(container_name)

                    if not container_client.exists():
                        self.stdout.write(f'  Container "{container_name}" does not exist, creating...')
                        container_client.create_container()
                        continue

                    # Delete all blobs
                    blobs = list(container_client.list_blobs())
                    for blob in blobs:
                        container_client.delete_blob(blob.name)
                        total_deleted += 1

                    self.stdout.write(
                        self.style.SUCCESS(f'  Cleared {len(blobs)} blob(s) from "{container_name}"')
                    )

                except Exception as e:
                    self.stdout.write(self.style.WARNING(f'  Error with container "{container_name}": {e}'))

            self.stdout.write(self.style.SUCCESS(f'  Total: {total_deleted} blob(s) deleted'))

        except Exception as e:
            self.stdout.write(self.style.ERROR(f'  Failed to connect to Azurite: {e}'))

    def clear_database(self):
        """Clear all data from the database while keeping the schema."""
        from django.apps import apps

        # Get all models in dependency order (reverse for deletion)
        all_models = apps.get_models()

        # Models to skip (Django internals that should be preserved or handled specially)
        skip_models = {
            'contenttypes.ContentType',
            'auth.Permission',
            'sessions.Session',
            'admin.LogEntry',
            'sites.Site',
        }

        deleted_counts = {}
        tables_cleared = []

        # Delete in reverse order to handle FK dependencies
        for model in reversed(all_models):
            model_name = f'{model._meta.app_label}.{model._meta.model_name}'
            model_label = f'{model._meta.app_label}.{model.__name__}'

            if model_label in skip_models or model_name in skip_models:
                continue

            try:
                count = model.objects.count()
                table_name = model._meta.db_table
                if count > 0:
                    # Use raw SQL for faster deletion and to bypass signals
                    with connection.cursor() as cursor:
                        cursor.execute(f'DELETE FROM "{table_name}"')
                    deleted_counts[model_label] = count
                tables_cleared.append(table_name)
            except Exception as e:
                # Some models may fail due to FK constraints, that's ok
                pass

        # Reset sequences for all cleared tables so IDs start from 1
        self.stdout.write('  Resetting ID sequences...')
        sequences_reset = 0
        with connection.cursor() as cursor:
            for table_name in tables_cleared:
                try:
                    # PostgreSQL sequence naming convention: {table}_id_seq
                    sequence_name = f'{table_name}_id_seq'
                    cursor.execute(f'ALTER SEQUENCE "{sequence_name}" RESTART WITH 1')
                    sequences_reset += 1
                except Exception:
                    # Not all tables have sequences (e.g., many-to-many tables)
                    pass

        # Print summary
        if deleted_counts:
            self.stdout.write('  Deleted:')
            for model, count in deleted_counts.items():
                self.stdout.write(f'    - {model}: {count} records')
        else:
            self.stdout.write('  Database was already empty')

        self.stdout.write(self.style.SUCCESS(f'  Database cleared ({sequences_reset} sequences reset)'))

    def create_sample_data(self, profile_count):
        """Run setup_local_dev to create sample data."""
        try:
            call_command(
                'setup_local_dev',
                profile_count=profile_count,
                verbosity=1
            )
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'  Failed to create sample data: {e}'))
