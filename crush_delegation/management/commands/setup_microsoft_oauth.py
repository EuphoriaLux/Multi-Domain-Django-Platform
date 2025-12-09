"""
Management command to set up Microsoft OAuth for Crush Delegation.

Usage:
    python manage.py setup_microsoft_oauth --client-id=XXX --client-secret=YYY

Or with environment variables:
    python manage.py setup_microsoft_oauth
"""
import os
from django.core.management.base import BaseCommand
from django.contrib.sites.models import Site
from allauth.socialaccount.models import SocialApp


class Command(BaseCommand):
    help = 'Set up Microsoft OAuth SocialApp for Crush Delegation'

    def add_arguments(self, parser):
        parser.add_argument(
            '--client-id',
            type=str,
            help='Microsoft Application (client) ID',
        )
        parser.add_argument(
            '--client-secret',
            type=str,
            help='Microsoft Client Secret Value',
        )

    def handle(self, *args, **options):
        # Get credentials from arguments or environment
        client_id = options.get('client_id') or os.environ.get('DELEGATION_GRAPH_CLIENT_ID')
        client_secret = options.get('client_secret') or os.environ.get('DELEGATION_GRAPH_CLIENT_SECRET')

        if not client_id:
            self.stderr.write(self.style.ERROR(
                'Client ID required. Use --client-id or set DELEGATION_GRAPH_CLIENT_ID env var'
            ))
            return

        if not client_secret:
            self.stderr.write(self.style.ERROR(
                'Client Secret required. Use --client-secret or set DELEGATION_GRAPH_CLIENT_SECRET env var'
            ))
            return

        # Get or create the SocialApp for Microsoft
        social_app, created = SocialApp.objects.update_or_create(
            provider='microsoft',
            defaults={
                'name': 'Microsoft (Crush Delegation)',
                'client_id': client_id,
                'secret': client_secret,
            }
        )

        if created:
            self.stdout.write(self.style.SUCCESS('Created Microsoft SocialApp'))
        else:
            self.stdout.write(self.style.SUCCESS('Updated Microsoft SocialApp'))

        # Ensure site exists for delegation.crush.lu
        delegation_site, site_created = Site.objects.get_or_create(
            domain='delegation.crush.lu',
            defaults={'name': 'Crush Delegation'}
        )

        if site_created:
            self.stdout.write(self.style.SUCCESS('Created Site for delegation.crush.lu'))

        # Also add localhost for development
        localhost_site, localhost_created = Site.objects.get_or_create(
            domain='localhost:8000',
            defaults={'name': 'Localhost Development'}
        )

        if localhost_created:
            self.stdout.write(self.style.SUCCESS('Created Site for localhost:8000'))

        # Associate SocialApp with sites
        social_app.sites.add(delegation_site)
        social_app.sites.add(localhost_site)

        self.stdout.write(self.style.SUCCESS(
            f'\nMicrosoft OAuth configured successfully!'
            f'\n  Client ID: {client_id[:8]}...{client_id[-4:]}'
            f'\n  Sites: delegation.crush.lu, localhost:8000'
        ))
