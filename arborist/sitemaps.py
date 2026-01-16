"""
Sitemap configuration for Baumwart - Tom Aakrann (arborist.lu).

Provides sitemap classes for SEO optimization.
"""

from django.contrib.sitemaps import Sitemap
from django.urls import reverse


class ArboristStaticViewSitemap(Sitemap):
    """Sitemap for all static pages on arborist.lu."""

    changefreq = "monthly"
    protocol = "https"

    def items(self):
        """Return list of URL names to include in sitemap."""
        return [
            "arborist:home",
            "arborist:obstbaumpflege",
            "arborist:baumpflege",
            "arborist:baumkontrolle",
            "arborist:oekologie",
            "arborist:technik",
            "arborist:about",
            "arborist:contact",
            "arborist:gallery",
            "arborist:faq",
        ]

    def location(self, item):
        """Return the URL for each item."""
        return reverse(item)

    def priority(self, item):
        """Return priority based on page importance."""
        priorities = {
            "arborist:home": 1.0,
            "arborist:baumkontrolle": 0.9,
            "arborist:baumpflege": 0.9,
            "arborist:obstbaumpflege": 0.9,
            "arborist:contact": 0.8,
            "arborist:about": 0.7,
            "arborist:oekologie": 0.7,
            "arborist:technik": 0.7,
            "arborist:gallery": 0.6,
            "arborist:faq": 0.6,
        }
        return priorities.get(item, 0.5)
