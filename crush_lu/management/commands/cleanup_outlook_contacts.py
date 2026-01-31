"""
Management command to clean up Outlook contacts for test/unapproved/orphaned profiles.

This command removes invalid contacts from the Outlook shared mailbox:
- Test users (example.com, test.com, localhost domains)
- Orphaned contacts (profile ID doesn't exist in database)
- Unapproved profiles (is_approved=False)
- Profiles without phone numbers

Usage:
    # Dry run (preview only - ALWAYS START HERE)
    python manage.py cleanup_outlook_contacts --dry-run

    # Delete all invalid contacts
    python manage.py cleanup_outlook_contacts

    # Delete only test user contacts
    python manage.py cleanup_outlook_contacts --test-only

    # Delete only orphaned contacts (profile doesn't exist)
    python manage.py cleanup_outlook_contacts --orphaned-only
"""

import re
import logging
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from crush_lu.services.graph_contacts import GraphContactsService, is_sync_enabled
from crush_lu.models import CrushProfile
from crush_lu.signals import is_test_user

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Clean up Outlook contacts for test/unapproved/orphaned profiles'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Preview what would be deleted without actually deleting'
        )
        parser.add_argument(
            '--test-only',
            action='store_true',
            help='Only delete test user contacts (example.com, test.com, etc.)'
        )
        parser.add_argument(
            '--orphaned-only',
            action='store_true',
            help='Only delete orphaned contacts (profile not in database)'
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        test_only = options['test_only']
        orphaned_only = options['orphaned_only']

        if not is_sync_enabled():
            raise CommandError(
                "Outlook contact sync is disabled. "
                "Set OUTLOOK_CONTACT_SYNC_ENABLED=true to run this command."
            )

        self.stdout.write(self.style.WARNING(
            "\n" + "=" * 80 + "\n"
            "Outlook Contact Cleanup Tool\n"
            + "=" * 80 + "\n"
        ))

        if dry_run:
            self.stdout.write(self.style.NOTICE(
                "DRY RUN MODE - No contacts will be deleted\n"
            ))

        # Initialize service
        try:
            service = GraphContactsService()
        except Exception as e:
            raise CommandError(f"Failed to initialize Graph service: {e}")

        # Fetch all contacts from Outlook
        self.stdout.write("Fetching contacts from Outlook...")
        try:
            contacts = service.list_all_contacts_from_outlook()
        except Exception as e:
            raise CommandError(f"Failed to list Outlook contacts: {e}")

        self.stdout.write(self.style.SUCCESS(f"Found {len(contacts)} contacts in Outlook\n"))

        if len(contacts) == 0:
            self.stdout.write(self.style.SUCCESS("No contacts to clean up!"))
            return

        # Analyze contacts
        self.stdout.write("Analyzing contacts...\n")

        stats = {
            'total': len(contacts),
            'test_users': 0,
            'orphaned': 0,
            'unapproved': 0,
            'missing_phone': 0,
            'valid': 0,
            'to_delete': [],
        }

        for contact in contacts:
            contact_id = contact.get('id')
            display_name = contact.get('displayName', 'Unknown')
            notes = contact.get('personalNotes', '')
            email_addresses = contact.get('emailAddresses', [])
            email = email_addresses[0].get('address') if email_addresses else None

            # Extract profile ID from notes
            profile_id = self._extract_profile_id(notes)

            # Determine contact status
            reason = None

            if not profile_id:
                # No profile ID - can't verify
                reason = "no_profile_id"
                stats['orphaned'] += 1
            else:
                # Check if profile exists in database
                try:
                    profile = CrushProfile.objects.select_related('user').get(pk=profile_id)

                    # Check if test user
                    if is_test_user(profile.user):
                        reason = "test_user"
                        stats['test_users'] += 1
                    # Check if unapproved
                    elif not profile.is_approved:
                        reason = "unapproved"
                        stats['unapproved'] += 1
                    # Check if missing phone
                    elif not profile.phone_number:
                        reason = "missing_phone"
                        stats['missing_phone'] += 1
                    else:
                        # Valid contact
                        stats['valid'] += 1

                except CrushProfile.DoesNotExist:
                    reason = "orphaned"
                    stats['orphaned'] += 1

            # Decide if should delete based on flags
            should_delete = False
            if reason:
                if test_only and reason == "test_user":
                    should_delete = True
                elif orphaned_only and reason in ["orphaned", "no_profile_id"]:
                    should_delete = True
                elif not test_only and not orphaned_only:
                    # Delete all invalid contacts
                    should_delete = True

            if should_delete:
                stats['to_delete'].append({
                    'id': contact_id,
                    'name': display_name,
                    'email': email,
                    'profile_id': profile_id,
                    'reason': reason,
                })

        # Show breakdown
        self.stdout.write(self.style.WARNING("\nBREAKDOWN:"))
        self.stdout.write(f"  Test user contacts (example.com, etc.):  {stats['test_users']:3d} ({stats['test_users']/stats['total']*100:5.1f}%)")
        self.stdout.write(f"  Orphaned (profile not in DB):            {stats['orphaned']:3d} ({stats['orphaned']/stats['total']*100:5.1f}%)")
        self.stdout.write(f"  Unapproved profiles:                     {stats['unapproved']:3d} ({stats['unapproved']/stats['total']*100:5.1f}%)")
        self.stdout.write(f"  Missing phone number:                    {stats['missing_phone']:3d} ({stats['missing_phone']/stats['total']*100:5.1f}%)")
        self.stdout.write(f"  Valid approved contacts:                 {stats['valid']:3d} ({stats['valid']/stats['total']*100:5.1f}%)")

        # Show actions
        delete_count = len(stats['to_delete'])
        keep_count = stats['total'] - delete_count

        self.stdout.write(self.style.WARNING(f"\nACTIONS {'(DRY RUN)' if dry_run else ''}:"))
        action_verb = "Would delete" if dry_run else "Will delete"
        self.stdout.write(f"  {action_verb}: {delete_count:3d} contacts")
        self.stdout.write(f"  Would keep:   {keep_count:3d} contacts")

        if delete_count == 0:
            self.stdout.write(self.style.SUCCESS("\nNo contacts to delete!"))
            return

        # Show sample of what will be deleted
        if delete_count > 0:
            self.stdout.write(self.style.WARNING(f"\nSample of contacts to delete (showing up to 10):"))
            for item in stats['to_delete'][:10]:
                reason_label = {
                    'test_user': 'TEST USER',
                    'orphaned': 'ORPHANED',
                    'no_profile_id': 'NO PROFILE ID',
                    'unapproved': 'UNAPPROVED',
                    'missing_phone': 'NO PHONE',
                }.get(item['reason'], 'UNKNOWN')

                self.stdout.write(
                    f"  [{reason_label:15s}] {item['name']:40s} (ID: {item['profile_id'] or 'N/A':5s}, Email: {item['email'] or 'N/A'})"
                )

            if delete_count > 10:
                self.stdout.write(f"  ... and {delete_count - 10} more")

        # Confirm before deleting (unless dry run)
        if not dry_run:
            self.stdout.write(self.style.WARNING(
                f"\n{'!' * 80}\n"
                f"WARNING: This will DELETE {delete_count} contacts from Outlook!\n"
                f"{'!' * 80}\n"
            ))
            confirm = input("Type 'yes' to confirm deletion: ")
            if confirm.lower() != 'yes':
                self.stdout.write(self.style.ERROR("Aborted."))
                return

        # Delete contacts
        if dry_run:
            self.stdout.write(self.style.NOTICE(
                f"\nDRY RUN: Would delete {delete_count} contacts"
            ))
        else:
            self.stdout.write(self.style.WARNING(f"\nDeleting {delete_count} contacts..."))
            deleted = 0
            errors = 0

            for item in stats['to_delete']:
                try:
                    success = service.delete_contact(item['id'])
                    if success:
                        deleted += 1
                        # Clear outlook_contact_id from database if profile exists
                        if item['profile_id']:
                            try:
                                CrushProfile.objects.filter(
                                    pk=item['profile_id'],
                                    outlook_contact_id=item['id']
                                ).update(outlook_contact_id="")
                            except Exception:
                                pass  # Profile might not exist
                        self.stdout.write(f"  ✓ Deleted: {item['name']}")
                    else:
                        errors += 1
                        self.stdout.write(self.style.ERROR(f"  ✗ Failed: {item['name']}"))
                except Exception as e:
                    errors += 1
                    self.stdout.write(self.style.ERROR(f"  ✗ Error deleting {item['name']}: {e}"))

            self.stdout.write(self.style.SUCCESS(
                f"\n{'=' * 80}\n"
                f"Cleanup complete!\n"
                f"  Deleted: {deleted}\n"
                f"  Errors:  {errors}\n"
                f"  Kept:    {keep_count}\n"
                f"{'=' * 80}\n"
            ))

    def _extract_profile_id(self, notes: str) -> int:
        """
        Extract profile ID from contact notes.

        Args:
            notes: personalNotes field from contact

        Returns:
            int: Profile ID or None if not found
        """
        if not notes:
            return None

        # Look for "Profile ID: 123" pattern
        match = re.search(r'Profile ID:\s*(\d+)', notes)
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                return None

        return None
