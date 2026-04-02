"""
Management command to manually verify a user's phone number for local development.

Usage:
    python manage.py verify_phone test@test.lu +352691123456
    python manage.py verify_phone --username=test +352691123456
    python manage.py verify_phone test@test.lu  # Just mark as verified without changing number
"""
from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth import get_user_model
from django.utils import timezone
from crush_lu.models import CrushProfile

User = get_user_model()


class Command(BaseCommand):
    help = 'Manually verify a phone number for local development (bypasses Firebase)'

    def add_arguments(self, parser):
        parser.add_argument(
            'identifier',
            type=str,
            help='User email or username'
        )
        parser.add_argument(
            'phone_number',
            type=str,
            nargs='?',
            default=None,
            help='Phone number to set (optional - if not provided, just marks existing number as verified)'
        )
        parser.add_argument(
            '--username',
            action='store_true',
            help='Treat identifier as username instead of email'
        )
        parser.add_argument(
            '--unverify',
            action='store_true',
            help='Unverify the phone number instead of verifying'
        )

    def handle(self, *args, **options):
        identifier = options['identifier']
        phone_number = options['phone_number']
        use_username = options['username']
        unverify = options['unverify']

        # Find the user
        try:
            if use_username:
                user = User.objects.get(username=identifier)
            else:
                user = User.objects.get(email=identifier)
        except User.DoesNotExist:
            field = 'username' if use_username else 'email'
            raise CommandError(f'User with {field}="{identifier}" not found')

        # Get or check for CrushProfile
        try:
            profile = CrushProfile.objects.get(user=user)
        except CrushProfile.DoesNotExist:
            raise CommandError(f'User "{identifier}" does not have a CrushProfile')

        if unverify:
            # Unverify the phone
            profile.phone_verified = False
            profile.phone_verified_at = None
            profile.phone_verification_uid = None
            # Use update_fields to bypass save() protection
            CrushProfile.objects.filter(pk=profile.pk).update(
                phone_verified=False,
                phone_verified_at=None,
                phone_verification_uid=None
            )
            self.stdout.write(self.style.SUCCESS(
                f'Phone unverified for user "{user.email}"'
            ))
            return

        # Set phone number if provided
        if phone_number:
            profile.phone_number = phone_number

        if not profile.phone_number:
            raise CommandError(
                f'User "{identifier}" has no phone number set. '
                f'Provide a phone number as argument: python manage.py verify_phone {identifier} +352691123456'
            )

        # Mark as verified using direct update to bypass save() protection
        CrushProfile.objects.filter(pk=profile.pk).update(
            phone_number=profile.phone_number,
            phone_verified=True,
            phone_verified_at=timezone.now(),
            phone_verification_uid=f'dev-verified-{timezone.now().timestamp()}'
        )

        self.stdout.write(self.style.SUCCESS(
            f'Phone verified for user "{user.email}":\n'
            f'   Phone: {profile.phone_number}\n'
            f'   Verified at: {timezone.now().strftime("%Y-%m-%d %H:%M:%S")}'
        ))
