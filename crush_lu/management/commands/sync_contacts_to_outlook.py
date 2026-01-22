# crush_lu/management/commands/sync_contacts_to_outlook.py
"""
Management command to sync Crush.lu user contacts to Outlook via Microsoft Graph API.

Syncs CrushProfile contacts to the noreply@crush.lu shared mailbox,
enabling caller ID recognition when Crush.lu users call.

Usage:
    # Full sync (all profiles)
    python manage.py sync_contacts_to_outlook

    # Dry run (preview only)
    python manage.py sync_contacts_to_outlook --dry-run

    # Single user by profile ID
    python manage.py sync_contacts_to_outlook --profile-id 123

    # Delete all synced contacts (cleanup)
    python manage.py sync_contacts_to_outlook --delete-all

Environment:
    In production, set OUTLOOK_CONTACT_SYNC_ENABLED=true to enable sync.
    Local development (DEBUG=True) will show disabled message unless --dry-run.
"""

from django.core.management.base import BaseCommand, CommandError
from django.conf import settings

from crush_lu.models import CrushProfile
from crush_lu.services.graph_contacts import GraphContactsService, is_sync_enabled


class Command(BaseCommand):
    help = 'Sync Crush.lu user contacts to Outlook via Microsoft Graph API'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Preview what would be synced without making changes'
        )
        parser.add_argument(
            '--profile-id',
            type=int,
            help='Sync only a specific profile by ID'
        )
        parser.add_argument(
            '--delete-all',
            action='store_true',
            help='Delete all synced contacts from Outlook (use with caution)'
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Force sync even if OUTLOOK_CONTACT_SYNC_ENABLED is not set (use with caution)'
        )
        parser.add_argument(
            '--local-test',
            action='store_true',
            help='Enable local testing mode - bypasses environment checks and syncs directly'
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        profile_id = options['profile_id']
        delete_all = options['delete_all']
        force = options['force']
        local_test = options['local_test']

        # Check if sync is enabled
        sync_enabled = is_sync_enabled()

        if not sync_enabled and not dry_run and not force and not local_test:
            self.stdout.write(
                self.style.WARNING(
                    '\nOutlook contact sync is disabled for this environment.\n'
                    '\nTo enable sync in production:\n'
                    '  1. Set OUTLOOK_CONTACT_SYNC_ENABLED=true in Azure App Service\n'
                    '  2. Ensure Contacts.ReadWrite permission is granted in Azure AD\n'
                    '\nTo preview what would be synced:\n'
                    '  python manage.py sync_contacts_to_outlook --dry-run\n'
                    '\nTo force sync anyway (use with caution):\n'
                    '  python manage.py sync_contacts_to_outlook --force\n'
                )
            )
            return

        # Check Graph API credentials (check both env vars and settings)
        import os
        has_tenant = os.getenv('GRAPH_TENANT_ID') or getattr(settings, 'GRAPH_TENANT_ID', None)
        has_client = os.getenv('GRAPH_CLIENT_ID') or getattr(settings, 'GRAPH_CLIENT_ID', None)
        has_secret = os.getenv('GRAPH_CLIENT_SECRET') or getattr(settings, 'GRAPH_CLIENT_SECRET', None)

        if not all([has_tenant, has_client, has_secret]):
            raise CommandError(
                'Microsoft Graph credentials not configured. '
                'Set GRAPH_TENANT_ID, GRAPH_CLIENT_ID, and GRAPH_CLIENT_SECRET.'
            )

        try:
            service = GraphContactsService()
        except Exception as e:
            raise CommandError(f'Failed to initialize GraphContactsService: {e}')

        # Handle delete-all mode
        if delete_all:
            self._handle_delete_all(service)
            return

        # Handle single profile sync
        if profile_id:
            self._handle_single_profile(service, profile_id, dry_run, local_test)
            return

        # Full sync
        self._handle_full_sync(service, dry_run)

    def _handle_delete_all(self, service: GraphContactsService):
        """Delete all synced contacts from Outlook."""
        self.stdout.write(
            self.style.WARNING(
                '\nThis will delete ALL Crush.lu contacts from Outlook.\n'
                'Are you sure? (type "yes" to confirm): '
            ),
            ending=''
        )

        confirmation = input()
        if confirmation.lower() != 'yes':
            self.stdout.write(self.style.NOTICE('Aborted.'))
            return

        self.stdout.write('\nDeleting all synced contacts...\n')

        stats = service.delete_all_contacts()

        self.stdout.write(
            self.style.SUCCESS(
                f'\nDeletion complete!\n'
                f'  Total with contact IDs: {stats["total"]}\n'
                f'  Deleted: {stats["deleted"]}\n'
                f'  Errors: {stats["errors"]}\n'
            )
        )

    def _handle_single_profile(
        self,
        service: GraphContactsService,
        profile_id: int,
        dry_run: bool,
        local_test: bool = False
    ):
        """Sync a single profile to Outlook."""
        try:
            profile = CrushProfile.objects.select_related('user').get(pk=profile_id)
        except CrushProfile.DoesNotExist:
            raise CommandError(f'Profile with ID {profile_id} not found.')

        self.stdout.write(
            f'\n{"[DRY RUN] " if dry_run else ""}{"[LOCAL TEST] " if local_test else ""}'
            f'Syncing profile {profile.pk} ({profile.user.email})...\n'
        )

        if not profile.phone_number:
            self.stdout.write(
                self.style.WARNING(
                    f'  Skipped - no phone number\n'
                )
            )
            return

        if dry_run:
            action = "update" if profile.outlook_contact_id else "create"
            self.stdout.write(
                self.style.SUCCESS(
                    f'  Would {action} contact\n'
                    f'  Name: {profile.user.first_name} {profile.user.last_name}\n'
                    f'  Phone: {profile.phone_number}\n'
                    f'  Email: {profile.user.email}\n'
                )
            )
        else:
            result = service.sync_profile(profile, force=local_test)
            if result:
                self.stdout.write(
                    self.style.SUCCESS(
                        f'  Synced successfully!\n'
                        f'  Contact ID: {result}\n'
                    )
                )
            else:
                self.stdout.write(
                    self.style.ERROR('  Failed to sync.\n')
                )

    def _handle_full_sync(self, service: GraphContactsService, dry_run: bool):
        """Sync all profiles to Outlook."""
        total_profiles = CrushProfile.objects.count()
        profiles_with_phone = CrushProfile.objects.exclude(
            phone_number__isnull=True
        ).exclude(phone_number='').count()

        self.stdout.write(
            f'\n{"[DRY RUN] " if dry_run else ""}'
            f'Syncing Crush.lu profiles to Outlook contacts...\n'
            f'\n'
            f'  Total profiles: {total_profiles}\n'
            f'  With phone numbers: {profiles_with_phone}\n'
            f'  Mailbox: {service.mailbox}\n'
            f'\n'
        )

        if not dry_run:
            self.stdout.write('Syncing...\n')

        stats = service.sync_all_profiles(dry_run=dry_run)

        if dry_run:
            self.stdout.write(
                self.style.SUCCESS(
                    f'\nDry run complete!\n'
                    f'  Total profiles: {stats["total"]}\n'
                    f'  Would sync: {stats["synced"]}\n'
                    f'  Would skip (no phone): {stats["skipped"]}\n'
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f'\nSync complete!\n'
                    f'  Total profiles: {stats["total"]}\n'
                    f'  Synced: {stats["synced"]}\n'
                    f'  Skipped (no phone): {stats["skipped"]}\n'
                    f'  Errors: {stats["errors"]}\n'
                )
            )
