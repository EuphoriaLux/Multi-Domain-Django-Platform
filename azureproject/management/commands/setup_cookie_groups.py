"""
Management command to create cookie consent groups for GDPR compliance.

Usage:
    python manage.py setup_cookie_groups

This creates the following cookie groups:
- essential: Required cookies (session, CSRF, language)
- analytics: Google Analytics, site performance tracking
- marketing: Facebook Pixel, advertising cookies

startup.sh runs this on every container start (both slots, swap warm-up
included), after migrate: django-cookie-consent's own decline view deletes
only the cookies a group registers here, so a deployed database must hold
these rows. It is idempotent: groups are looked up by varname and cookies by
(group, name), and existing rows are never modified.

Adding a cookie to a group moves that group's version (the date of its
newest cookie), so every earlier acceptance of the group counts as undecided
and the banner asks again. That also happens when a row deleted in the admin
is created again by the next start.
"""

from django.core.management.base import BaseCommand
from cookie_consent.cache import delete_cache
from cookie_consent.models import CookieGroup, Cookie


class Command(BaseCommand):
    help = "Create cookie consent groups for GDPR compliance"

    def _ensure_cookies(self, group, cookies):
        for cookie_data in cookies:
            # Looked up by (group, name), not by the unique key (group, name,
            # domain): a row whose domain was edited in the admin must not get
            # a duplicate beside it, which would move the group version and
            # ask everyone again on every database where that edit happened.
            try:
                cookie, created = Cookie.objects.get_or_create(
                    cookiegroup=group,
                    name=cookie_data["name"],
                    defaults={
                        "description": cookie_data["description"],
                        "domain": cookie_data["domain"],
                    },
                )
            except Cookie.MultipleObjectsReturned:
                # Several rows share the name (different domains, added in the
                # admin): the cookie is registered; leave them as they are
                # rather than abort before the remaining rows.
                self.stdout.write(
                    self.style.WARNING(
                        f"    Several {group.varname} cookies named "
                        f"{cookie_data['name']}; left as they are"
                    )
                )
                continue
            if created:
                self.stdout.write(f"    Added cookie: {cookie.name}")

    def handle(self, *args, **options):
        self.stdout.write("Setting up cookie consent groups...")

        # Essential Cookies Group
        essential, created = CookieGroup.objects.get_or_create(
            varname="essential",
            defaults={
                "name": "Essential Cookies",
                "description": (
                    "These cookies are necessary for the website to function properly. "
                    "They enable basic features like page navigation, secure areas access, "
                    "and session management. The website cannot function properly without these cookies."
                ),
                "is_required": True,
                "ordering": 0,
            },
        )
        if created:
            self.stdout.write(
                self.style.SUCCESS(f"  Created cookie group: {essential.name}")
            )
        else:
            self.stdout.write(f"  Cookie group already exists: {essential.name}")

        # Essential cookies
        essential_cookies = [
            {
                "name": "sessionid",
                "description": "Django session cookie - stores your session ID for authentication",
                "domain": "",
            },
            {
                "name": "csrftoken",
                "description": "CSRF protection token - prevents cross-site request forgery attacks",
                "domain": "",
            },
            {
                "name": "django_language",
                "description": "Language preference - remembers your selected language",
                "domain": "",
            },
            {
                "name": "cookie_consent",
                "description": "Cookie consent preference - stores your cookie choices",
                "domain": "",
            },
        ]
        self._ensure_cookies(essential, essential_cookies)

        # Analytics Cookies Group
        analytics, created = CookieGroup.objects.get_or_create(
            varname="analytics",
            defaults={
                "name": "Analytics Cookies",
                "description": (
                    "These cookies help us understand how visitors interact with our website "
                    "by collecting and reporting information anonymously. This helps us improve "
                    "our website and your experience."
                ),
                "is_required": False,
                "ordering": 1,
            },
        )
        if created:
            self.stdout.write(
                self.style.SUCCESS(f"  Created cookie group: {analytics.name}")
            )
        else:
            self.stdout.write(f"  Cookie group already exists: {analytics.name}")

        # Analytics cookies
        analytics_cookies = [
            {
                "name": "_ga",
                "description": "Google Analytics - distinguishes unique users (expires: 2 years)",
                "domain": "",
            },
            {
                "name": "_ga_*",
                "description": "Google Analytics 4 - maintains session state (expires: 2 years)",
                "domain": "",
            },
            {
                "name": "_gid",
                "description": "Google Analytics - distinguishes users (expires: 24 hours)",
                "domain": "",
            },
            {
                "name": "_gat",
                "description": "Google Analytics - throttle request rate (expires: 1 minute)",
                "domain": "",
            },
            {
                "name": "ai_user",
                "description": "Application Insights - distinguishes browser users",
                "domain": "",
            },
            {
                "name": "ai_session",
                "description": "Application Insights - identifies browser sessions",
                "domain": "",
            },
        ]
        self._ensure_cookies(analytics, analytics_cookies)

        # Marketing Cookies Group
        marketing, created = CookieGroup.objects.get_or_create(
            varname="marketing",
            defaults={
                "name": "Marketing Cookies",
                "description": (
                    "These cookies are used to track visitors across websites. "
                    "The intention is to display ads that are relevant and engaging "
                    "for the individual user."
                ),
                "is_required": False,
                "ordering": 2,
            },
        )
        if created:
            self.stdout.write(
                self.style.SUCCESS(f"  Created cookie group: {marketing.name}")
            )
        else:
            self.stdout.write(f"  Cookie group already exists: {marketing.name}")

        # Marketing cookies
        marketing_cookies = [
            {
                "name": "_fbp",
                "description": "Facebook Pixel - tracks visits across websites for advertising (expires: 3 months)",
                "domain": "",
            },
            {
                "name": "fr",
                "description": "Facebook - delivers advertising (expires: 3 months)",
                "domain": ".facebook.com",
            },
        ]
        self._ensure_cookies(marketing, marketing_cookies)

        # Each save above already clears django-cookie-consent's cached groups,
        # but inside get_or_create's transaction, before the row is committed:
        # a request served meanwhile (the old container keeps answering during
        # a restart or a swap warm-up) can cache the groups without the new
        # row for an hour. Every write has committed by now (autocommit), so
        # clear once more.
        delete_cache()

        self.stdout.write(self.style.SUCCESS("\nCookie consent groups setup complete!"))
        self.stdout.write("\nSummary:")
        self.stdout.write(
            f"  - Essential: {Cookie.objects.filter(cookiegroup=essential).count()} cookies"
        )
        self.stdout.write(
            f"  - Analytics: {Cookie.objects.filter(cookiegroup=analytics).count()} cookies"
        )
        self.stdout.write(
            f"  - Marketing: {Cookie.objects.filter(cookiegroup=marketing).count()} cookies"
        )
