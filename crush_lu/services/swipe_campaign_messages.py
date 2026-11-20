"""
Message sequence of a past-event swipe campaign (December 2026).

Driven by the hourly ``SwipeCampaignMessages`` timer (Azure Function
``hybrid-maintenance``) through ``POST /api/admin/swipe-campaign/dispatch/``,
or by hand with ``manage.py send_swipe_campaign_messages``. Each call sends a
bounded batch inside a wall-clock budget, so it fits the 120s request limit;
the next call continues where this one stopped.

Steps (times on the ``SwipeCampaign`` row):

=============  ===============  ================  ===============================
step           when             channels          who
=============  ===============  ================  ===============================
start          ``starts_at``    email + push      the whole audience
reminder_1     ``reminder_1_at`` push             has not swiped since the start
reminder_2     ``reminder_2_at`` push             still has profiles left
last_call      ``last_call_at`` email             still has profiles left
=============  ===============  ================  ===============================

Audience (decision 2026-11-20, Tom): every verified member who attended an
event that ended before the campaign start — email goes to all of them, not
only marketing opt-ins. They still never get one when they unsubscribed from
all email or turned off event emails (``can_send_email(.., "event_reminders")``,
the same gate as the event recap), when they are on a break, opted out of
past-event decks, deleted or banned.

``SwipeCampaignMessage`` is both the log and the idempotency key: a row is
claimed before the send, so a repeated or overlapping call never sends twice.
"""

import logging
import time

from django.contrib.auth.models import User
from django.db import IntegrityError
from django.template.loader import render_to_string
from django.utils import timezone, translation
from django.utils.formats import date_format
from django.utils.translation import gettext as _

from crush_lu.models.event_swipes import (
    EventSwipe,
    PastEventDeckOptOut,
    SwipeCampaignMessage as Msg,
)
from crush_lu.models.events import EventRegistration
from crush_lu.services import event_swipes

logger = logging.getLogger(__name__)

DEFAULT_LIMIT = 100
DEFAULT_BUDGET_SECONDS = 75

# (step, campaign field holding its time, channels, who)
STEPS = (
    (Msg.STEP_START, "starts_at", (Msg.CHANNEL_EMAIL, Msg.CHANNEL_PUSH), "all"),
    (Msg.STEP_REMINDER_1, "reminder_1_at", (Msg.CHANNEL_PUSH,), "not_swiped"),
    (Msg.STEP_REMINDER_2, "reminder_2_at", (Msg.CHANNEL_PUSH,), "profiles_left"),
    (Msg.STEP_LAST_CALL, "last_call_at", (Msg.CHANNEL_EMAIL,), "profiles_left"),
)


def campaign_audience(campaign):
    """Users invited to ``campaign`` (see the module docstring)."""
    from crush_lu.models.profiles import CrushProfile

    attended = (
        event_swipes.with_event_end(EventRegistration.objects.filter(status="attended"))
        .filter(_event_end__lt=campaign.starts_at)
        .values_list("user_id", flat=True)
    )
    return (
        User.objects.filter(
            is_active=True,
            id__in=attended,
            crushprofile__verification_status="verified",
            crushprofile__is_active=True,
            data_consent__crushlu_consent_given=True,
            data_consent__crushlu_banned=False,
        )
        .exclude(
            id__in=CrushProfile.objects.filter(on_break_at__isnull=False).values(
                "user_id"
            )
        )
        .exclude(id__in=PastEventDeckOptOut.objects.values("user_id"))
        .order_by("id")
    )


def _latest_event(user, campaign):
    reg = (
        event_swipes.with_event_end(
            EventRegistration.objects.filter(user=user, status="attended")
        )
        .filter(_event_end__lt=campaign.starts_at)
        .select_related("event")
        .order_by("-_event_end")
        .first()
    )
    return reg.event if reg else None


def _wants_step(user, campaign, who) -> bool:
    if who == "not_swiped":
        return not EventSwipe.objects.filter(
            swiper=user, created_at__gte=campaign.starts_at
        ).exists()
    if who == "profiles_left":
        return event_swipes.profiles_left(user, campaign)
    return True


def _hub_url(user, request=None):
    from crush_lu.email_helpers import get_user_language_url

    return get_user_language_url(user, "crush_lu:past_event_swipes", request)


def _end_words(campaign, lang):
    with translation.override(lang):
        return date_format(timezone.localtime(campaign.ends_at), "j F")


def send_campaign_email(user, campaign, step, request=None) -> bool:
    """One campaign email. False when the member's email settings say no."""
    from azureproject.email_utils import html_to_plain_text, send_domain_email
    from crush_lu.email_helpers import (
        can_send_email,
        get_email_context_with_unsubscribe,
    )
    from crush_lu.utils.i18n import get_user_preferred_language

    if not user.email or not can_send_email(user, "event_reminders"):
        return False
    event = _latest_event(user, campaign)
    if event is None:
        return False
    lang = get_user_preferred_language(user=user, request=request, default="en")
    context = get_email_context_with_unsubscribe(
        user,
        request,
        first_name=user.first_name,
        event_title=event.title,
        end_date=_end_words(campaign, lang),
        hub_url=_hub_url(user, request),
        is_last_call=step == Msg.STEP_LAST_CALL,
    )
    with translation.override(lang):
        if step == Msg.STEP_LAST_CALL:
            subject = _("Last weekend to swipe your past events")
        else:
            subject = _("Remember {title}?").format(title=event.title)
        html_message = render_to_string("crush_lu/emails/swipe_campaign.html", context)
        plain_message = html_to_plain_text(html_message)
    send_domain_email(
        subject=subject,
        message=plain_message,
        html_message=html_message,
        recipient_list=[user.email],
        request=request,
        domain="crush.lu",
        fail_silently=False,
    )
    return True


def send_campaign_push(user, campaign, step) -> bool:
    """One campaign push. False when the member has no device that wants it."""
    from crush_lu.push_notifications import (
        get_user_language_url,
        send_push_notification,
        user_language_context,
    )

    event = _latest_event(user, campaign)
    if event is None:
        return False
    with user_language_context(user):
        end = date_format(timezone.localtime(campaign.ends_at), "j F")
        if step == Msg.STEP_START:
            title = _("Swipe your past events")
            body = _(
                "Until %(end)s: swipe the people from %(event)s and your other "
                "Crush.lu events. A mutual like opens your chat."
            ) % {"end": end, "event": event.title}
        elif step == Msg.STEP_REMINDER_1:
            title = _("Your weekend plan 💜")
            body = _("People from %(event)s are waiting for your swipe.") % {
                "event": event.title
            }
        else:
            title = _("Profiles left to swipe")
            body = _(
                "You still have people from your past events to swipe, until %(end)s."
            ) % {"end": end}
    result = send_push_notification(
        user=user,
        title=title,
        body=body,
        url=get_user_language_url(user, "crush_lu:past_event_swipes"),
        tag=f"swipe-campaign-{campaign.pk}-{step}",
        preference_key="event_reminders",
    )
    return bool(result and result.get("success"))


def _send(user, campaign, step, channel) -> bool:
    if channel == Msg.CHANNEL_EMAIL:
        return send_campaign_email(user, campaign, step)
    return send_campaign_push(user, campaign, step)


def dispatch(
    now=None, limit=DEFAULT_LIMIT, budget_seconds=DEFAULT_BUDGET_SECONDS, dry_run=False
):
    """Send what is due for the running campaign, at most ``limit`` messages.

    Returns ``{"campaign", "sent", "skipped", "failed", "remaining",
    "would_send"}``. With ``dry_run`` nothing is sent or logged; ``would_send``
    counts the members each due step and channel would still reach.
    """
    now = now or timezone.now()
    started = time.monotonic()
    result = {
        "campaign": None,
        "sent": 0,
        "skipped": 0,
        "failed": 0,
        "remaining": 0,
        "would_send": {},
    }
    campaign = event_swipes.active_campaign(now)
    if campaign is None:
        return result
    result["campaign"] = campaign.pk
    audience = campaign_audience(campaign)
    handled = 0

    for step, field, channels, who in STEPS:
        due_at = getattr(campaign, field)
        if due_at is None or due_at > now:
            continue
        for channel in channels:
            todo = audience.exclude(
                id__in=Msg.objects.filter(
                    campaign=campaign, step=step, channel=channel
                ).values("user_id")
            )
            if dry_run:
                result["would_send"][f"{step}/{channel}"] = todo.count()
                continue
            for user in todo.select_related("crushprofile").iterator():
                if handled >= limit or time.monotonic() - started > budget_seconds:
                    result["remaining"] += 1
                    break
                handled += 1
                try:
                    claim = Msg.objects.create(
                        campaign=campaign,
                        user=user,
                        step=step,
                        channel=channel,
                        status=Msg.STATUS_SKIPPED,
                    )
                except IntegrityError:
                    continue  # another call got there first
                try:
                    sent = _wants_step(user, campaign, who) and _send(
                        user, campaign, step, channel
                    )
                except Exception:  # noqa: BLE001 — one member never stops the batch
                    logger.exception(
                        "[swipe_campaign] %s/%s failed for user %s",
                        step,
                        channel,
                        user.pk,
                    )
                    claim.status = Msg.STATUS_FAILED
                    claim.save(update_fields=["status"])
                    result["failed"] += 1
                    continue
                if sent:
                    claim.status = Msg.STATUS_SENT
                    claim.save(update_fields=["status"])
                    result["sent"] += 1
                else:
                    result["skipped"] += 1
    return result
