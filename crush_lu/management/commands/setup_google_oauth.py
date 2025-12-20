"""
Management command to set up Google OAuth SocialApp for Crush.lu
Run: python manage.py setup_google_oauth

Required environment variables:
- GOOGLE_CLIENT_ID: Your Google OAuth client ID
- GOOGLE_CLIENT_SECRET: Your Google OAuth client secret

Google Cloud Console configuration:
- Authorized JavaScript origins: https://crush.lu
- Authorized redirect URIs: https://crush.lu/accounts/google/login/callback/
"""
import os
from django.core.management.base import BaseCommand
from django.contrib.sites.models import Site
from allauth.socialaccount.models import SocialApp


class Command(BaseCommand):
    help = 'Set up Google OAuth SocialApp for Crush.lu'

    def add_arguments(self, parser):
        parser.add_argument(
            '--client-id',
            type=str,
            help='Google OAuth client ID (overrides GOOGLE_CLIENT_ID env var)',
        )
        parser.add_argument(
            '--client-secret',
            type=str,
            help='Google OAuth client secret (overrides GOOGLE_CLIENT_SECRET env var)',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be done without making changes',
        )

    def handle(self, *args, **options):
        """Set up Google OAuth SocialApp and associate with crush.lu Site"""

        # Get credentials from options or environment
        client_id = options.get('client_id') or os.environ.get('GOOGLE_CLIENT_ID')
        client_secret = options.get('client_secret') or os.environ.get('GOOGLE_CLIENT_SECRET')
        dry_run = options.get('dry_run', False)

        if not client_id:
            self.stdout.write(self.style.ERROR(
                'Missing GOOGLE_CLIENT_ID. Provide via --client-id or environment variable.'
            ))
            return

        if not client_secret:
            self.stdout.write(self.style.ERROR(
                'Missing GOOGLE_CLIENT_SECRET. Provide via --client-secret or environment variable.'
            ))
            return

        if dry_run:
            self.stdout.write(self.style.WARNING('\n[DRY RUN] No changes will be made.\n'))

        # Step 1: Ensure crush.lu Site exists
        self.stdout.write('Step 1: Checking crush.lu Site...')
        try:
            crush_site = Site.objects.get(domain='crush.lu')
            self.stdout.write(self.style.SUCCESS(f'  Found crush.lu Site (ID: {crush_site.id})'))
        except Site.DoesNotExist:
            if dry_run:
                self.stdout.write(self.style.WARNING('  Would create crush.lu Site'))
                crush_site = None
            else:
                crush_site = Site.objects.create(
                    domain='crush.lu',
                    name='Crush.lu'
                )
                self.stdout.write(self.style.SUCCESS(f'  Created crush.lu Site (ID: {crush_site.id})'))

        # Step 2: Create or update Google SocialApp
        self.stdout.write('Step 2: Setting up Google SocialApp...')

        if dry_run:
            existing = SocialApp.objects.filter(provider='google').first()
            if existing:
                self.stdout.write(self.style.WARNING(f'  Would update existing Google SocialApp (ID: {existing.id})'))
            else:
                self.stdout.write(self.style.WARNING('  Would create new Google SocialApp'))
        else:
            social_app, created = SocialApp.objects.update_or_create(
                provider='google',
                defaults={
                    'name': 'Google',
                    'client_id': client_id,
                    'secret': client_secret,
                }
            )
            action = "Created" if created else "Updated"
            self.stdout.write(self.style.SUCCESS(f'  {action} Google SocialApp (ID: {social_app.id})'))

        # Step 3: Associate SocialApp with crush.lu Site
        self.stdout.write('Step 3: Associating SocialApp with crush.lu Site...')

        if dry_run:
            self.stdout.write(self.style.WARNING('  Would associate Google SocialApp with crush.lu Site'))
        else:
            if crush_site:
                # Check if already associated
                if crush_site in social_app.sites.all():
                    self.stdout.write(self.style.SUCCESS('  Already associated with crush.lu Site'))
                else:
                    social_app.sites.add(crush_site)
                    self.stdout.write(self.style.SUCCESS('  Associated Google SocialApp with crush.lu Site'))

        # Summary
        self.stdout.write('')
        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN COMPLETE - No changes were made.'))
            self.stdout.write(self.style.WARNING('Run without --dry-run to apply changes.'))
        else:
            self.stdout.write(self.style.SUCCESS('Google OAuth setup complete!'))

        self.stdout.write('')
        self.stdout.write('Google Cloud Console Configuration:')
        self.stdout.write(self.style.WARNING('  Authorized JavaScript origins:'))
        self.stdout.write('    - https://crush.lu')
        self.stdout.write('    - http://localhost:8000 (for local development)')
        self.stdout.write(self.style.WARNING('  Authorized redirect URIs:'))
        self.stdout.write('    - https://crush.lu/accounts/google/login/callback/')
        self.stdout.write('    - http://localhost:8000/accounts/google/login/callback/')
