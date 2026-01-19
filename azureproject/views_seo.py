# azureproject/views_seo.py
"""
SEO-related views for all domains (VinsDelux, Entreprinder, Power-Up, Tableau).

This module provides domain-specific robots.txt generation.
Crush.lu has its own views_seo.py in the crush_lu app.
"""

from django.http import HttpResponse
from django.views.decorators.cache import cache_page
from django.views.decorators.http import require_GET


@require_GET
@cache_page(60 * 60 * 24)  # Cache for 24 hours
def robots_txt_vinsdelux(request):
    """
    Generate robots.txt for VinsDelux wine e-commerce platform.

    Allows crawling of public wine catalog and vineyard information
    while blocking admin, checkout, and user account areas.
    """
    lines = [
        "# Robots.txt for VinsDelux",
        "# https://vinsdelux.com/robots.txt",
        "",
        "User-agent: *",
        "",
        "# Allow public pages",
        "Allow: /",
        "Allow: /wines/",
        "Allow: /vineyards/",
        "Allow: /producers/",
        "Allow: /journey/",
        "Allow: /about/",
        "Allow: /contact/",
        "",
        "# Block admin and private areas",
        "Disallow: /admin/",
        "Disallow: /vinsdelux-admin/",
        "Disallow: /accounts/",
        "Disallow: /api/",
        "Disallow: /cart/",
        "Disallow: /checkout/",
        "Disallow: /order/",
        "Disallow: /profile/",
        "Disallow: /media/",
        "",
        "# Block language-prefixed private areas",
        "Disallow: /en/admin/",
        "Disallow: /en/accounts/",
        "Disallow: /en/api/",
        "Disallow: /en/cart/",
        "Disallow: /en/checkout/",
        "Disallow: /de/admin/",
        "Disallow: /de/accounts/",
        "Disallow: /de/api/",
        "Disallow: /de/cart/",
        "Disallow: /de/checkout/",
        "Disallow: /fr/admin/",
        "Disallow: /fr/accounts/",
        "Disallow: /fr/api/",
        "Disallow: /fr/cart/",
        "Disallow: /fr/checkout/",
        "",
        "# Crawl delay (be nice to our server)",
        "Crawl-delay: 1",
        "",
        "# Sitemap location",
        "Sitemap: https://vinsdelux.com/sitemap.xml",
        "",
    ]

    return HttpResponse("\n".join(lines), content_type="text/plain")


@require_GET
@cache_page(60 * 60 * 24)  # Cache for 24 hours
def robots_txt_entreprinder(request):
    """
    Generate robots.txt for Entreprinder business networking platform.

    Allows crawling of public pages while blocking admin,
    API endpoints, and user account areas.
    """
    lines = [
        "# Robots.txt for Entreprinder",
        "# https://entreprinder.lu/robots.txt",
        "",
        "User-agent: *",
        "",
        "# Allow public pages",
        "Allow: /",
        "Allow: /about/",
        "Allow: /features/",
        "Allow: /pricing/",
        "Allow: /contact/",
        "",
        "# Block admin and private areas",
        "Disallow: /admin/",
        "Disallow: /powerup-admin/",
        "Disallow: /accounts/",
        "Disallow: /api/",
        "Disallow: /profile/",
        "Disallow: /dashboard/",
        "Disallow: /matching/",
        "Disallow: /finops/",
        "Disallow: /media/",
        "",
        "# Block language-prefixed private areas",
        "Disallow: /en/admin/",
        "Disallow: /en/accounts/",
        "Disallow: /en/api/",
        "Disallow: /en/profile/",
        "Disallow: /en/dashboard/",
        "Disallow: /de/admin/",
        "Disallow: /de/accounts/",
        "Disallow: /de/api/",
        "Disallow: /de/profile/",
        "Disallow: /de/dashboard/",
        "Disallow: /fr/admin/",
        "Disallow: /fr/accounts/",
        "Disallow: /fr/api/",
        "Disallow: /fr/profile/",
        "Disallow: /fr/dashboard/",
        "",
        "# Crawl delay (be nice to our server)",
        "Crawl-delay: 1",
        "",
        "# Sitemap location",
        "Sitemap: https://entreprinder.lu/sitemap.xml",
        "",
    ]

    return HttpResponse("\n".join(lines), content_type="text/plain")


@require_GET
@cache_page(60 * 60 * 24)  # Cache for 24 hours
def robots_txt_powerup(request):
    """
    Generate robots.txt for PowerUP business networking platform.

    NOTE: powerup.lu now redirects to power-up.lu corporate site.
    This view is kept for backwards compatibility during transition.
    """
    # Redirect to power-up.lu robots.txt for consistency
    return robots_txt_power_up(request)


@require_GET
@cache_page(60 * 60 * 24)  # Cache for 24 hours
def robots_txt_power_up(request):
    """
    Generate robots.txt for Power-Up corporate/investor site.

    Simple static site - allow crawling of all public pages.
    No auth, no API, no forms - just static content.
    """
    lines = [
        "# Robots.txt for Power-Up Corporate Site",
        "# https://power-up.lu/robots.txt",
        "",
        "User-agent: *",
        "",
        "# Allow all public pages",
        "Allow: /",
        "",
        "# Block health check endpoint",
        "Disallow: /healthz/",
        "",
        "# Crawl delay (be nice to our server)",
        "Crawl-delay: 1",
        "",
        "# No sitemap yet - static corporate site",
        "",
    ]

    return HttpResponse("\n".join(lines), content_type="text/plain")


@require_GET
@cache_page(60 * 60 * 24)  # Cache for 24 hours
def robots_txt_tableau(request):
    """
    Generate robots.txt for Tableau AI Art e-commerce site.

    Simple static site - allow crawling of all public pages.
    No auth, no API, no forms - just static content.
    """
    lines = [
        "# Robots.txt for Tableau AI Art",
        "# https://tableau.lu/robots.txt",
        "",
        "User-agent: *",
        "",
        "# Allow all public pages",
        "Allow: /",
        "",
        "# Block health check endpoint",
        "Disallow: /healthz/",
        "",
        "# Crawl delay (be nice to our server)",
        "Crawl-delay: 1",
        "",
        "# No sitemap yet - e-commerce coming soon",
        "",
    ]

    return HttpResponse("\n".join(lines), content_type="text/plain")


@require_GET
@cache_page(60 * 60 * 24)  # Cache for 24 hours
def robots_txt_arborist(request):
    """
    Generate robots.txt for Arborist informational site.

    Simple static site - allow crawling of all public pages.
    No auth, no API, no forms - just static content.
    """
    lines = [
        "# Robots.txt for Arborist",
        "# https://arborist.lu/robots.txt",
        "",
        "User-agent: *",
        "",
        "# Allow all public pages",
        "Allow: /",
        "",
        "# Block health check endpoint",
        "Disallow: /healthz/",
        "",
        "# Crawl delay (be nice to our server)",
        "Crawl-delay: 1",
        "",
        "# Sitemap location",
        "Sitemap: https://arborist.lu/sitemap.xml",
        "",
    ]

    return HttpResponse("\n".join(lines), content_type="text/plain")
