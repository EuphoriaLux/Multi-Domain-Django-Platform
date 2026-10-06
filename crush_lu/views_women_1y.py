"""Landing page and CTA router for the women's 1-year campaign.

``/women-1-year/`` is the language-prefixed landing page the campaign email
links to (the unprefixed email URL is redirected by LocaleMiddleware, which
keeps the query string). ``/women-1-year/go/`` is the CTA: it routes by who is
asking, see ``campaign_women_1y.landing_destination``.

Landing views and CTA hits are logged under the ``crush_lu.women_1y`` logger
(shipped to App Insights with the other app logs); email clicks are recorded
separately as ``CampaignClick`` rows by the ``/c/<token>/`` redirect.
"""

import logging
from urllib.parse import urlencode

from django.contrib.staticfiles import finders
from django.shortcuts import redirect, render
from django.templatetags.static import static
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET

from .campaign_women_1y import (
    CAMPAIGN_SLUG,
    STATIC_DIR,
    cdn_media_url,
    landing_destination,
)
from .models import MeetupEvent
from .views_events import _filter_private_events

logger = logging.getLogger("crush_lu.women_1y")

# Events a not-yet-verified member can actually join; the others require an
# already-verified or coach-assigned member.
JOINABLE_REQUIREMENTS = ("none", "completed", "profile_exists", "unverified")
MAX_EVENTS = 3


def _utm(request):
    return {k: v for k, v in request.GET.items() if k.startswith("utm_") and v}


def _static_if_present(name):
    """Static URL for ``name``, or None until the file exists / is collected."""
    if not finders.find(name):
        return None
    try:
        return static(name)
    except ValueError:  # manifest storage has not seen it yet
        return None


def _upcoming_events(user):
    now = timezone.now()
    events = MeetupEvent.objects.filter(
        is_published=True,
        is_cancelled=False,
        registration_deadline__gt=now,
        date_time__gte=MeetupEvent.live_lookback_cutoff(now),
        profile_requirement__in=JOINABLE_REQUIREMENTS,
    ).order_by("date_time")[: MAX_EVENTS * 3]
    events = [e for e in events if e.end_time >= now]
    return _filter_private_events(events, user)[:MAX_EVENTS]


@require_GET
def women_1y_landing(request):
    utm = _utm(request)
    logger.info(
        "women_1y landing view: authenticated=%s utm=%s",
        request.user.is_authenticated,
        utm or "-",
    )
    profile = getattr(request.user, "crushprofile", None)
    go_url = reverse("crush_lu:women_1y_go")
    if utm:
        go_url = f"{go_url}?{urlencode(utm)}"
    return render(
        request,
        "crush_lu/women_1y_landing.html",
        {
            "campaign_slug": CAMPAIGN_SLUG,
            "go_url": go_url,
            "is_verified": bool(profile and profile.verification_status == "verified"),
            "logged_in": request.user.is_authenticated,
            "events": _upcoming_events(request.user),
            "poster_url": static(f"{STATIC_DIR}/poster-frame.png"),
            "video_url": cdn_media_url("women-1y.mp4")
            or _static_if_present(f"{STATIC_DIR}/women-1y.mp4"),
        },
    )


@require_GET
def women_1y_go(request):
    utm = _utm(request)
    url_name, query = landing_destination(request.user, utm)
    logger.info("women_1y cta: destination=%s utm=%s", url_name, utm or "-")
    target = reverse(url_name)
    if url_name == "crush_lu:signup":
        # Signup does not honour ``next`` (a new member goes through
        # onboarding), but login does; keep it so the login tab returns here.
        query = {**query, "next": request.get_full_path()}
    return redirect(f"{target}?{urlencode(query)}" if query else target)
