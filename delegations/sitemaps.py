"""
Sitemap configuration for Delegations.lu.

Generates multilingual URLs (en, de, fr) with xhtml:link hreflang alternates
for SEO indexing in Luxembourg.
"""

from django.contrib.sitemaps import Sitemap
from django.urls import reverse


class DelegationsStaticViewSitemap(Sitemap):
    """
    Sitemap for public marketing and employment-help pages on delegations.lu.
    """

    protocol = "https"
    i18n = True
    alternates = True
    x_default = True

    def items(self):
        return [
            "delegations_home_i18n",
        ]

    def location(self, item):
        return reverse(item)

    def priority(self, item):
        return 1.0

    def changefreq(self, item):
        return "weekly"
