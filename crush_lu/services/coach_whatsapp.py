"""Coach WhatsApp inbox: members' messages to a coach's own WhatsApp number.

A coach whose number moved off the WhatsApp phone app into the Crush
Luxembourg WABA (``CrushCoach.whatsapp_phone_number_id``) reads and answers
those chats on crush.lu instead. Meta posts every number's messages to the
one webhook (``hub/views_whatsapp.py``), which stores them as
``WhatsAppInboundMessage`` rows tagged with the receiving number and calls
``on_inbound`` for each new one.

A conversation is (coach number, member's WhatsApp number). It has no table:
its handle in URLs is the pk of its first inbound row, so no phone number ever
appears in a URL. Replies are ``WhatsAppMessage`` rows sent from the coach's
number.

Meta accepts a free-form reply only within 24 hours of the member's last
message to that number (``REPLY_WINDOW``); after that only an approved
template can reach them, so the composer closes.

To write first, a coach sends the follow-up template from the screening tab
(``send_followup``): a business-initiated message, so it needs the member's
WhatsApp opt-in. Their reply opens a conversation here like any other.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from urllib.parse import urlparse

import requests
from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.db.models import Count, F, Max, Min, Q, Value
from django.db.models.functions import Replace
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import pgettext

from hub.models import WhatsAppInboundMessage, WhatsAppMessage
from hub.whatsapp_service import (
    GRAPH_BASE,
    TEXT_MAX_LENGTH,
    deliver_whatsapp_template,
    meta_settings_ok,
    queue_whatsapp_template,
    send_whatsapp_text,
)

logger = logging.getLogger(__name__)

REPLY_WINDOW = timedelta(hours=24)
NOTIFICATION_TYPE = "coach_whatsapp_message"
# One push per conversation per this many seconds: a member typing five short
# messages in a row should buzz the coach's phone once, not five times.
PUSH_THROTTLE_SECONDS = 300
# Bounds the push fan-out inside the webhook request (Meta retries a slow ack).
PUSH_BUDGET_SECONDS = 5
# The webhook stores an erased member's late message under this sender.
ERASED_SENDER = "erased"

MEDIA_TYPES = ("image", "audio", "video", "document", "sticker")
MEDIA_MAX_BYTES = 16 * 1024 * 1024
MEDIA_TIMEOUT = 15
# Media download URLs Meta hands out; the token is never sent anywhere else.
MEDIA_HOST_SUFFIXES = (".fbsbx.com", ".facebook.com", ".fbcdn.net", ".whatsapp.net")
# Shown inline; anything else (documents, SVG, …) is served as a download.
INLINE_MEDIA_PREFIXES = ("image/jpeg", "image/png", "image/webp", "audio/", "video/")
# One follow-up template per member per coach number in this period: each is a
# billed message the member didn't ask for.
FOLLOWUP_COOLDOWN = timedelta(hours=24)


class CoachWhatsAppError(Exception):
    """A reply the coach cannot send; ``str(exc)`` is safe to show them."""


class MediaUnavailable(Exception):
    """The media can't be fetched from Meta (expired, too large, misconfigured)."""


@dataclass
class Conversation:
    id: int
    from_number: str
    display_name: str
    profile: object | None
    last_at: datetime
    last_inbound_at: datetime
    last_text: str
    last_is_outbound: bool
    unread: int
    window_open: bool


@dataclass
class FollowupStatus:
    """Whether the follow-up template can go out now; ``reason`` says why not."""

    ready: bool
    reason: str = ""


@dataclass
class ThreadItem:
    outbound: bool
    at: datetime
    text: str
    message_type: str = "text"
    status: str = ""
    inbound_id: int | None = None
    media: dict | None = field(default=None)


def _digits(value: str) -> str:
    return "".join(ch for ch in (value or "") if ch.isdigit())


def has_inbox(coach) -> bool:
    return bool(coach and coach.whatsapp_phone_number_id)


def inbound_for(coach):
    """The coach's inbound rows (erased members' messages left out)."""
    if not has_inbox(coach):
        return WhatsAppInboundMessage.objects.none()
    return WhatsAppInboundMessage.objects.filter(
        business_phone_number_id=coach.whatsapp_phone_number_id
    ).exclude(from_number=ERASED_SENDER)


def unread_count(coach) -> int:
    return inbound_for(coach).filter(is_read=False).count()


def window_open(last_inbound_at, now=None) -> bool:
    if last_inbound_at is None:
        return False
    return (now or timezone.now()) - last_inbound_at < REPLY_WINDOW


def profiles_by_digits(numbers) -> dict:
    """Map digits-only phone → CrushProfile for the given WhatsApp numbers.

    Same digits-only rule as ``mark_not_on_whatsapp``: verified numbers are
    stored ``+<digits>`` but form-entered ones may carry spaces, dashes or
    parens, while Meta reports bare digits. A verified profile wins a tie.
    """
    from crush_lu.models import CrushProfile

    wanted = {_digits(n) for n in numbers if _digits(n)}
    if not wanted:
        return {}
    digits_expr = F("phone_number")
    for ch in (" ", "-", "(", ")", ".", "+"):
        digits_expr = Replace(digits_expr, Value(ch), Value(""))
    found = {}
    for profile in (
        CrushProfile.objects.annotate(_pn_digits=digits_expr)
        .filter(_pn_digits__in=wanted)
        .select_related("user")
        .order_by("phone_verified", "pk")
    ):
        # Ascending phone_verified: a verified profile is written last and wins.
        found[profile._pn_digits] = profile
    return found


def _display_name(profile, contact_name: str, from_number: str) -> str:
    member_name = profile.user.get_full_name() if profile is not None else ""
    return member_name or contact_name or f"+{from_number}"


def message_summary(message_type: str, payload: dict, text: str) -> str:
    """One line for a non-text message (a caption if it has one)."""
    if message_type == "text":
        return text
    body = (payload or {}).get(message_type) or {}
    if not isinstance(body, dict):
        body = {}
    caption = body.get("caption") or ""
    # pgettext: one-word msgids are shared with every app's catalog, and
    # crush_lu's wins, so a bare "Location" here would re-translate others.
    labels = {
        "image": pgettext("whatsapp message", "Photo"),
        "audio": (
            _("Voice message")
            if body.get("voice")
            else pgettext("whatsapp message", "Audio")
        ),
        "video": pgettext("whatsapp message", "Video"),
        "document": pgettext("whatsapp message", "Document"),
        "sticker": pgettext("whatsapp message", "Sticker"),
        "location": pgettext("whatsapp message", "Location"),
        "contacts": _("Contact card"),
    }
    if message_type == "reaction":
        return _("Reacted %(emoji)s") % {"emoji": body.get("emoji", "")}
    if message_type == "button":
        return body.get("text", "")
    label = labels.get(message_type, _("Unsupported message"))
    return f"{label}: {caption}" if caption else label


def media_info(row) -> dict | None:
    """Media details of an inbound row, or None for a non-media message."""
    if row.message_type not in MEDIA_TYPES:
        return None
    body = (row.payload or {}).get(row.message_type) or {}
    if not isinstance(body, dict) or not body.get("id"):
        return None
    mime = body.get("mime_type") or ""
    return {
        "id": body["id"],
        "mime_type": mime,
        "kind": row.message_type,
        "filename": body.get("filename") or "",
        "inline": mime.startswith(INLINE_MEDIA_PREFIXES),
    }


def _outbound_text(msg) -> str:
    """What the coach sees for a sent message: a template row stores no body."""
    if msg.text:
        return msg.text
    if msg.template_name and msg.template_name == followup_template():
        return _("Follow-up message (template)")
    return msg.template_name


def _outbound_for(coach, from_numbers=None):
    qs = WhatsAppMessage.objects.filter(
        business_phone_number_id=coach.whatsapp_phone_number_id
    ).exclude(recipient="")
    if from_numbers is not None:
        candidates = set(from_numbers) | {f"+{n}" for n in from_numbers}
        qs = qs.filter(recipient__in=candidates)
    return qs


def conversations_for(coach) -> list[Conversation]:
    """The coach's conversations, most recent activity first."""
    groups = list(
        inbound_for(coach)
        .values("from_number")
        .annotate(
            first_id=Min("pk"),
            last_id=Max("pk"),
            last_inbound_at=Max("received_at"),
            unread=Count("pk", filter=Q(is_read=False)),
        )
    )
    if not groups:
        return []
    numbers = [g["from_number"] for g in groups]
    last_rows = WhatsAppInboundMessage.objects.in_bulk([g["last_id"] for g in groups])
    contact_names = dict(
        inbound_for(coach)
        .exclude(contact_name="")
        .order_by("from_number", "pk")
        .values_list("from_number", "contact_name")
    )
    last_out = {}
    for msg in _outbound_for(coach, numbers).order_by("created_at"):
        last_out[_digits(msg.recipient)] = msg
    profiles = profiles_by_digits(numbers)
    now = timezone.now()

    conversations = []
    for g in groups:
        number = g["from_number"]
        last_in = last_rows[g["last_id"]]
        out = last_out.get(_digits(number))
        outbound_last = out is not None and out.created_at > last_in.received_at
        profile = profiles.get(_digits(number))
        conversations.append(
            Conversation(
                id=g["first_id"],
                from_number=number,
                display_name=_display_name(
                    profile, contact_names.get(number, ""), number
                ),
                profile=profile,
                last_at=out.created_at if outbound_last else last_in.received_at,
                last_inbound_at=g["last_inbound_at"],
                last_text=(
                    _outbound_text(out)
                    if outbound_last
                    else message_summary(
                        last_in.message_type, last_in.payload, last_in.text
                    )
                ),
                last_is_outbound=outbound_last,
                unread=g["unread"],
                window_open=window_open(g["last_inbound_at"], now),
            )
        )
    conversations.sort(key=lambda c: c.last_at, reverse=True)
    return conversations


def conversation(coach, conversation_id: int) -> Conversation | None:
    """The coach's conversation behind ``conversation_id``, or None.

    Any inbound row of the conversation resolves it, but only on the coach's
    own number: another coach's id is indistinguishable from a missing one.
    """
    row = inbound_for(coach).filter(pk=conversation_id).first()
    if row is None:
        return None
    for conv in conversations_for(coach):
        if conv.from_number == row.from_number:
            return conv
    return None


def thread(coach, from_number: str) -> list[ThreadItem]:
    """Inbound and outbound messages of one conversation, oldest first."""
    items = [
        ThreadItem(
            outbound=False,
            at=row.received_at,
            text=message_summary(row.message_type, row.payload, row.text),
            message_type=row.message_type,
            inbound_id=row.pk,
            media=media_info(row),
        )
        for row in inbound_for(coach).filter(from_number=from_number)
    ]
    items += [
        ThreadItem(
            outbound=True,
            at=msg.created_at,
            text=_outbound_text(msg),
            status=msg.status,
        )
        for msg in _outbound_for(coach, [from_number])
    ]
    items.sort(key=lambda item: item.at)
    return items


def mark_read(coach, from_number: str) -> int:
    return (
        inbound_for(coach)
        .filter(from_number=from_number, is_read=False)
        .update(is_read=True)
    )


def send_reply(coach, conv: Conversation, text: str, sender) -> WhatsAppMessage:
    """Send ``text`` from the coach's number into ``conv``.

    Raises ``CoachWhatsAppError`` when the reply can't go out: empty or too
    long, the 24-hour window is closed, or WhatsApp isn't configured. A Meta
    rejection still returns the FAILED row, like every other send.
    """
    text = (text or "").strip()
    if not text:
        raise CoachWhatsAppError(_("Write a message first."))
    if len(text) > TEXT_MAX_LENGTH:
        raise CoachWhatsAppError(
            _("Messages can be at most %(max)s characters.") % {"max": TEXT_MAX_LENGTH}
        )
    if not window_open(conv.last_inbound_at):
        raise CoachWhatsAppError(
            _(
                "More than 24 hours have passed since this member's last "
                "message. WhatsApp only lets you reply once they write again."
            )
        )
    if not meta_settings_ok():
        raise CoachWhatsAppError(_("WhatsApp isn't configured on the server."))
    return send_whatsapp_text(
        sender=sender,
        recipient=conv.from_number,
        text=text,
        phone_number_id=coach.whatsapp_phone_number_id,
    )


def followup_template() -> str:
    """The approved Utility template a coach writes first with ("" = off)."""
    return getattr(settings, "WHATSAPP_COACH_FOLLOWUP_TEMPLATE", "")


def _followup_sent_recently(coach, profile) -> bool:
    """A follow-up from this coach's number to this member that hasn't failed."""
    return (
        _outbound_for(coach, [_digits(profile.phone_number)])
        .filter(
            template_name=followup_template(),
            created_at__gte=timezone.now() - FOLLOWUP_COOLDOWN,
        )
        .exclude(status=WhatsAppMessage.Status.FAILED)
        .exists()
    )


def followup_status(coach, profile) -> FollowupStatus:
    """Whether ``coach`` may send ``profile`` the follow-up template now.

    A business-initiated message, so the same gate as the Connect Week request
    alert (``send_connect_week_request_whatsapp``): the member's WhatsApp
    opt-in, no master unsubscribe, and a verified number Meta hasn't flagged
    as off-WhatsApp. A coach without a Crush number gets no reason: the
    button isn't theirs.
    """
    from crush_lu.models import EmailPreference
    from crush_lu.services.whatsapp import can_send_whatsapp

    if not has_inbox(coach):
        return FollowupStatus(False)
    if not followup_template() or not meta_settings_ok():
        return FollowupStatus(
            False, _("Messages from your Crush number aren't switched on yet.")
        )
    if not can_send_whatsapp(profile):
        return FollowupStatus(
            False, _("This member's number can't receive WhatsApp messages.")
        )
    prefs = (
        EmailPreference.objects.filter(user_id=profile.user_id)
        .values("whatsapp_opt_in", "unsubscribed_all")
        .first()
    )
    if not prefs or not prefs["whatsapp_opt_in"] or prefs["unsubscribed_all"]:
        return FollowupStatus(
            False,
            _(
                "This member hasn't turned on WhatsApp messages, so you can't "
                "write to them first on WhatsApp. Call or send an SMS instead."
            ),
        )
    if _followup_sent_recently(coach, profile):
        return FollowupStatus(
            False,
            _(
                "You sent them a WhatsApp message in the last 24 hours. Their "
                "reply will show in your WhatsApp inbox."
            ),
        )
    return FollowupStatus(True)


def send_followup(coach, profile, sender) -> WhatsAppMessage:
    """Send ``profile`` the follow-up template from the coach's Crush number.

    Raises ``CoachWhatsAppError`` when ``followup_status`` says no. A Meta
    rejection still returns the FAILED row, like every other send. The
    recipient is stored as ``+<digits>`` so ``_outbound_for`` finds the row
    for the cooldown and, once the member replies, in their conversation.

    Two clicks at once (two tabs, two devices) must not both pass the
    cooldown and send two billed messages. So the check and a QUEUED
    reservation row (which the cooldown counts) happen under a lock on the
    coach row; Meta is called after commit, outside the lock. SQLite ignores
    the lock, so the tests assert this order structurally.
    """
    from crush_lu.models import CrushCoach
    from crush_lu.utils.i18n import get_user_preferred_language

    with transaction.atomic():
        CrushCoach.objects.select_for_update().get(pk=coach.pk)
        status = followup_status(coach, profile)
        if not status.ready:
            raise CoachWhatsAppError(
                status.reason or _("You don't have a Crush WhatsApp number.")
            )
        user = profile.user
        message = queue_whatsapp_template(
            sender=sender,
            recipient=f"+{_digits(profile.phone_number)}",
            template_name=followup_template(),
            language=get_user_preferred_language(user=user, default="en"),
            # Meta fails a send whose body parameter is "" (see
            # send_connect_week_request_whatsapp), so neither may be blank.
            parameters={
                "1": (user.first_name or "").strip() or profile.display_name,
                "2": (coach.user.first_name or "").strip() or "Crush.lu",
            },
            phone_number_id=coach.whatsapp_phone_number_id,
        )
    return deliver_whatsapp_template(message)


def fetch_media(media_id: str) -> tuple[bytes, str]:
    """Download one inbound media file from Meta: ``(content, mime_type)``.

    Nothing is stored; Meta keeps inbound media for about 30 days.
    """
    token = getattr(settings, "META_WHATSAPP_ACCESS_TOKEN", "")
    if not token:
        raise MediaUnavailable("WhatsApp is not configured")
    headers = {"Authorization": f"Bearer {token}"}
    try:
        meta = requests.get(
            f"{GRAPH_BASE}/{media_id}", headers=headers, timeout=MEDIA_TIMEOUT
        )
        if not meta.ok:
            raise MediaUnavailable(f"media lookup HTTP {meta.status_code}")
        info = meta.json()
        url = info.get("url") or ""
        host = urlparse(url).hostname or ""
        if urlparse(url).scheme != "https" or not host.endswith(MEDIA_HOST_SUFFIXES):
            raise MediaUnavailable("unexpected media host")
        if int(info.get("file_size") or 0) > MEDIA_MAX_BYTES:
            raise MediaUnavailable("media too large")
        resp = requests.get(url, headers=headers, timeout=MEDIA_TIMEOUT)
        if not resp.ok:
            raise MediaUnavailable(f"media download HTTP {resp.status_code}")
    except (requests.RequestException, ValueError) as exc:
        raise MediaUnavailable(exc.__class__.__name__) from exc
    if len(resp.content) > MEDIA_MAX_BYTES:
        raise MediaUnavailable("media too large")
    return resp.content, info.get("mime_type") or "application/octet-stream"


def on_inbound(row) -> None:
    """Alert the coach who owns the receiving number (called by the webhook).

    Writes or refreshes one unread bell row per conversation, and pushes to
    the coach's devices that accept member-response alerts, at most once per
    conversation every ``PUSH_THROTTLE_SECONDS``. The alert names the member
    but never carries the message text: pushes show on a lock screen.
    """
    from crush_lu.coach_notifications import notify_coach_whatsapp_message
    from crush_lu.models import CrushCoach, Notification
    from crush_lu.push_notifications import user_language_context

    if not row.business_phone_number_id or row.from_number == ERASED_SENDER:
        return
    coach = (
        CrushCoach.objects.select_related("user")
        .filter(whatsapp_phone_number_id=row.business_phone_number_id, is_active=True)
        .first()
    )
    if coach is None:
        return

    conversation_id = (
        inbound_for(coach)
        .filter(from_number=row.from_number)
        .aggregate(first=Min("pk"))["first"]
    )
    profile = profiles_by_digits([row.from_number]).get(_digits(row.from_number))
    name = (
        profile.user.first_name
        if profile is not None and profile.user.first_name
        else _display_name(profile, row.contact_name, row.from_number)
    )

    with user_language_context(coach.user):
        url = reverse(
            "crush_lu:coach_whatsapp_thread",
            args=[conversation_id],
            urlconf="azureproject.urls_crush",
        )
        title = _("New WhatsApp message")
        body = _("%(name)s wrote to you on WhatsApp.") % {"name": name}

    with transaction.atomic():
        existing = (
            Notification.objects.select_for_update()
            .filter(
                user=coach.user,
                notification_type=NOTIFICATION_TYPE,
                read_at__isnull=True,
                metadata__conversation_id=conversation_id,
            )
            .first()
        )
        if existing is not None:
            Notification.objects.filter(pk=existing.pk).update(
                created_at=timezone.now(), body=body
            )
        else:
            Notification.objects.create(
                user=coach.user,
                notification_type=NOTIFICATION_TYPE,
                title=title,
                body=body,
                link_url=url,
                metadata={"conversation_id": conversation_id},
            )

    throttle_key = f"coach-wa-push:{coach.pk}:{conversation_id}"
    if cache.add(throttle_key, 1, PUSH_THROTTLE_SECONDS):
        notify_coach_whatsapp_message(
            coach,
            title=title,
            body=body,
            url=url,
            tag=f"coach-whatsapp-{conversation_id}",
            deadline=time.monotonic() + PUSH_BUDGET_SECONDS,
        )
