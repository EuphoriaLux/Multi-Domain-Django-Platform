# crush_lu/views_seo.py
"""
SEO-related views for Crush.lu.

This module provides views for:
- robots.txt generation
- Structured data helpers
"""

from django.http import HttpResponse
from django.views.decorators.cache import cache_page
from django.views.decorators.http import require_GET


@require_GET
@cache_page(60 * 60 * 24)  # Cache for 24 hours
def robots_txt(request):
    """
    Generate robots.txt for search engine crawlers.

    This dynamically generates robots.txt to:
    - Allow crawling of public pages
    - Block admin and private areas
    - Reference the sitemap
    """
    lines = [
        "# Robots.txt for Crush.lu",
        "# https://crush.lu/robots.txt",
        "",
        "User-agent: *",
        "",
        "# Allow public pages",
        "Allow: /",
        "Allow: /about/",
        "Allow: /how-it-works/",
        "Allow: /events/",
        "Allow: /privacy-policy/",
        "Allow: /terms-of-service/",
        "",
        "# Block admin and private areas",
        "Disallow: /admin/",
        "Disallow: /crush-admin/",
        "Disallow: /accounts/",
        "Disallow: /api/",
        "Disallow: /dashboard/",
        "Disallow: /profile/",
        "Disallow: /create-profile/",
        "Disallow: /edit-profile/",
        "Disallow: /connections/",
        "Disallow: /coach/",
        "Disallow: /journey/",
        "Disallow: /media/",
        "",
        "# Block language-prefixed private areas",
        "Disallow: /en/admin/",
        "Disallow: /en/crush-admin/",
        "Disallow: /en/accounts/",
        "Disallow: /en/api/",
        "Disallow: /en/dashboard/",
        "Disallow: /en/profile/",
        "Disallow: /de/admin/",
        "Disallow: /de/crush-admin/",
        "Disallow: /de/accounts/",
        "Disallow: /de/api/",
        "Disallow: /de/dashboard/",
        "Disallow: /de/profile/",
        "Disallow: /fr/admin/",
        "Disallow: /fr/crush-admin/",
        "Disallow: /fr/accounts/",
        "Disallow: /fr/api/",
        "Disallow: /fr/dashboard/",
        "Disallow: /fr/profile/",
        "",
        "# Crawl delay (be nice to our server)",
        "Crawl-delay: 1",
        "",
        "# Sitemap location",
        "Sitemap: https://crush.lu/sitemap.xml",
        "",
    ]

    return HttpResponse("\n".join(lines), content_type="text/plain")