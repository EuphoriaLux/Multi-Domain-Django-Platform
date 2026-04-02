"""
Management command to backfill UserDataConsent for existing users.
This should be run once after deploying the two-tier consent system.
"""
from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from django.utils import timezone
from crush_lu.models.profiles import UserDataConsent, CrushProfile


class Command(BaseCommand):
    help = 'Backfill UserDataConsent for existing users'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be created without actually creating records',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']

        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN MODE - No changes will be made'))

        # Find users without consent records
        users_without_consent = User.objects.filter(data_consent__isnull=True)
        total_users = users_without_consent.count()

        if total_users == 0:
            self.stdout.write(self.style.SUCCESS('All users already have consent records'))
            return

        self.stdout.write(f'Found {total_users} users without consent records')

        created_count = 0
        for user in users_without_consent:
            has_crushlu_profile = hasattr(user, 'crushprofile')

            if dry_run:
                self.stdout.write(
                    f'Would create consent for user {user.id} ({user.email}): '
                    f'powerup=True, crushlu={has_crushlu_profile}'
                )
            else:
                UserDataConsent.objects.create(
                    user=user,
                    powerup_consent_given=True,  # Implicit consent
                    powerup_consent_date=user.date_joined,
                    crushlu_consent_given=has_crushlu_profile,  # Explicit if profile exists
                    crushlu_consent_date=user.date_joined if has_crushlu_profile else None,
                )
                created_count += 1

                if created_count % 100 == 0:
                    self.stdout.write(f'Created {created_count}/{total_users} consent records...')

        if dry_run:
            self.stdout.write(self.style.SUCCESS(f'Would create {total_users} consent records'))
        else:
            self.stdout.write(self.style.SUCCESS(f'Successfully created {created_count} consent records'))
