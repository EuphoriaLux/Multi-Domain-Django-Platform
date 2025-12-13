"""
Management command to create cookie consent groups for GDPR compliance.

Usage:
    python manage.py setup_cookie_groups

This creates the following cookie groups:
- essential: Required cookies (session, CSRF, language)
- analytics: Google Analytics, site performance tracking
- marketing: Facebook Pixel, advertising cookies
"""

from django.core.management.base import BaseCommand
from cookie_consent.models import CookieGroup, Cookie


class Command(BaseCommand):
    help = 'Create cookie consent groups for GDPR compliance'

    def handle(self, *args, **options):
        self.stdout.write('Setting up cookie consent groups...')

        # Essential Cookies Group
        essential, created = CookieGroup.objects.get_or_create(
            varname='essential',
            defaults={
                'name': 'Essential Cookies',
                'description': (
                    'These cookies are necessary for the website to function properly. '
                    'They enable basic features like page navigation, secure areas access, '
                    'and session management. The website cannot function properly without these cookies.'
                ),
                'is_required': True,
                'ordering': 0,
            }
        )
        if created:
            self.stdout.write(self.style.SUCCESS(f'  Created cookie group: {essential.name}'))
        else:
            self.stdout.write(f'  Cookie group already exists: {essential.name}')

        # Essential cookies
        essential_cookies = [
            {
                'name': 'sessionid',
                'description': 'Django session cookie - stores your session ID for authentication',
                'domain': '',
            },
            {
                'name': 'csrftoken',
                'description': 'CSRF protection token - prevents cross-site request forgery attacks',
                'domain': '',
            },
            {
                'name': 'django_language',
                'description': 'Language preference - remembers your selected language',
                'domain': '',
            },
            {
                'name': 'cookie_consent',
                'description': 'Cookie consent preference - stores your cookie choices',
                'domain': '',
            },
        ]

        for cookie_data in essential_cookies:
            cookie, created = Cookie.objects.get_or_create(
                cookiegroup=essential,
                name=cookie_data['name'],
                defaults={
                    'description': cookie_data['description'],
                    'domain': cookie_data['domain'],
                }
            )
            if created:
                self.stdout.write(f'    Added cookie: {cookie.name}')

        # Analytics Cookies Group
        analytics, created = CookieGroup.objects.get_or_create(
            varname='analytics',
            defaults={
                'name': 'Analytics Cookies',
                'description': (
                    'These cookies help us understand how visitors interact with our website '
                    'by collecting and reporting information anonymously. This helps us improve '
                    'our website and your experience.'
                ),
                'is_required': False,
                'ordering': 1,
            }
        )
        if created:
            self.stdout.write(self.style.SUCCESS(f'  Created cookie group: {analytics.name}'))
        else:
            self.stdout.write(f'  Cookie group already exists: {analytics.name}')

        # Analytics cookies
        analytics_cookies = [
            {
                'name': '_ga',
                'description': 'Google Analytics - distinguishes unique users (expires: 2 years)',
                'domain': '',
            },
            {
                'name': '_ga_*',
                'description': 'Google Analytics 4 - maintains session state (expires: 2 years)',
                'domain': '',
            },
            {
                'name': '_gid',
                'description': 'Google Analytics - distinguishes users (expires: 24 hours)',
                'domain': '',
            },
            {
                'name': '_gat',
                'description': 'Google Analytics - throttle request rate (expires: 1 minute)',
                'domain': '',
            },
        ]

        for cookie_data in analytics_cookies:
            cookie, created = Cookie.objects.get_or_create(
                cookiegroup=analytics,
                name=cookie_data['name'],
                defaults={
                    'description': cookie_data['description'],
                    'domain': cookie_data['domain'],
                }
            )
            if created:
                self.stdout.write(f'    Added cookie: {cookie.name}')

        # Marketing Cookies Group
        marketing, created = CookieGroup.objects.get_or_create(
            varname='marketing',
            defaults={
                'name': 'Marketing Cookies',
                'description': (
                    'These cookies are used to track visitors across websites. '
                    'The intention is to display ads that are relevant and engaging '
                    'for the individual user.'
                ),
                'is_required': False,
                'ordering': 2,
            }
        )
        if created:
            self.stdout.write(self.style.SUCCESS(f'  Created cookie group: {marketing.name}'))
        else:
            self.stdout.write(f'  Cookie group already exists: {marketing.name}')

        # Marketing cookies
        marketing_cookies = [
            {
                'name': '_fbp',
                'description': 'Facebook Pixel - tracks visits across websites for advertising (expires: 3 months)',
                'domain': '',
            },
            {
                'name': 'fr',
                'description': 'Facebook - delivers advertising (expires: 3 months)',
                'domain': '.facebook.com',
            },
        ]

        for cookie_data in marketing_cookies:
            cookie, created = Cookie.objects.get_or_create(
                cookiegroup=marketing,
                name=cookie_data['name'],
                defaults={
                    'description': cookie_data['description'],
                    'domain': cookie_data['domain'],
                }
            )
            if created:
                self.stdout.write(f'    Added cookie: {cookie.name}')

        self.stdout.write(self.style.SUCCESS('\nCookie consent groups setup complete!'))
        self.stdout.write('\nSummary:')
        self.stdout.write(f'  - Essential: {Cookie.objects.filter(cookiegroup=essential).count()} cookies')
        self.stdout.write(f'  - Analytics: {Cookie.objects.filter(cookiegroup=analytics).count()} cookies')
        self.stdout.write(f'  - Marketing: {Cookie.objects.filter(cookiegroup=marketing).count()} cookies')
