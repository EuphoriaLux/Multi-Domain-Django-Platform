"""
Custom SMS page for the Crush-Admin panel.

Reuses the coach panel's event-invite mechanism — a per-recipient ``sms:``
deep link that opens the sender's own SMS app with the body prefilled, plus
an audit ``CallAttempt`` row logged over HTMX — but with a free-text message
written by the sender and an audience chosen on the page (an event's
registrations, a user segment, or a pasted list of emails/phone numbers).

Nothing is sent server-side. The page's job is to make working through a
list one recipient at a time fast and resumable: tapping "Open SMS" logs the
row as sent, the next unsent row is highlighted, and progress survives a
reload or a switch to another device because the sent state lives in
``CallAttempt`` rows tagged with the batch (``result="custom_sms"``).
"""

import base64
import logging
import re
from datetime import timedelta
from urllib.parse import quote

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ObjectDoesNotExist
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import F, Q, Value
from django.db.models.functions import Replace
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone, translation
from django.utils.formats import date_format
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy as _l
from django.views.decorators.http import require_POST

from crush_lu.models import (
    CallAttempt,
    CrushProfile,
    CustomSmsBatch,
    EventRegistration,
    MeetupEvent,
    ProfileSubmission,
)

logger = logging.getLogger(__name__)

RESULT_CUSTOM_SMS = "custom_sms"

# Placeholders a sender may use in the message. Unknown ``{tokens}`` are left
# literally in the body — the message is user-supplied text, so it is never
# passed through ``str.format`` (which would walk attributes and raise on any
# unknown key).
BASE_PLACEHOLDERS = (
    ("first_name", _l("Recipient's first name")),
    ("coach_name", _l("Your first name")),
)
EVENT_PLACEHOLDERS = (
    ("event_title", _l("Event title, in the recipient's language")),
    ("event_date", _l("Event date, formatted for the recipient")),
    ("event_url", _l("Link to the event page, in the recipient's language")),
)
_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")
# Validation scans every brace-delimited candidate, so typos such as
# ``{first-name}`` or ``{first_name.foo}`` are rejected at compose time instead
# of being delivered literally.
_BRACE_RE = re.compile(r"\{([^{}]*)\}")

SITE_HEADER = "💕 Crush.lu Coach Panel"
# A pasted number without a country code is read as a Luxembourg number —
# the same default the phone field's intl-tel-input uses (initialCountry
# "lu"). Suffix matching was rejected: ``691123456`` would also select
# ``+33691123456``.
DEFAULT_COUNTRY_CALLING_CODE = "352"
MESSAGE_MAX_LENGTH = 1000
RECENT_BATCHES_LIMIT = 10
# Send list rows per page. Every row carries the personalised body twice (the
# percent-encoded sms: link and the preview), so a thousand-recipient audience
# is rendered in windows; progress is always computed over the whole list.
PAGE_SIZE = 50
SUPPORTED_LANGUAGES = ("en", "de", "fr")


# ---------------------------------------------------------------------------
# Access
# ---------------------------------------------------------------------------


def _user_can_send(user):
    """Superusers and active Crush coaches — same gate as the email template manager."""
    if user.is_superuser:
        return True
    try:
        return bool(user.crushcoach.is_active)
    except (AttributeError, ObjectDoesNotExist):
        return False


def _deny_unless_allowed(request):
    if _user_can_send(request.user):
        return None
    return HttpResponseForbidden(
        "You must be a Crush coach or superuser to access this page."
    )


def _coach_for(user):
    try:
        coach = user.crushcoach
    except (AttributeError, ObjectDoesNotExist):
        return None
    return coach if coach.is_active else None


def _sender_first_name(user):
    coach = _coach_for(user)
    if coach is not None and coach.user.first_name:
        return coach.user.first_name
    return user.first_name or "Crush.lu"


# ---------------------------------------------------------------------------
# Message rendering
# ---------------------------------------------------------------------------


def render_message(template, values):
    """Substitute ``{placeholder}`` tokens from ``values``; leave unknown ones as-is."""

    def _sub(match):
        key = match.group(1)
        value = values.get(key)
        if value is None:
            return match.group(0)
        return str(value)

    return _PLACEHOLDER_RE.sub(_sub, template)


def canonical_phone(phone_number):
    """``+<digits>`` form of a stored number.

    The model validator allows spaces, dashes, dots and parentheses after the
    ``+`` and form-entered numbers keep them, so matching and the ``sms:``
    link both work on the digits only.
    """
    digits = "".join(ch for ch in (phone_number or "") if ch.isdigit())
    return f"+{digits}" if digits else ""


def _phone_digits_expr():
    """DB expression: ``phone_number`` reduced to bare digits (same idiom as services/whatsapp.py)."""
    expr = F("phone_number")
    for ch in (" ", "-", "(", ")", ".", "+"):
        expr = Replace(expr, Value(ch), Value(""))
    return expr


def build_sms_uri(phone_number, body):
    """``sms:`` deep link with the body prefilled — same encoding as the coach invite page."""
    return f"sms:{canonical_phone(phone_number)}?body={quote(body, safe='')}"


def _event_values(request, event, lang):
    """Event placeholders rendered in the recipient's language (title, date, URL prefix)."""
    if event is None:
        return {}
    with translation.override(lang):
        return {
            "event_title": event.title,
            "event_date": date_format(
                timezone.localtime(event.date_time),
                format="SHORT_DATE_FORMAT",
                use_l10n=True,
            ),
            "event_url": request.build_absolute_uri(
                reverse("crush_lu:event_detail", args=[event.id])
            ),
        }


def _recipient_language(profile):
    lang = (getattr(profile, "preferred_language", "") or "en").lower()
    return lang if lang in SUPPORTED_LANGUAGES else "en"


def render_body_for(request, batch, profile, coach_name):
    """Personalised body for one recipient, in their language."""
    lang = _recipient_language(profile)
    values = {
        "first_name": profile.user.first_name or "",
        "coach_name": coach_name,
    }
    values.update(_event_values(request, batch.event, lang))
    return lang, render_message(batch.message_for_language(lang), values)


# ---------------------------------------------------------------------------
# Audience resolution
# ---------------------------------------------------------------------------


def _normalise_phone(raw):
    """Canonical ``+<digits>`` for a pasted number.

    Spaces and punctuation are stripped, a ``00`` prefix becomes ``+``, and a
    number without any country prefix (``691 000 001`` typed the Luxembourg
    way) gets ``+352`` — an exact match, never a suffix match.
    """
    compact = re.sub(r"[^\d+]", "", raw)
    if compact.startswith("00"):
        compact = "+" + compact[2:]
    if compact.startswith("+"):
        return "+" + re.sub(r"\D", "", compact[1:])
    digits = re.sub(r"\D", "", compact)
    return f"+{DEFAULT_COUNTRY_CALLING_CODE}{digits}" if digits else ""


def parse_manual_recipients(text):
    """Split a pasted list into ``(emails, phones, junk)``.

    ``phones`` are canonical ``+<digits>`` values (see ``_normalise_phone``);
    ``junk`` keeps the original lines that are neither an email nor a number.
    """
    emails, phones, junk = [], [], []
    for raw_line in (text or "").splitlines():
        line = raw_line.strip().strip(",;")
        if not line:
            continue
        if "@" in line:
            emails.append(line.lower())
            continue
        if len(re.sub(r"\D", "", line)) < 6:
            junk.append(line)
        else:
            phones.append(_normalise_phone(line))
    return emails, phones, junk


def _eligible_profiles(include_unverified_phones):
    """Profiles this page may text at all — the base every audience is filtered through.

    Same exclusions as every other outreach channel (campaigns, newsletters):
    a phone number is required (verified unless the batch opts in), members
    who deleted their profile or were banned (``UserDataConsent.crushlu_banned``)
    are out, and so are deactivated accounts and deactivated profiles
    (``CrushProfile.is_active`` — the segments dashboard's own baseline).
    """
    phone_q = Q(phone_number__isnull=False) & ~Q(phone_number="")
    if not include_unverified_phones:
        phone_q &= Q(phone_verified=True)
    return (
        CrushProfile.objects.filter(phone_q)
        .exclude(user__data_consent__crushlu_banned=True)
        .filter(user__is_active=True, is_active=True)
    )


def resolve_manual_recipients(text, include_unverified_phones):
    """Resolve a pasted list to ``(user_ids, unmatched_lines, junk_lines)``.

    Runs at compose time so only member ids are stored on the batch. A line
    that matches no eligible member is reported back verbatim — the sender
    sees what to fix, and nothing about *why* a member is ineligible leaks.
    Digits are compared on both sides: stored numbers may carry the
    spaces/dashes the validator allows.
    """
    emails, phones, junk = parse_manual_recipients(text)
    if not emails and not phones:
        return [], [], junk

    eligible = _eligible_profiles(include_unverified_phones).annotate(
        _pn_digits=_phone_digits_expr()
    )
    match_q = Q()
    for email in emails:
        match_q |= Q(user__email__iexact=email)
    if phones:
        match_q |= Q(_pn_digits__in=[p.lstrip("+") for p in phones])
    rows = list(
        eligible.filter(match_q).values_list("user_id", "user__email", "_pn_digits")
    )

    found_emails = {email.lower() for _uid, email, _digits in rows if email}
    found_digits = {digits for _uid, _email, digits in rows}
    unmatched = [e for e in emails if e not in found_emails]
    unmatched += [p for p in phones if p.lstrip("+") not in found_digits]
    user_ids = sorted({uid for uid, _email, _digits in rows})
    return user_ids, unmatched, junk


def _profiles_from_segment(segment_queryset):
    """Map any segment queryset (profiles, submissions, user-linked rows) onto CrushProfile."""
    model = getattr(segment_queryset, "model", None)
    if model is CrushProfile:
        return CrushProfile.objects.filter(pk__in=segment_queryset.values("pk"))
    if model is ProfileSubmission:
        return CrushProfile.objects.filter(pk__in=segment_queryset.values("profile_id"))
    if model is not None and any(f.name == "user" for f in model._meta.fields):
        return CrushProfile.objects.filter(
            user_id__in=segment_queryset.values("user_id")
        )
    if model is not None and model._meta.label == "auth.User":
        return CrushProfile.objects.filter(user_id__in=segment_queryset.values("pk"))
    return CrushProfile.objects.none()


def load_segment_definitions(include_counts=False):
    """The User Segments catalogue, loaded once per request.

    Without counts this is just queryset construction (no database hits);
      the 64 per-segment COUNTs are only paid by the dropdown that shows them.
    """
    from crush_lu.admin.user_segments import get_segment_definitions

    return get_segment_definitions(include_counts=include_counts)


def find_segment(segment_key, definitions=None):
    """Locate a segment by key; pass ``definitions`` to avoid re-counting every segment."""
    if not segment_key:
        return None, None
    if definitions is None:
        definitions = load_segment_definitions()
    for category in definitions.values():
        for segment in category["segments"]:
            if segment["key"] == segment_key:
                return segment, category
    return None, None


def recipient_queryset(batch, definitions=None):
    """Resolve the batch audience to a CrushProfile queryset plus warnings for the sender.

    Only profiles with a phone number are returned; unverified numbers are
    excluded unless the batch opts in. ``definitions`` is the segment
    catalogue, when the caller already has it.
    """
    warnings = []

    if batch.audience_type == CustomSmsBatch.Audience.EVENT:
        if not batch.event_id:
            warnings.append(_("This batch has no event — no recipients."))
            return CrushProfile.objects.none(), warnings
        statuses = list(batch.registration_statuses or ["confirmed"])
        user_ids = EventRegistration.objects.filter(
            event_id=batch.event_id, status__in=statuses
        ).values("user_id")
        qs = CrushProfile.objects.filter(user_id__in=user_ids)

    elif batch.audience_type == CustomSmsBatch.Audience.SEGMENT:
        segment, _category = find_segment(batch.segment_key, definitions)
        if segment is None:
            warnings.append(
                _("Segment '%(key)s' no longer exists — no recipients.")
                % {"key": batch.segment_key}
            )
            return CrushProfile.objects.none(), warnings
        qs = _profiles_from_segment(segment["queryset"])

    else:  # manual — ids were resolved at compose time
        user_ids = [
            uid for uid in (batch.manual_user_ids or []) if isinstance(uid, int)
        ]
        if not user_ids:
            return CrushProfile.objects.none(), warnings
        qs = CrushProfile.objects.filter(user_id__in=user_ids)

    qs = (
        qs.filter(pk__in=_eligible_profiles(batch.include_unverified_phones))
        .select_related("user")
        .order_by("user__first_name", "user__last_name", "pk")
    )
    return qs, warnings


def _sent_map(batch, profile_ids):
    """profile_id → first attempt_date logged for this batch."""
    rows = (
        CallAttempt.objects.filter(
            result=RESULT_CUSTOM_SMS,
            notes__startswith=batch.notes_prefix,
            profile_id__in=profile_ids,
        )
        .order_by("attempt_date")
        .values_list("profile_id", "attempt_date")
    )
    sent = {}
    for profile_id, attempt_date in rows:
        sent.setdefault(profile_id, attempt_date)
    return sent


def _build_row(request, batch, profile, coach_name, sent_at):
    lang, body = render_body_for(request, batch, profile, coach_name)
    user = profile.user
    return {
        "profile": profile,
        "row_id": f"row-{profile.pk}",
        "name": user.get_full_name() or profile.display_name,
        "first_name": user.first_name,
        "email": user.email,
        "phone": profile.phone_number,
        "phone_verified": profile.phone_verified,
        "language": lang,
        "gender": profile.gender,
        "age": profile.age,
        "body": body,
        "sms_uri": build_sms_uri(profile.phone_number, body),
        "sent_at": sent_at,
    }


def _progress(batch, definitions=None, ids=None, sent=None, current_page=None):
    """Whole-list progress plus where the next unsent recipient lives.

    ``ids`` (ordered recipient pks) and ``sent`` (pk → attempt_date) can be
    passed by a caller that already has them; ``current_page`` lets the
    progress header say whether the next unsent row is on the page being
    viewed or on another one.
    """
    if ids is None:
        qs, _warnings = recipient_queryset(batch, definitions)
        ids = list(qs.values_list("pk", flat=True))
    if sent is None:
        sent = _sent_map(batch, ids) if ids else {}
    total = len(ids)
    sent_count = len(sent)
    next_index = next((i for i, pk in enumerate(ids) if pk not in sent), None)
    next_unsent_page = (next_index // PAGE_SIZE + 1) if next_index is not None else None
    return {
        "total": total,
        "sent": sent_count,
        "remaining": total - sent_count,
        "next_unsent_pk": ids[next_index] if next_index is not None else None,
        "next_unsent_page": next_unsent_page,
        "next_on_page": (
            next_unsent_page is not None
            and current_page is not None
            and next_unsent_page == current_page
        ),
    }


def _page_number(raw):
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return 1


def _qr_data_uri(url):
    """PNG data URI of a QR code for ``url`` — lets a desktop-composed batch be opened on a phone."""
    try:
        from crush_lu.qr_utils import generate_qr_code_image

        # Keep the helper's default 4-module quiet zone — a thinner border
        # can fail to scan when page content sits next to the code.
        png = generate_qr_code_image(url, box_size=6)
    except Exception:  # qrcode missing or payload too large — the link is still shown
        logger.debug("Custom SMS: QR generation skipped", exc_info=True)
        return ""
    if not png:
        return ""
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


# ---------------------------------------------------------------------------
# Form handling
# ---------------------------------------------------------------------------


def _event_choices(selected_id=""):
    """Published events from the last 90 days onwards, plus ``selected_id``.

    Recent past events are included so post-event follow-ups work. Drafts
    are excluded: ``event_detail`` 404s on ``is_published=False``, so an
    ``{event_url}`` pointing at one would be a dead link in every SMS.
    ``selected_id`` (the event of a batch being duplicated, or a failed
    submission's choice) is always offered, however old — batches are kept
    for a year, the dropdown window is 90 days.
    """
    cutoff = timezone.now() - timedelta(days=90)
    window_q = Q(date_time__gte=cutoff, is_published=True)
    if str(selected_id).isdigit():
        window_q |= Q(pk=int(selected_id))
    return MeetupEvent.objects.filter(window_q).order_by("-date_time")


def _blank_form():
    return {
        "title": "",
        "audience_type": CustomSmsBatch.Audience.EVENT,
        "event_id": "",
        "registration_statuses": ["confirmed"],
        "segment_key": "",
        "manual_recipients": "",
        "manual_user_ids": [],
        "include_unverified_phones": False,
        "message_en": "",
        "message_de": "",
        "message_fr": "",
    }


def _form_from_batch(batch):
    return {
        "title": batch.title,
        "audience_type": batch.audience_type,
        "event_id": str(batch.event_id or ""),
        "registration_statuses": list(batch.registration_statuses or []),
        "segment_key": batch.segment_key,
        # Re-derive the pasted list from members who are still eligible: a
        # member deleted or banned since simply drops out. Profile deletion
        # keeps the Django user (and its email) with crushlu_banned=True, so
        # this must go through the same eligibility filter as the send list,
        # never through User directly.
        "manual_recipients": "\n".join(
            _eligible_profiles(batch.include_unverified_phones)
            .filter(user_id__in=batch.manual_user_ids or [])
            .order_by("user__email")
            .values_list("user__email", flat=True)
        ),
        "manual_user_ids": list(batch.manual_user_ids or []),
        "include_unverified_phones": batch.include_unverified_phones,
        "message_en": batch.message_en,
        "message_de": batch.message_de,
        "message_fr": batch.message_fr,
    }


def _form_from_post(post):
    return {
        "title": post.get("title", "").strip()[:120],
        "audience_type": post.get("audience_type", "").strip(),
        "event_id": post.get("event_id", "").strip(),
        "registration_statuses": post.getlist("registration_statuses"),
        "segment_key": post.get("segment_key", "").strip()[:64],
        "manual_recipients": post.get("manual_recipients", ""),
        "manual_user_ids": [],
        "include_unverified_phones": post.get("include_unverified_phones") == "on",
        "message_en": post.get("message_en", "").strip(),
        "message_de": post.get("message_de", "").strip(),
        "message_fr": post.get("message_fr", "").strip(),
    }


def _validate(form):
    """Return (errors, event). Errors is a list of user-facing strings."""
    errors = []
    event = None
    audience = form["audience_type"]
    if audience not in CustomSmsBatch.Audience.values:
        errors.append(_("Choose an audience."))

    if form["event_id"]:
        try:
            event = MeetupEvent.objects.get(pk=int(form["event_id"]))
        except (ValueError, MeetupEvent.DoesNotExist):
            errors.append(_("The selected event does not exist."))

    valid_statuses = {key for key, _label in CustomSmsBatch.REGISTRATION_STATUS_OPTIONS}
    form["registration_statuses"] = [
        s for s in form["registration_statuses"] if s in valid_statuses
    ]

    if audience == CustomSmsBatch.Audience.EVENT:
        if event is None:
            errors.append(_("Pick the event whose registrations you want to text."))
        if not form["registration_statuses"]:
            errors.append(_("Tick at least one registration status."))
    elif audience == CustomSmsBatch.Audience.SEGMENT:
        if not form["segment_key"]:
            errors.append(_("Pick a user segment."))
        else:
            segment, _category = find_segment(form["segment_key"])
            if segment is None:
                errors.append(_("Unknown user segment."))
    elif audience == CustomSmsBatch.Audience.MANUAL:
        user_ids, unmatched, junk = resolve_manual_recipients(
            form["manual_recipients"], form["include_unverified_phones"]
        )
        form["manual_user_ids"] = user_ids
        if junk:
            errors.append(
                _("Not an email address or phone number: %(lines)s")
                % {"lines": ", ".join(junk[:10])}
            )
        if unmatched:
            errors.append(
                _(
                    "No member with a phone number matches: %(lines)s. "
                    "Numbers without a country code are read as +%(cc)s."
                )
                % {
                    "lines": ", ".join(unmatched[:10]),
                    "cc": DEFAULT_COUNTRY_CALLING_CODE,
                }
            )
        if not user_ids and not junk and not unmatched:
            errors.append(_("Paste at least one email address or phone number."))

    if not form["message_en"]:
        errors.append(_("Write the message (English / default variant)."))
    for field in ("message_en", "message_de", "message_fr"):
        if len(form[field]) > MESSAGE_MAX_LENGTH:
            errors.append(
                _("The %(field)s message is longer than %(max)d characters.")
                % {"field": field[-2:].upper(), "max": MESSAGE_MAX_LENGTH}
            )

    allowed = {key for key, _d in BASE_PLACEHOLDERS}
    if event is not None:
        allowed |= {key for key, _d in EVENT_PLACEHOLDERS}
    used = set()
    for field in ("message_en", "message_de", "message_fr"):
        used |= set(_BRACE_RE.findall(form[field]))
    unknown = {key for key in used if key not in allowed}
    if unknown:
        if event is None and unknown & {key for key, _d in EVENT_PLACEHOLDERS}:
            errors.append(
                _("Event placeholders (%(names)s) need an event selected.")
                % {"names": ", ".join("{%s}" % k for k in sorted(unknown))}
            )
        else:
            errors.append(
                _("Unknown placeholder(s): %(names)s. They would be sent literally.")
                % {"names": ", ".join("{%s}" % k for k in sorted(unknown))}
            )
    if event is not None and not event.is_published and "event_url" in used:
        errors.append(
            _(
                "%(event)s is not published yet, so {event_url} would be a dead "
                "link. Publish the event or drop the placeholder."
            )
            % {"event": event.title}
        )
    # event_detail lets a private-invitation event be opened only by someone
    # with a non-cancelled registration, an invited_users entry or an approved
    # invitation. A segment or pasted list carries no such guarantee, and a
    # cancelled registrant has lost theirs — so the link would bounce.
    if event is not None and event.is_private_invitation and "event_url" in used:
        if audience != CustomSmsBatch.Audience.EVENT:
            errors.append(
                _(
                    "%(event)s is invitation-only: {event_url} only opens for its "
                    "registrants, so it cannot be used with this audience. Pick "
                    "the event-registrations audience or drop the placeholder."
                )
                % {"event": event.title}
            )
        elif "cancelled" in form["registration_statuses"]:
            errors.append(
                _(
                    "%(event)s is invitation-only: cancelled registrants can no "
                    "longer open {event_url}. Untick “Cancelled” or drop the "
                    "placeholder."
                )
                % {"event": event.title}
            )
    return errors, event


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------


@login_required
def custom_sms_compose(request):
    """Compose a new batch (GET shows the form + recent batches, POST creates and redirects)."""
    denied = _deny_unless_allowed(request)
    if denied:
        return denied

    errors = []
    if request.method == "POST":
        form = _form_from_post(request.POST)
        errors, event = _validate(form)
        if not errors:
            batch = CustomSmsBatch.objects.create(
                title=form["title"],
                created_by=request.user,
                audience_type=form["audience_type"],
                event=event,
                registration_statuses=(
                    form["registration_statuses"]
                    if form["audience_type"] == CustomSmsBatch.Audience.EVENT
                    else []
                ),
                segment_key=(
                    form["segment_key"]
                    if form["audience_type"] == CustomSmsBatch.Audience.SEGMENT
                    else ""
                ),
                manual_user_ids=(
                    form["manual_user_ids"]
                    if form["audience_type"] == CustomSmsBatch.Audience.MANUAL
                    else []
                ),
                include_unverified_phones=form["include_unverified_phones"],
                message_en=form["message_en"],
                message_de=form["message_de"],
                message_fr=form["message_fr"],
            )
            messages.success(
                request,
                _(
                    "Batch created. Work through the list below — every tap on “Open SMS” is logged."
                ),
            )
            return redirect("custom_sms_send", batch_id=batch.pk)
    else:
        form = _blank_form()
        source_id = request.GET.get("from", "")
        if source_id.isdigit():
            source = CustomSmsBatch.objects.filter(pk=int(source_id)).first()
            if source is not None:
                form = _form_from_batch(source)
                form["title"] = ""
        elif request.GET.get("event"):
            form["event_id"] = request.GET["event"]

    recent_batches = list(
        CustomSmsBatch.objects.select_related("event", "created_by")[
            :RECENT_BATCHES_LIMIT
        ]
    )
    definitions = None
    if any(b.audience_type == CustomSmsBatch.Audience.SEGMENT for b in recent_batches):
        definitions = load_segment_definitions()
    recent = [
        {"batch": batch, **_progress(batch, definitions)} for batch in recent_batches
    ]

    context = {
        "title": _("Custom SMS"),
        "site_header": SITE_HEADER,
        "site_title": SITE_HEADER,
        "form": form,
        "errors": errors,
        "events": _event_choices(form["event_id"]),
        "registration_status_options": CustomSmsBatch.REGISTRATION_STATUS_OPTIONS,
        "audience_choices": CustomSmsBatch.Audience.choices,
        "base_placeholders": BASE_PLACEHOLDERS,
        "event_placeholders": EVENT_PLACEHOLDERS,
        "message_max_length": MESSAGE_MAX_LENGTH,
        "coach_name": _sender_first_name(request.user),
        "recent_batches": recent,
    }
    return render(request, "admin/crush_lu/custom_sms_compose.html", context)


@login_required
def custom_sms_segment_options(request):
    """HTMX: ``<option>`` list of user segments — loaded lazily because counting them is slow."""
    denied = _deny_unless_allowed(request)
    if denied:
        return denied
    selected = request.GET.get("selected", "")
    groups = []
    for category in load_segment_definitions(include_counts=True).values():
        groups.append(
            {
                "title": category["title"],
                "icon": category.get("icon", ""),
                "segments": [
                    {
                        "key": s["key"],
                        "name": s["name"],
                        "count": s["count"],
                    }
                    for s in category["segments"]
                ],
            }
        )
    return render(
        request,
        "admin/crush_lu/partials/_custom_sms_segment_options.html",
        {"groups": groups, "selected": selected},
    )


@login_required
def custom_sms_send(request, batch_id):
    """The working list: one row per recipient with Open SMS / sent state."""
    denied = _deny_unless_allowed(request)
    if denied:
        return denied

    batch = get_object_or_404(
        CustomSmsBatch.objects.select_related("event", "created_by"), pk=batch_id
    )
    coach_name = _sender_first_name(request.user)
    definitions = (
        load_segment_definitions()
        if batch.audience_type == CustomSmsBatch.Audience.SEGMENT
        else None
    )
    qs, warnings = recipient_queryset(batch, definitions)
    # Only pks for the whole audience — progress needs all of them, the
    # rendered rows are one page.
    ids = list(qs.values_list("pk", flat=True))
    sent = _sent_map(batch, ids) if ids else {}
    page_obj = Paginator(ids, PAGE_SIZE).get_page(_page_number(request.GET.get("page")))
    page_ids = list(page_obj.object_list)
    order = {pk: i for i, pk in enumerate(page_ids)}
    profiles = sorted(qs.filter(pk__in=page_ids), key=lambda p: order[p.pk])
    progress = _progress(
        batch, definitions, ids=ids, sent=sent, current_page=page_obj.number
    )

    rows = [
        _build_row(request, batch, profile, coach_name, sent.get(profile.pk))
        for profile in profiles
    ]
    next_row = next(
        (r for r in rows if r["profile"].pk == progress["next_unsent_pk"]), None
    )
    unverified_count = (
        qs.filter(phone_verified=False).count()
        if batch.include_unverified_phones and ids
        else 0
    )

    segment_name = ""
    if definitions is not None:
        segment, _category = find_segment(batch.segment_key, definitions)
        segment_name = segment["name"] if segment else batch.segment_key

    page_url = request.build_absolute_uri(reverse("custom_sms_send", args=[batch.pk]))
    context = {
        "title": batch.display_title,
        "site_header": SITE_HEADER,
        "site_title": SITE_HEADER,
        "batch": batch,
        "rows": rows,
        "page_obj": page_obj,
        "total": progress["total"],
        "sent_count": progress["sent"],
        "remaining": progress["remaining"],
        "next_on_page": progress["next_on_page"],
        "next_unsent_page": progress["next_unsent_page"],
        "next_row_id": next_row["row_id"] if next_row else "",
        "warnings": warnings,
        "unverified_count": unverified_count,
        "segment_name": segment_name,
        "page_url": page_url,
        "qr_data_uri": _qr_data_uri(page_url),
        "registration_status_display": ", ".join(
            str(label)
            for key, label in CustomSmsBatch.REGISTRATION_STATUS_OPTIONS
            if key in (batch.registration_statuses or [])
        ),
    }
    return render(request, "admin/crush_lu/custom_sms_send.html", context)


def _row_response(request, batch, profile, definitions):
    """Re-render one recipient row plus the out-of-band progress header."""
    coach_name = _sender_first_name(request.user)
    sent = _sent_map(batch, [profile.pk])
    row = _build_row(request, batch, profile, coach_name, sent.get(profile.pk))
    progress = _progress(
        batch, definitions, current_page=_page_number(request.POST.get("page"))
    )
    return render(
        request,
        "admin/crush_lu/partials/_custom_sms_row.html",
        {"batch": batch, "row": row, "oob_progress": progress},
    )


def _batch_and_recipient(batch_id, profile_id):
    """Load the batch and the recipient — 404 unless the profile is in this batch's audience."""
    batch = get_object_or_404(
        CustomSmsBatch.objects.select_related("event"), pk=batch_id
    )
    definitions = (
        load_segment_definitions()
        if batch.audience_type == CustomSmsBatch.Audience.SEGMENT
        else None
    )
    qs, _warnings = recipient_queryset(batch, definitions)
    profile = get_object_or_404(qs, pk=profile_id)
    return batch, profile, definitions


@login_required
@require_POST
def custom_sms_log(request, batch_id, profile_id):
    """HTMX: record that the SMS for this recipient was sent (idempotent)."""
    denied = _deny_unless_allowed(request)
    if denied:
        return denied
    batch, profile, definitions = _batch_and_recipient(batch_id, profile_id)
    with transaction.atomic():
        # Serialise check-and-create per batch: a double tap, or two devices
        # working the same list, must not produce two audit rows. Locking the
        # batch row is the simplest fence — CallAttempt has no batch column
        # to put a unique constraint on. (SQLite ignores the lock; Postgres,
        # where prod runs, honours it.)
        CustomSmsBatch.objects.select_for_update().get(pk=batch.pk)
        already = CallAttempt.objects.filter(
            profile=profile,
            result=RESULT_CUSTOM_SMS,
            notes__startswith=batch.notes_prefix,
        ).exists()
        if not already:
            _lang, body = render_body_for(
                request, batch, profile, _sender_first_name(request.user)
            )
            CallAttempt.objects.create(
                profile=profile,
                result=RESULT_CUSTOM_SMS,
                coach=_coach_for(request.user),
                # Always attribute the acting account: a superuser without a
                # coach row would otherwise leave an anonymous audit row.
                logged_by=request.user,
                event=batch.event,
                notes=f"{batch.notes_prefix} {body}",
            )
            _touch(batch)
    return _row_response(request, batch, profile, definitions)


def _touch(batch):
    """Record activity on the batch — the GDPR sweep expires from this stamp."""
    CustomSmsBatch.objects.filter(pk=batch.pk).update(last_activity_at=timezone.now())


@login_required
@require_POST
def custom_sms_unlog(request, batch_id, profile_id):
    """HTMX: undo a mis-tap — removes this batch's audit rows for the recipient."""
    denied = _deny_unless_allowed(request)
    if denied:
        return denied
    batch, profile, definitions = _batch_and_recipient(batch_id, profile_id)
    with transaction.atomic():
        # Same batch-row lock as custom_sms_log, so an undo racing a log from
        # another device is ordered after it (and removes the row) rather
        # than slipping between its existence check and its insert.
        CustomSmsBatch.objects.select_for_update().get(pk=batch.pk)
        CallAttempt.objects.filter(
            profile=profile,
            result=RESULT_CUSTOM_SMS,
            notes__startswith=batch.notes_prefix,
        ).delete()
        _touch(batch)
    return _row_response(request, batch, profile, definitions)
