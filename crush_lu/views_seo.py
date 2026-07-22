# crush_lu/views_seo.py
"""
SEO-related views for Crush.lu.

This module provides views for:
- robots.txt generation
- Structured data helpers
"""

import logging

from django.http import HttpResponse
from django.shortcuts import render
from django.template.loader import render_to_string
from django.views.decorators.cache import cache_page
from django.views.decorators.http import require_GET

logger = logging.getLogger(__name__)

# Standalone, context-free 404 used only if the branded template fails to
# render (crush_lu/404.html extends base.html, which runs the request context
# processors; if one of those throws, Django would otherwise convert the failed
# 404 render into a 500 — which is how missing routes like /favicon.ico were
# surfacing as 500s). No external CSS, no context — always renders.
_FALLBACK_404_HTML = (
    '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
    '<meta name="viewport" content="width=device-width, initial-scale=1">'
    '<meta name="robots" content="noindex, follow">'
    "<title>Page not found – Crush.lu</title><style>"
    "body{margin:0;min-height:100vh;display:flex;align-items:center;"
    "justify-content:center;background:#faf7fb;color:#2d2d2d;font-family:"
    '-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,'
    "sans-serif;text-align:center;padding:1.5rem}.code{font-size:4.5rem;"
    "font-weight:700;color:#9b59b6;margin:0}h1{font-size:1.5rem;"
    "font-style:italic;margin:.5rem 0 0}p{color:#555;margin:.75rem 0 1.75rem}"
    "a.home{display:inline-block;padding:.75rem 1.75rem;border-radius:.5rem;"
    "background:#9b59b6;color:#fff;text-decoration:none;font-weight:600}"
    '</style></head><body><div><p class="code">404</p>'
    "<h1>We couldn&#39;t find that page</h1>"
    "<p>The page you&#39;re looking for may have moved, or it never existed.</p>"
    '<a class="home" href="/">Back to home</a></div></body></html>'
)


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
        "Allow: /child-safety-standards/",
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
        "# Block login, signup, and data deletion pages",
        "Disallow: /login/",
        "Disallow: /signup/",
        "Disallow: /data-deletion/",
        "Disallow: /en/login/",
        "Disallow: /en/signup/",
        "Disallow: /en/data-deletion/",
        "Disallow: /de/login/",
        "Disallow: /de/signup/",
        "Disallow: /de/data-deletion/",
        "Disallow: /fr/login/",
        "Disallow: /fr/signup/",
        "Disallow: /fr/data-deletion/",
        "",
        "# Block language-prefixed private areas",
        "# Note: /admin/ and /crush-admin/ are language-neutral (no prefix needed)",
        "Disallow: /en/accounts/",
        "Disallow: /en/api/",
        "Disallow: /en/dashboard/",
        "Disallow: /en/profile/",
        "Disallow: /de/accounts/",
        "Disallow: /de/api/",
        "Disallow: /de/dashboard/",
        "Disallow: /de/profile/",
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


def custom_404(request, exception):
    """Crush-branded 404 page.

    Wired as ``handler404`` in ``azureproject/urls_crush.py`` only, so it is
    scoped to crush.lu — the other domains keep Django's default handler.

    The branded template extends ``crush_lu/base.html`` and therefore runs the
    request context processors. If one of those throws (a transient DB fault,
    or the broken async sync-executor seen under load), Django would convert
    the failed 404 render into a 500 — which is how missing routes like
    ``/favicon.ico`` were surfacing as 500s. Fall back to a standalone,
    context-free 404 so a not-found never becomes a server error.
    """
    try:
        return render(request, "crush_lu/404.html", status=404)
    except Exception:
        logger.exception(
            "custom_404: branded 404 render failed; serving minimal fallback"
        )
        return HttpResponse(_FALLBACK_404_HTML, status=404)


def custom_500(request):
    """Crush-branded 500 page.

    Rendered WITHOUT a request (no context processors) so a failing context
    processor — a plausible cause of the 500 — can't cascade into a second
    error. The template is therefore standalone and must not rely on request
    context. Wired as ``handler500`` in ``azureproject/urls_crush.py`` only.
    """
    html = render_to_string("crush_lu/500.html")
    return HttpResponse(html, status=500)
