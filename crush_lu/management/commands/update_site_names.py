"""
Management command to update Django Sites with proper branding
Run: python manage.py update_site_names
"""
from django.core.management.base import BaseCommand
from django.contrib.sites.models import Site


class Command(BaseCommand):
    help = 'Update Django Sites with proper display names for multi-domain setup'

    def handle(self, *args, **options):
        """Update site names for proper branding"""

        # Update or create PowerUp site
        powerup, created = Site.objects.update_or_create(
            domain='powerup.lu',
            defaults={'name': 'PowerUP'}
        )
        action = "Created" if created else "Updated"
        self.stdout.write(self.style.SUCCESS(f'{action} PowerUP site (ID: {powerup.id})'))

        # Update or create Crush.lu site
        crush, created = Site.objects.update_or_create(
            domain='crush.lu',
            defaults={'name': 'Crush.lu'}
        )
        action = "Created" if created else "Updated"
        self.stdout.write(self.style.SUCCESS(f'{action} Crush.lu site (ID: {crush.id})'))

        # Update or create VinsDelux site (if needed)
        vinsdelux, created = Site.objects.update_or_create(
            domain='vinsdelux.com',
            defaults={'name': 'VinsDelux'}
        )
        action = "Created" if created else "Updated"
        self.stdout.write(self.style.SUCCESS(f'{action} VinsDelux site (ID: {vinsdelux.id})'))

        self.stdout.write(self.style.SUCCESS('\n✅ All sites updated successfully!'))
        self.stdout.write(self.style.WARNING('\n⚠️  Note: SITE_ID should NOT be hardcoded in settings.py'))
        self.stdout.write(self.style.WARNING('CurrentSiteMiddleware will dynamically set the site based on domain.'))
