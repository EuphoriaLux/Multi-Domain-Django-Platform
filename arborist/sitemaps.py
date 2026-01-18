"""
Sitemap configuration for Baumwart - Tom Aakrann (arborist.lu).

Provides sitemap classes for SEO optimization with multi-language support.
Generates URLs for all supported languages (en, de, fr) with hreflang alternates.
"""

from django.contrib.sitemaps import Sitemap
from django.urls import reverse


class ArboristStaticViewSitemap(Sitemap):
    """
    Sitemap for all static pages on arborist.lu.

    Generates URLs for all three languages (en, de, fr) with:
    - Proper priority based on page importance
    - Monthly change frequency for most pages
    - xhtml:link alternates for hreflang SEO
    """

    protocol = "https"
    i18n = True  # Generate URLs for all languages (en, de, fr)
    alternates = True  # Include xhtml:link elements with hreflang

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

    def changefreq(self, item):
        """Return change frequency based on page type."""
        if item == "arborist:home":
            return "weekly"
        elif item in ["arborist:gallery"]:
            return "weekly"  # Gallery may get new photos
        else:
            return "monthly"
