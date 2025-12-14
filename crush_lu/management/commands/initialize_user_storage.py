from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from crush_lu.models import CrushProfile
from crush_lu.storage import initialize_user_storage, user_storage_exists


class Command(BaseCommand):
    help = 'Initialize storage folders for existing Crush.lu users'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be done without making changes',
        )
        parser.add_argument(
            '--all-users',
            action='store_true',
            help='Process all users, not just those with CrushProfiles',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        all_users = options['all_users']

        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN - No changes will be made\n'))

        # Get users to process
        if all_users:
            users = User.objects.all()
            self.stdout.write(f'Processing ALL {users.count()} users...\n')
        else:
            # Only users with CrushProfile (actual Crush.lu users)
            users = User.objects.filter(crushprofile__isnull=False)
            self.stdout.write(f'Processing {users.count()} users with CrushProfiles...\n')

        created_count = 0
        skipped_count = 0
        error_count = 0

        for user in users:
            # Check if storage already exists
            if user_storage_exists(user.id):
                skipped_count += 1
                if options['verbosity'] >= 2:
                    self.stdout.write(
                        self.style.WARNING(f'  Skipped (exists): User {user.id} ({user.email})')
                    )
                continue

            if dry_run:
                created_count += 1
                self.stdout.write(
                    self.style.SUCCESS(f'  Would create: User {user.id} ({user.email})')
                )
            else:
                try:
                    success = initialize_user_storage(user.id)
                    if success:
                        created_count += 1
                        self.stdout.write(
                            self.style.SUCCESS(f'  Created: User {user.id} ({user.email})')
                        )
                    else:
                        error_count += 1
                        self.stdout.write(
                            self.style.ERROR(f'  Failed: User {user.id} ({user.email})')
                        )
                except Exception as e:
                    error_count += 1
                    self.stdout.write(
                        self.style.ERROR(f'  Error for User {user.id}: {str(e)}')
                    )

        # Summary
        self.stdout.write('\n' + '=' * 50)
        self.stdout.write(self.style.SUCCESS(f'Storage folders created: {created_count}'))
        self.stdout.write(self.style.WARNING(f'Already existed (skipped): {skipped_count}'))
        if error_count:
            self.stdout.write(self.style.ERROR(f'Errors: {error_count}'))

        if dry_run:
            self.stdout.write(self.style.WARNING('\nDRY RUN - No changes were made'))
            self.stdout.write('Run without --dry-run to create the folders')
