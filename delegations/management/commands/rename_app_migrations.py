"""
Management command to rename app in django_migrations table.

Run this BEFORE running migrations after renaming crush_delegation to delegations.

Usage:
    python manage.py rename_app_migrations

This is safe to run multiple times - it only updates rows where app='crush_delegation'.
"""
from django.core.management.base import BaseCommand
from django.db import connection


class Command(BaseCommand):
    help = 'Rename crush_delegation to delegations in django_migrations table'

    def handle(self, *args, **options):
        with connection.cursor() as cursor:
            # Check current state
            cursor.execute(
                "SELECT COUNT(*) FROM django_migrations WHERE app = 'crush_delegation'"
            )
            old_count = cursor.fetchone()[0]

            cursor.execute(
                "SELECT COUNT(*) FROM django_migrations WHERE app = 'delegations'"
            )
            new_count = cursor.fetchone()[0]

            if old_count == 0 and new_count > 0:
                self.stdout.write(
                    self.style.SUCCESS('Already migrated: delegations app found, no crush_delegation entries.')
                )
                return

            if old_count == 0 and new_count == 0:
                self.stdout.write(
                    self.style.WARNING('No migrations found for either app name. Fresh database?')
                )
                return

            # Perform the rename
            cursor.execute(
                "UPDATE django_migrations SET app = 'delegations' WHERE app = 'crush_delegation'"
            )

            self.stdout.write(
                self.style.SUCCESS(f'Successfully renamed {old_count} migration(s) from crush_delegation to delegations.')
            )
