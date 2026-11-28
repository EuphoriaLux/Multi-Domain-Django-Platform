# crush_lu/sitemaps.py
"""
Sitemap configuration for Crush.lu SEO.

This module provides dynamic sitemap generation for:
- Static pages (home, about, how-it-works, legal pages)
- Published events (with proper priority and changefreq)

Every page is listed once per language (/en/, /de/, /fr/), each with hreflang
alternates for all three plus x-default, matching the tags the pages carry in
their own <head> (``crush_lu/templatetags/seo_tags.py``).
"""

from django.conf import settings
from django.contrib.sitemaps import Sitemap
from django.db.models import Q
from django.urls import reverse
from .models import MeetupEvent
from django.utils import timezone


class LocalizedSitemap(Sitemap):
    """One URL per language, with hreflang alternates and x-default.

    ``x-default`` points at the default-language URL (/en/...), the same target
    the ``hreflang_tags`` template tag writes into each page. Django's own
    ``x_default = True`` is not used: it strips the language prefix and would
    announce ``/events/30/``, a redirect, while the page itself announces
    ``/en/events/30/``. Two different x-default targets for one page are
    conflicting signals, so the sitemap builds its own.
    """

    protocol = "https"
    i18n = True  # One <url> per language version
    alternates = True  # xhtml:link hreflang elements on each of them

    def _urls(self, page, protocol, domain):
        urls = super()._urls(page, protocol, domain)
        default_lang = settings.LANGUAGE_CODE
        for url_info in urls:
            default_url = next(
                (
                    alternate["location"]
                    for alternate in url_info["alternates"]
                    if alternate["lang_code"] == default_lang
                ),
                None,
            )
            if default_url:
                url_info["alternates"].append(
                    {"location": default_url, "lang_code": "x-default"}
                )
        return urls


class CrushStaticViewSitemap(LocalizedSitemap):
    """Sitemap for static pages."""

    def items(self):
        return [
            "crush_lu:home",
            "crush_lu:about",
            "crush_lu:how_it_works",
            "crush_lu:speed_dating_guide",
            "crush_lu:quiz_night_guide",
            "crush_lu:event_list",
            "crush_lu:theme_board",
            "crush_lu:crush_coach",
            "crush_lu:crush_connect_teaser",
            "crush_lu:membership",
            "crush_lu:privacy_policy",
            "crush_lu:terms_of_service",
        ]

    def location(self, item):
        return reverse(item)

    def priority(self, item):
        """Assign different priorities based on page importance."""
        priorities = {
            "crush_lu:home": 1.0,
            "crush_lu:event_list": 0.9,
            "crush_lu:about": 0.8,
            "crush_lu:how_it_works": 0.8,
            "crush_lu:crush_coach": 0.7,
            "crush_lu:membership": 0.7,
            "crush_lu:crush_connect_teaser": 0.6,
            "crush_lu:privacy_policy": 0.3,
            "crush_lu:terms_of_service": 0.3,
        }
        return priorities.get(item, 0.5)

    def changefreq(self, item):
        """Set change frequency based on content type."""
        if item == "crush_lu:home":
            return "daily"
        elif item == "crush_lu:event_list":
            return "daily"
        elif item in [
            "crush_lu:about",
            "crush_lu:how_it_works",
            "crush_lu:crush_coach",
            "crush_lu:crush_connect_teaser",
            "crush_lu:membership",
        ]:
            return "monthly"
        else:
            return "yearly"  # Legal pages rarely change


class CrushEventSitemap(LocalizedSitemap):
    """Sitemap for published events, in every language.

    Until 2026-11 only the English URL of an event was listed, so the German
    and French event pages were discovered late or not at all.
    """

    changefreq = "weekly"

    def items(self):
        """Return all published, non-cancelled, current/upcoming events."""
        now = timezone.now()
        event_filter = {
            "is_published": True,
            "is_cancelled": False,
            "is_private_invitation": False,
        }
        live_event_ids = [
            event.pk
            for event in MeetupEvent.objects.filter(
                **event_filter,
                date_time__gte=MeetupEvent.live_lookback_cutoff(now),
                date_time__lt=now,
            ).only("pk", "date_time", "duration_minutes")
            if event.end_time >= now
        ]
        return MeetupEvent.objects.filter(
            Q(date_time__gte=now) | Q(pk__in=live_event_ids),
            **event_filter,
        ).order_by("date_time")

    def location(self, obj):
        return reverse("crush_lu:event_detail", kwargs={"event_id": obj.id})

    def lastmod(self, obj):
        return obj.updated_at

    def priority(self, obj):
        """Higher priority for events happening soon."""
        days_until = (obj.date_time - timezone.now()).days
        if days_until <= 7:
            return 0.9  # Events this week
        elif days_until <= 30:
            return 0.7  # Events this month
        else:
            return 0.5  # Future events


# Dictionary for use in URL configuration
crush_sitemaps = {
    "static": CrushStaticViewSitemap,
    "events": CrushEventSitemap,
}
