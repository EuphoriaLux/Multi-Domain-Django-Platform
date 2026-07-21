# crush_lu/sitemaps.py
"""
Sitemap configuration for Crush.lu SEO.

This module provides dynamic sitemap generation for:
- Static pages (home, about, how-it-works, legal pages)
- Published events (with proper priority and changefreq)
"""

from datetime import timedelta

from django.contrib.sitemaps import Sitemap
from django.db.models import Q
from django.urls import reverse
from .models import MeetupEvent
from django.utils import timezone


class CrushStaticViewSitemap(Sitemap):
    """Sitemap for static pages."""

    protocol = 'https'
    changefreq = 'weekly'
    priority = 0.8
    i18n = True  # Enable i18n for multiple language versions
    alternates = True  # Include xhtml:link elements with hreflang for SEO

    def items(self):
        return [
            'crush_lu:home',
            'crush_lu:about',
            'crush_lu:how_it_works',
            'crush_lu:event_list',
            'crush_lu:crush_coach',
            'crush_lu:crush_connect_teaser',
            'crush_lu:membership',
            'crush_lu:privacy_policy',
            'crush_lu:terms_of_service',
        ]

    def location(self, item):
        return reverse(item)

    def priority(self, item):
        """Assign different priorities based on page importance."""
        priorities = {
            'crush_lu:home': 1.0,
            'crush_lu:event_list': 0.9,
            'crush_lu:about': 0.8,
            'crush_lu:how_it_works': 0.8,
            'crush_lu:crush_coach': 0.7,
            'crush_lu:membership': 0.7,
            'crush_lu:crush_connect_teaser': 0.6,
            'crush_lu:privacy_policy': 0.3,
            'crush_lu:terms_of_service': 0.3,
        }
        return priorities.get(item, 0.5)

    def changefreq(self, item):
        """Set change frequency based on content type."""
        if item == 'crush_lu:home':
            return 'daily'
        elif item == 'crush_lu:event_list':
            return 'daily'
        elif item in [
            'crush_lu:about',
            'crush_lu:how_it_works',
            'crush_lu:crush_coach',
            'crush_lu:crush_connect_teaser',
            'crush_lu:membership',
        ]:
            return 'monthly'
        else:
            return 'yearly'  # Legal pages rarely change


class CrushEventSitemap(Sitemap):
    """Sitemap for published events."""

    protocol = 'https'
    changefreq = 'weekly'
    priority = 0.7

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
        ).order_by('date_time')

    def location(self, obj):
        return reverse('crush_lu:event_detail', kwargs={'event_id': obj.id})

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
    'static': CrushStaticViewSitemap,
    'events': CrushEventSitemap,
}
