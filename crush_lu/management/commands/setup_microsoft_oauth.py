"""
Management command to set up Microsoft OAuth SocialApp for Crush.lu
Run: python manage.py setup_microsoft_oauth

Required environment variables:
- MICROSOFT_CLIENT_ID: Your Microsoft/Azure AD app client ID
- MICROSOFT_CLIENT_SECRET: Your Microsoft/Azure AD app client secret

Azure Portal App Registration configuration:
- Redirect URIs: https://crush.lu/accounts/microsoft/login/callback/
- Supported account types: Accounts in any organizational directory and personal Microsoft accounts
"""
import os
from django.core.management.base import BaseCommand
from django.contrib.sites.models import Site
from allauth.socialaccount.models import SocialApp


class Command(BaseCommand):
    help = 'Set up Microsoft OAuth SocialApp for Crush.lu'

    def add_arguments(self, parser):
        parser.add_argument(
            '--client-id',
            type=str,
            help='Microsoft OAuth client ID (overrides MICROSOFT_CLIENT_ID env var)',
        )
        parser.add_argument(
            '--client-secret',
            type=str,
            help='Microsoft OAuth client secret (overrides MICROSOFT_CLIENT_SECRET env var)',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be done without making changes',
        )

    def handle(self, *args, **options):
        """Set up Microsoft OAuth SocialApp and associate with crush.lu Site"""

        # Get credentials from options or environment
        client_id = options.get('client_id') or os.environ.get('MICROSOFT_CLIENT_ID')
        client_secret = options.get('client_secret') or os.environ.get('MICROSOFT_CLIENT_SECRET')
        dry_run = options.get('dry_run', False)

        if not client_id:
            self.stdout.write(self.style.ERROR(
                'Missing MICROSOFT_CLIENT_ID. Provide via --client-id or environment variable.'
            ))
            return

        if not client_secret:
            self.stdout.write(self.style.ERROR(
                'Missing MICROSOFT_CLIENT_SECRET. Provide via --client-secret or environment variable.'
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

        # Step 2: Create or update Microsoft SocialApp
        self.stdout.write('Step 2: Setting up Microsoft SocialApp...')

        if dry_run:
            existing = SocialApp.objects.filter(provider='microsoft').first()
            if existing:
                self.stdout.write(self.style.WARNING(f'  Would update existing Microsoft SocialApp (ID: {existing.id})'))
            else:
                self.stdout.write(self.style.WARNING('  Would create new Microsoft SocialApp'))
        else:
            social_app, created = SocialApp.objects.update_or_create(
                provider='microsoft',
                defaults={
                    'name': 'Microsoft',
                    'client_id': client_id,
                    'secret': client_secret,
                }
            )
            action = "Created" if created else "Updated"
            self.stdout.write(self.style.SUCCESS(f'  {action} Microsoft SocialApp (ID: {social_app.id})'))

        # Step 3: Associate SocialApp with crush.lu Site
        self.stdout.write('Step 3: Associating SocialApp with crush.lu Site...')

        if dry_run:
            self.stdout.write(self.style.WARNING('  Would associate Microsoft SocialApp with crush.lu Site'))
        else:
            if crush_site:
                # Check if already associated
                if crush_site in social_app.sites.all():
                    self.stdout.write(self.style.SUCCESS('  Already associated with crush.lu Site'))
                else:
                    social_app.sites.add(crush_site)
                    self.stdout.write(self.style.SUCCESS('  Associated Microsoft SocialApp with crush.lu Site'))

        # Summary
        self.stdout.write('')
        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN COMPLETE - No changes were made.'))
            self.stdout.write(self.style.WARNING('Run without --dry-run to apply changes.'))
        else:
            self.stdout.write(self.style.SUCCESS('Microsoft OAuth setup complete!'))

        self.stdout.write('')
        self.stdout.write('Azure Portal App Registration Configuration:')
        self.stdout.write(self.style.WARNING('  Redirect URIs (Web platform):'))
        self.stdout.write('    - https://crush.lu/accounts/microsoft/login/callback/')
        self.stdout.write('    - http://localhost:8000/accounts/microsoft/login/callback/ (for local development)')
        self.stdout.write(self.style.WARNING('  Supported account types:'))
        self.stdout.write('    - Accounts in any organizational directory (Any Azure AD directory - Multitenant)')
        self.stdout.write('    - Personal Microsoft accounts (e.g. Skype, Xbox)')
        self.stdout.write(self.style.WARNING('  API Permissions:'))
        self.stdout.write('    - Microsoft Graph: User.Read (delegated)')
        self.stdout.write('    - Microsoft Graph: profile (delegated)')
        self.stdout.write('    - Microsoft Graph: email (delegated)')
        self.stdout.write('    - Microsoft Graph: openid (delegated)')
