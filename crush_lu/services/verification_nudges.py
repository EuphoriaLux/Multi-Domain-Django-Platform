"""Help submitted ("pending") members finish verification.

A pending member has built and submitted a profile but is not verified yet.
They get verified with LuxID or in person at an entry event (an event with
``profile_requirement="completed"``). Prod data from 2026-11-19: of the
profiles created since June, 338 were still pending, none had linked LuxID
and one had ever attended an event. This module holds the pieces that turn
that dead end into a next step:

- ``upcoming_entry_events`` for the "get verified" page and the emails,
- the eligibility queries for the day 1 / 3 / 7 reminder emails
  (``v24h`` / ``v72h`` / ``v7d``) and the one-off catch-up for members who
  submitted before the reminders existed (``vlegacy``),
- ``send_verification_reminder``, which sends one of them and records it as a
  ``ProfileReminder`` row (unique per user and type, so nothing is sent twice).

Sending is driven by ``manage.py send_verification_reminders``.
"""

import logging

from django.conf import settings
from django.core.cache import cache
from django.db.models import Exists, F, OuterRef
from django.template.loader import render_to_string
from django.utils import timezone, translation
from django.utils.translation import gettext as _

logger = logging.getLogger(__name__)

SEQUENCE_TYPES = ("v24h", "v72h", "v7d")
LEGACY_TYPE = "vlegacy"
ALL_TYPES = SEQUENCE_TYPES + (LEGACY_TYPE,)

# Same shape as PROFILE_REMINDER_TIMING: hours after submitted_for_verification_at.
# Windows are wider than a day so a missed daily run is retried, not dropped.
DEFAULT_TIMING = {
    "v24h": {"min_hours": 24, "max_hours": 120},
    "v72h": {"min_hours": 72, "max_hours": 192},
    "v7d": {"min_hours": 168, "max_hours": 336},
}

TEMPLATES = {
    "v24h": "crush_lu/emails/verification_reminder.html",
    "v72h": "crush_lu/emails/verification_reminder.html",
    "v7d": "crush_lu/emails/verification_reminder.html",
    "vlegacy": "crush_lu/emails/verification_reminder.html",
}


def reminder_timing():
    return getattr(settings, "VERIFICATION_REMINDER_TIMING", DEFAULT_TIMING)


def upcoming_entry_events(limit=2, now=None):
    """Next public events a pending member can register for and get verified at."""
    from crush_lu.models import MeetupEvent

    now = now or timezone.now()
    return list(
        MeetupEvent.objects.filter(
            is_published=True,
            is_cancelled=False,
            is_private_invitation=False,
            profile_requirement="completed",
            date_time__gt=now,
            registration_deadline__gt=now,
        ).order_by("date_time", "id")[:limit]
    )


def _base_pending_users():
    """Pending members we may contact about verification.

    Mirrors get_users_needing_reminder's contact rules (active user and
    profile, consent given, not banned) and leaves out members who already
    linked LuxID (their profile verifies on its own) or who are on the paid
    coach path (an open ProfileSubmission: a coach is handling them).
    """
    from allauth.socialaccount.models import SocialAccount

    from crush_lu.models import ProfileSubmission
    from django.contrib.auth.models import User

    open_submission = ProfileSubmission.objects.filter(
        profile__user=OuterRef("pk")
    ).exclude(status__in=("expired", "rejected"))
    # The dedicated provider is cheap to exclude here; a LuxID linked through
    # the generic openid_connect app is caught at send time (_has_luxid).
    luxid_account = SocialAccount.objects.filter(user=OuterRef("pk"), provider="luxid")
    return (
        User.objects.filter(
            is_active=True,
            crushprofile__is_active=True,
            crushprofile__verification_status="pending",
            data_consent__crushlu_consent_given=True,
        )
        .exclude(data_consent__crushlu_banned=True)
        .exclude(Exists(open_submission))
        .exclude(Exists(luxid_account))
    )


def _has_luxid(user):
    try:
        return bool(user.crushprofile.has_luxid_connected)
    except Exception:  # noqa: BLE001 - a lookup error must not send
        logger.exception("LuxID lookup failed for user pk=%s", user.pk)
        return True


def get_users_needing_verification_reminder(reminder_type, now=None):
    """Users due the given sequence reminder (v24h / v72h / v7d)."""
    from django.contrib.auth.models import User

    timing = reminder_timing()
    if reminder_type not in SEQUENCE_TYPES or reminder_type not in timing:
        logger.error("Unknown verification reminder type: %s", reminder_type)
        return User.objects.none()

    now = now or timezone.now()
    window = timing[reminder_type]
    users = (
        _base_pending_users()
        .filter(
            crushprofile__submitted_for_verification_at__gte=now
            - timezone.timedelta(hours=window["max_hours"]),
            crushprofile__submitted_for_verification_at__lte=now
            - timezone.timedelta(hours=window["min_hours"]),
        )
        .exclude(profile_reminders__reminder_type=reminder_type)
        .exclude(profile_reminders__reminder_type=LEGACY_TYPE)
    )

    # Later stages need the previous one, sent at least the planned gap ago,
    # so a member caught up late still gets them spaced out, not on three
    # consecutive days.
    prior = {"v72h": "v24h", "v7d": "v72h"}.get(reminder_type)
    if prior:
        gap = window["min_hours"] - timing[prior]["min_hours"]
        users = users.filter(
            profile_reminders__reminder_type=prior,
            profile_reminders__sent_at__lte=now - timezone.timedelta(hours=gap),
        )

    return (
        users.annotate(_submitted=F("crushprofile__submitted_for_verification_at"))
        .distinct()
        .order_by("_submitted", "pk")
    )


def get_legacy_pending_users():
    """Pending members who submitted before the reminders existed.

    They have no submitted_for_verification_at, so the day 1/3/7 sequence
    never picks them up. They get one catch-up email (``vlegacy``), sent only
    by an explicit ``send_verification_reminders --legacy`` run.
    """
    return (
        _base_pending_users()
        .filter(crushprofile__submitted_for_verification_at__isnull=True)
        .exclude(profile_reminders__reminder_type__in=ALL_TYPES)
        .distinct()
        .order_by("crushprofile__created_at", "pk")
    )


def _subject(reminder_type):
    return {
        "v24h": _("One last step: get verified on Crush.lu"),
        "v72h": _("Your Crush.lu profile is ready — it just needs verifying"),
        "v7d": _("Still not verified? Here's the quickest way"),
        "vlegacy": _("Your Crush.lu profile is waiting to be verified"),
    }[reminder_type]


def build_context(user, profile, lang):
    from crush_lu.email_helpers import get_social_links, normalize_greeting_name
    from crush_lu.models import EmailPreference
    from crush_lu.utils.i18n import build_absolute_url

    email_prefs = EmailPreference.get_or_create_for_user(user)
    with translation.override(lang):
        events = [
            {
                "title": event.title,
                "date_time": event.date_time,
                "location": getattr(event, "location", ""),
                "url": build_absolute_url(
                    "crush_lu:event_detail", lang=lang, kwargs={"event_id": event.id}
                ),
            }
            for event in upcoming_entry_events()
        ]
    return {
        "user": user,
        "greeting_name": normalize_greeting_name(user.first_name),
        "verify_url": build_absolute_url("crush_lu:profile_submitted", lang=lang),
        "entry_events": events,
        "entry_events_url": build_absolute_url("crush_lu:event_list", lang=lang)
        + "?entry=1",
        "unsubscribe_url": build_absolute_url(
            "crush_lu:email_unsubscribe",
            lang=lang,
            kwargs={"token": email_prefs.unsubscribe_token},
        ),
        "home_url": build_absolute_url("crush_lu:home", lang=lang),
        "about_url": build_absolute_url("crush_lu:about", lang=lang),
        "events_url": build_absolute_url("crush_lu:event_list", lang=lang),
        "settings_url": build_absolute_url("crush_lu:edit_profile", lang=lang)
        + "?section=account&sub=notifications",
        "social_links": get_social_links(),
    }


def send_verification_reminder(user, reminder_type):
    """Send one verification reminder email and record it. Returns True if sent."""
    from azureproject.email_utils import html_to_plain_text, send_domain_email
    from crush_lu.email_helpers import can_send_email
    from crush_lu.models import CrushProfile, ProfileReminder

    if reminder_type not in ALL_TYPES:
        logger.error("Unknown verification reminder type: %s", reminder_type)
        return False
    if not can_send_email(user, "profile_updates"):
        return False
    try:
        profile = user.crushprofile
    except CrushProfile.DoesNotExist:
        return False
    # Re-check at send time: the member may have verified since the query ran.
    if profile.verification_status != "pending" or _has_luxid(user):
        return False
    if ProfileReminder.objects.filter(user=user, reminder_type=reminder_type).exists():
        return False

    claim_key = f"verification_reminder_claim:{user.pk}:{reminder_type}"
    claim = cache.add(claim_key, "1", 300)
    if claim is False:
        return False
    claim_held = claim is True

    lang = getattr(profile, "preferred_language", "en") or "en"
    try:
        context = build_context(user, profile, lang)
        context["reminder_type"] = reminder_type
        with translation.override(lang):
            subject = _subject(reminder_type)
            html_message = render_to_string(TEMPLATES[reminder_type], context)
            plain_message = html_to_plain_text(html_message)
        sent = send_domain_email(
            subject=subject,
            message=plain_message,
            html_message=html_message,
            recipient_list=[user.email],
            request=None,
            domain="crush.lu",
            fail_silently=False,
        )
    except Exception:  # noqa: BLE001 - one failure must not stop the sweep
        logger.exception(
            "Failed to send %s verification reminder to user pk=%s",
            reminder_type,
            user.pk,
        )
        sent = 0

    if sent:
        ProfileReminder.objects.get_or_create(user=user, reminder_type=reminder_type)
        logger.info(
            "Sent %s verification reminder to user pk=%s (lang=%s)",
            reminder_type,
            user.pk,
            lang,
        )
    if claim_held:
        cache.delete(claim_key)
    return bool(sent)
