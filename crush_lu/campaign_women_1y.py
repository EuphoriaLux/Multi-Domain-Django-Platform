"""Women's 1-year campaign: "We saved you a seat".

Invites women who are not yet verified to get verified in person at a Crush
event, which makes them first in line for Crush Connect. Three moving parts:

* ``eligible_recipients()`` - who may be mailed (the single definition shared
  by the command's dry-run, the send loop and the tests).
* ``landing_destination()`` - where the CTA sends a visitor, by state.
* ``send_women_1y_email()`` - renders and sends one email.

No new tables. The send log is a ``NewsletterRecipient`` under the email leg
(``Campaign.email_newsletter``) of a ``Campaign`` with slug ``women_1y``, so the
campaign dashboard's email counts and click rates include it; its unique
(newsletter, user) constraint is what makes "never send twice" structural. Click
attribution reuses ``/c/<token>/?r=`` tracked links, which also give the
campaign dashboard its click counts.

The Campaign is left in ``draft``: ``dispatch_campaigns`` only claims
``scheduled``/``sending`` campaigns, so the Azure timer never touches it.
"""

import logging
from urllib.parse import urlencode

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models import Exists, F, OuterRef, Q
from django.template.loader import render_to_string
from django.templatetags.static import static
from django.utils import timezone, translation

from azureproject.email_utils import send_domain_email
from crush_lu.email_helpers import get_unsubscribe_url
from crush_lu.models import (
    Campaign,
    CampaignClick,
    CrushProfile,
    Newsletter,
    NewsletterRecipient,
)
from crush_lu.services.campaigns import build_tracked_url
from crush_lu.utils.i18n import build_absolute_url, get_user_preferred_language

logger = logging.getLogger(__name__)
User = get_user_model()

CAMPAIGN_SLUG = "women_1y"
CAMPAIGN_NAME = "Women 1 year: We saved you a seat"
CHANNEL = Campaign.CHANNEL_EMAIL
SUBJECT = "We saved you a seat \U0001f495"
PUBLIC_BASE = "https://crush.lu"

# Not yet verified. ``rejected`` profiles are deliberately left out: a
# rejection is a coach/admin decision, and inviting those members to "get
# verified" would contradict it.
UNVERIFIED_STATUSES = ("incomplete", "pending")

STATIC_DIR = "campaigns/women-1y"
UTM = {"utm_source": "email", "utm_medium": "email", "utm_campaign": CAMPAIGN_SLUG}


def get_campaign(create=False):
    if create:
        campaign, _ = Campaign.objects.get_or_create(
            slug=CAMPAIGN_SLUG,
            defaults={
                "name": CAMPAIGN_NAME,
                "channels": [CHANNEL],
                "audience": "segment",
                "status": "draft",
            },
        )
        return campaign
    return Campaign.objects.filter(slug=CAMPAIGN_SLUG).first()


def get_newsletter(campaign):
    """The campaign's email leg, used as the send log and for dashboard stats.

    Created already ``sent`` so no newsletter engine ever picks it up: this
    command does its own sending and only keeps the counters honest.
    """
    newsletter, _ = Newsletter.objects.get_or_create(
        campaign=campaign,
        defaults={
            "subject": SUBJECT,
            "body_html": "Rendered per recipient by send_women_1y_campaign.",
            "audience": "segment",
            "status": "sent",
        },
    )
    return newsletter


def eligible_recipients(include_sent=False):
    """Users who may receive the campaign.

    Active female members, not verified, not on a break, who consented to
    marketing email and have not unsubscribed.

    Consent has two records: ``EmailPreference.email_marketing`` (the settings
    toggle) and ``UserDataConsent.marketing_consent`` (the signup tick). Older
    toggles did not update the signup tick, so the tick only counts while it is
    newer than the last change to the member's email preferences; any later
    change to their preferences is read as them having reviewed it, which can
    only shrink the audience. ``unsubscribed_all`` vetoes both.
    """
    already_logged = NewsletterRecipient.objects.filter(
        newsletter__campaign__slug=CAMPAIGN_SLUG, user=OuterRef("pk")
    )
    qs = (
        User.objects.filter(
            is_active=True,
            crushprofile__gender="F",
            crushprofile__is_active=True,
            crushprofile__on_break_at__isnull=True,
            crushprofile__verification_status__in=UNVERIFIED_STATUSES,
        )
        .exclude(email="")
        .filter(
            Q(email_preference__email_marketing=True)
            | Q(
                data_consent__marketing_consent=True,
                data_consent__marketing_consent_date__gte=F(
                    "email_preference__updated_at"
                ),
            )
        )
        .exclude(email_preference__unsubscribed_all=True)
        # delete_crushlu_profile_only keeps the user active but bans them.
        .exclude(data_consent__crushlu_banned=True)
        .select_related("crushprofile")
        .order_by("pk")
    )
    if not include_sent:
        qs = qs.exclude(Exists(already_logged))
    return qs


def with_utm(url, content=None):
    params = dict(UTM)
    if content:
        params["utm_content"] = content
    return f"{url}?{urlencode(params)}"


def landing_destination(user, utm=None):
    """Where the CTA sends ``user``, as ``(url_name, query)``.

    Logged out -> signup. No profile, or a profile that is not complete yet ->
    onboarding (entry events require a participation-ready profile). Verified
    -> Crush Connect (the teaser fast-paths onboarded members onward).
    Otherwise the entry events, where verification happens in person.
    """
    utm = dict(utm or {})
    if not getattr(user, "is_authenticated", False):
        return "crush_lu:signup", utm
    profile = getattr(user, "crushprofile", None)
    if profile is None or profile.verification_status == "incomplete":
        return "crush_lu:onboarding_entry", utm
    if profile.verification_status == "verified":
        return "crush_lu:crush_connect_teaser", utm
    return "crush_lu:event_list", {"entry": "1", **utm}


def cdn_media_url(filename):
    """Public blob URL (via cdn.crush.lu when configured) of a campaign file.

    Files live in the ``crush-lu-media`` container under ``campaigns/women-1y/``
    so they exist the moment they are uploaded, independent of any deploy.
    ``CRUSH_MEDIA_BASE_URL`` is only defined by production settings; returns
    None elsewhere (dev, tests), where callers fall back to static files.
    """
    base = getattr(settings, "CRUSH_MEDIA_BASE_URL", None)
    return f"{base}/campaigns/women-1y/{filename}" if base else None


def poster_image_url():
    """Absolute URL of the email poster.

    Prefers the CDN blob. Fallback is the static file; production uses a
    manifest storage, so ``static()`` returns the hashed name and raises until
    ``collectstatic`` has seen the file.
    """
    cdn = cdn_media_url("email-poster-560x996.png")
    if cdn:
        return cdn
    name = f"{STATIC_DIR}/email-poster-560x996.png"
    try:
        path = static(name)
    except ValueError:
        path = f"/static/{name}"
    return f"{PUBLIC_BASE}{path}"


def build_email(user, campaign, test_mode=False, unsubscribe_url=None):
    """Render ``(subject, text, html)`` for ``user``.

    ``test_mode`` links straight to the landing page (no click tracking). With ``user=None`` (a test send to an address with no account)
    ``unsubscribe_url`` must be given, so a test mail never carries a real
    member's live unsubscribe link.
    """
    lang = "en"
    if user is not None:
        lang = get_user_preferred_language(user=user, request=None, default="en")
        unsubscribe_url = unsubscribe_url or get_unsubscribe_url(user, None)
    if not unsubscribe_url:
        raise ValueError("no unsubscribe URL; refusing to send")
    landing = build_absolute_url("crush_lu:women_1y_landing", lang=lang)
    if test_mode:
        # Tracked links would record the tester's clicks as campaign
        # engagement, so QA sends link straight to the landing page.
        poster_url = with_utm(landing, "poster")
        cta_url = with_utm(landing, "cta")
    else:
        poster_url = build_tracked_url(
            with_utm(landing, "poster"), campaign, CHANNEL, user
        )
        cta_url = build_tracked_url(with_utm(landing, "cta"), campaign, CHANNEL, user)
    context = {
        "subject": SUBJECT,
        "poster_url": poster_url,
        "cta_url": cta_url,
        "poster_image_url": poster_image_url(),
        "unsubscribe_url": unsubscribe_url,
    }
    # The campaign copy is English-only.
    with translation.override("en"):
        html = render_to_string("crush_lu/emails/women_1y.html", context)
        text = render_to_string("crush_lu/emails/women_1y.txt", context)
    return SUBJECT, text, html


def send_women_1y_email(user, campaign, to=None, test_mode=False, unsubscribe_url=None):
    """Send one email; returns the number sent (0 or 1)."""
    subject, text, html = build_email(
        user, campaign, test_mode=test_mode, unsubscribe_url=unsubscribe_url
    )
    return send_domain_email(
        subject=subject,
        message=text,
        html_message=html,
        recipient_list=[to or user.email],
        # Batch sender has no request; without a domain the config falls back
        # to the PowerUp sender.
        domain="crush.lu",
        fail_silently=False,
    )


def finalize_status(campaign):
    """Set the campaign's terminal status from the send log.

    Mirrors the shared dispatcher: ``sent`` when nothing failed, ``partial``
    for a mix, ``failed`` when nothing was delivered (including every
    recipient skipped). Only once no eligible recipient is left; until then
    the campaign stays ``draft``. A cancelled campaign is never overwritten.
    """
    campaign.refresh_from_db(fields=["status"])
    if campaign.status == "cancelled" or eligible_recipients().exists():
        return campaign.status
    rows = NewsletterRecipient.objects.filter(newsletter__campaign=campaign)
    if not rows.exists():
        return campaign.status
    sent = rows.filter(status="sent").count()
    failed = rows.filter(status="failed").count()
    status = "sent" if sent and not failed else "partial" if sent else "failed"
    # Conditional update: a Cancel pressed meanwhile must win.
    updated = (
        Campaign.objects.filter(pk=campaign.pk)
        .exclude(status="cancelled")
        .update(status=status, completed_at=timezone.now())
    )
    campaign.refresh_from_db(fields=["status"])
    return campaign.status if updated else "cancelled"


def campaign_report(campaign):
    """Send, click and verification numbers for the recipients so far."""
    if campaign is None:
        return None
    rows = NewsletterRecipient.objects.filter(newsletter__campaign=campaign)
    sent_at = dict(rows.filter(status="sent").values_list("user_id", "sent_at"))
    verified = CrushProfile.objects.filter(
        user_id__in=sent_at, verification_status="verified"
    )
    after_send = sum(
        1
        for user_id, approved_at in verified.values_list("user_id", "approved_at")
        if approved_at and sent_at[user_id] and approved_at >= sent_at[user_id]
    )
    clicks = CampaignClick.objects.filter(link__campaign=campaign)
    return {
        "sent": len(sent_at),
        "failed": rows.filter(status="failed").count(),
        "clicks": clicks.count(),
        "clicked_recipients": clicks.exclude(user=None)
        .values("user")
        .distinct()
        .count(),
        "verified_now": verified.count(),
        "verified_after_send": after_send,
    }
