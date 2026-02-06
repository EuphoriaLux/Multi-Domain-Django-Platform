"""
Management command to check users with missing Crush.lu consent.
This identifies users who signed up before the consent system was implemented.
"""
from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from django.utils import timezone
from crush_lu.models.profiles import UserDataConsent, CrushProfile


class Command(BaseCommand):
    help = 'Check users with missing Crush.lu consent and optionally fix them'

    def add_arguments(self, parser):
        parser.add_argument(
            '--fix',
            action='store_true',
            help='Automatically grant consent to users with existing profiles (backfill)',
        )
        parser.add_argument(
            '--notify',
            action='store_true',
            help='Send email notifications to users asking them to confirm consent',
        )

    def handle(self, *args, **options):
        fix_consent = options['fix']
        notify_users = options['notify']

        # Find users without Crush.lu consent
        users_without_consent = UserDataConsent.objects.filter(
            crushlu_consent_given=False
        ).select_related('user')

        self.stdout.write(f'\nFound {users_without_consent.count()} users without Crush.lu consent\n')

        # Categorize users
        users_with_profiles = []
        users_without_profiles = []

        for consent in users_without_consent:
            if hasattr(consent.user, 'crushprofile'):
                users_with_profiles.append(consent)
            else:
                users_without_profiles.append(consent)

        self.stdout.write(self.style.WARNING(
            f'Users WITH profiles but no consent: {len(users_with_profiles)}'
        ))
        self.stdout.write(
            f'Users WITHOUT profiles (no action needed): {len(users_without_profiles)}\n'
        )

        # Show detailed info about users with profiles
        if users_with_profiles:
            self.stdout.write(self.style.WARNING('\nUsers needing consent (with profiles):'))
            for consent in users_with_profiles[:10]:  # Show first 10
                user = consent.user
                profile = user.crushprofile
                self.stdout.write(
                    f'  - {user.email} (ID: {user.id}, joined: {user.date_joined.date()}, '
                    f'approved: {profile.is_approved})'
                )
            if len(users_with_profiles) > 10:
                self.stdout.write(f'  ... and {len(users_with_profiles) - 10} more')

        # Handle --fix flag
        if fix_consent and users_with_profiles:
            self.stdout.write(self.style.WARNING(
                f'\n--fix flag detected: Granting implicit consent to {len(users_with_profiles)} users'
            ))

            confirmation = input('Are you sure? (yes/no): ')
            if confirmation.lower() != 'yes':
                self.stdout.write(self.style.ERROR('Aborted'))
                return

            fixed_count = 0
            for consent in users_with_profiles:
                consent.crushlu_consent_given = True
                consent.crushlu_consent_date = consent.user.date_joined  # Use signup date
                consent.crushlu_consent_ip = None  # No IP available for old signups
                consent.save()
                fixed_count += 1

                if fixed_count % 10 == 0:
                    self.stdout.write(f'Fixed {fixed_count}/{len(users_with_profiles)}...')

            self.stdout.write(self.style.SUCCESS(
                f'\nSuccessfully granted consent to {fixed_count} users'
            ))

        # Handle --notify flag
        elif notify_users and users_with_profiles:
            self.stdout.write(self.style.WARNING(
                f'\n--notify flag detected: Would send consent request emails to {len(users_with_profiles)} users'
            ))
            self.stdout.write(self.style.ERROR(
                'Email notification not implemented yet. Use --fix to grant implicit consent instead.'
            ))

        # Recommendation
        if users_with_profiles and not fix_consent:
            self.stdout.write(self.style.SUCCESS('\nRecommendation:'))
            self.stdout.write(
                'These users signed up before the consent system was implemented.\n'
                'Since they have active profiles, their continued use of the platform\n'
                'constitutes implied consent. You can safely run:\n'
            )
            self.stdout.write(self.style.SUCCESS('  python manage.py check_missing_consent --fix\n'))
