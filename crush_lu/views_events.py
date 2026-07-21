from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.urls import reverse
from django.http import HttpResponse
from django.db import transaction
from datetime import timedelta
import json
import logging

logger = logging.getLogger(__name__)

from .models import (
    CrushProfile,
    MeetupEvent,
    EventRegistration,
    EventInvitation,
    EventFeedback,
)
from .models.event_polls import EventPoll
from .forms import EventRegistrationForm, EventFeedbackForm
from .decorators import crush_login_required, ratelimit
from .email_helpers import (
    send_event_registration_confirmation,
    send_event_waitlist_notification,
    send_event_cancellation_confirmation,
)


def _promote_from_waitlist(event, cancelled_user=None):
    """
    Promote the best waitlisted candidate to confirmed.

    When gender limits are active:
    1. Try to promote a waitlisted user from the same gender pool as the
       cancelled user (maintains balance).
    2. If no same-pool candidate, try any waitlisted user whose pool has room.

    When gender limits are inactive: simple FIFO.

    Must be called inside a transaction with the event locked via
    select_for_update().

    Returns the promoted EventRegistration, or None.
    """
    # NOTE: Do NOT use select_related("user__crushprofile") here — it creates
    # a LEFT OUTER JOIN, and PostgreSQL forbids FOR UPDATE on the nullable
    # side of an outer join.  Profiles are fetched in a separate query below.
    waitlisted = (
        EventRegistration.objects.select_for_update()
        .filter(event=event, status="waitlist")
        .order_by("registered_at")
    )

    if not waitlisted.exists():
        return None

    # Prefetch profiles in a separate query (no FOR UPDATE conflict). Used for
    # both gender pools and premium (coach-assigned) capacity checks.
    waitlisted_list = list(waitlisted)
    user_ids = [reg.user_id for reg in waitlisted_list]
    profiles_by_user = {
        p.user_id: p for p in CrushProfile.objects.filter(user_id__in=user_ids)
    }

    def _get_gender(reg):
        profile = profiles_by_user.get(reg.user_id)
        return profile.gender if profile else None

    def _is_premium(reg):
        # Premium = personal coach assigned. Premium members may take a
        # reserved seat (measured against total capacity); general members are
        # capped at public_capacity so the reserved block stays held back.
        profile = profiles_by_user.get(reg.user_id)
        return bool(profile and profile.assigned_coach_id)

    # If gender limits are not active, promote the first in line who still has
    # a seat under their own capacity (general → public, premium → total).
    if not event.gender_limits_active:
        for candidate in waitlisted_list:
            if not event.is_full_for(is_premium=_is_premium(candidate)):
                candidate.status = "confirmed"
                candidate.save()
                return candidate
        return None

    # Gender-aware promotion
    cancelled_gender = None
    if cancelled_user:
        cancelled_profile = getattr(cancelled_user, "crushprofile", None)
        if cancelled_profile:
            cancelled_gender = cancelled_profile.gender

    # 1. Try same-pool candidates first
    if cancelled_gender:
        pool = event.get_gender_pool(cancelled_gender)
        if pool:
            pool_codes = event.POOL_TO_CODES.get(pool, [])
            for candidate in waitlisted_list:
                cand_gender = _get_gender(candidate)
                if cand_gender in pool_codes:
                    if not event.is_full_for(
                        is_premium=_is_premium(candidate)
                    ) and not event.is_gender_pool_full(cand_gender):
                        candidate.status = "confirmed"
                        candidate.save()
                        return candidate

    # 2. Try any waitlisted user whose pool (and overall capacity) has room
    for candidate in waitlisted_list:
        cand_gender = _get_gender(candidate)
        if not cand_gender or event.is_gender_pool_full(cand_gender):
            continue
        if event.is_full_for(is_premium=_is_premium(candidate)):
            continue
        candidate.status = "confirmed"
        candidate.save()
        return candidate

    return None


def _filter_private_events(events, user):
    """Filter out private invitation events unless user is invited."""
    if not user.is_authenticated:
        return [e for e in events if not e.is_private_invitation]

    # Batch-fetch invitation data to avoid N+1 queries
    private_events = [e for e in events if e.is_private_invitation]
    if private_events:
        private_ids = [e.id for e in private_events]
        invited_event_ids = set(
            MeetupEvent.objects.filter(
                id__in=private_ids, invited_users=user
            ).values_list("id", flat=True)
        )
        approved_event_ids = set(
            EventInvitation.objects.filter(
                event_id__in=private_ids,
                created_user=user,
                approval_status="approved",
            ).values_list("event_id", flat=True)
        )
        allowed_ids = invited_event_ids | approved_event_ids
    else:
        allowed_ids = set()

    return [e for e in events if not e.is_private_invitation or e.id in allowed_ids]


def event_list(request):
    """List of upcoming and past events"""
    now = timezone.now()

    # Fetch published, non-cancelled events and split into upcoming/past
    # in Python using the model's end_time property. This avoids
    # timedelta * F() which is not supported on SQLite. The cutoff is the
    # enforced max event duration, so an in-progress event is never dropped
    # regardless of its length (see MeetupEvent.live_lookback_cutoff).
    generous_cutoff = MeetupEvent.live_lookback_cutoff(now)
    upcoming_events = list(
        MeetupEvent.objects.with_registration_counts()
        .filter(is_published=True, is_cancelled=False, date_time__gte=generous_cutoff)
        .prefetch_related("coaches__user")
        .order_by("date_time")
    )
    upcoming_events = [e for e in upcoming_events if e.end_time >= now]

    past_events = list(
        MeetupEvent.objects.with_registration_counts()
        .filter(is_published=True, is_cancelled=False, date_time__lt=now)
        .order_by("-date_time")[:50]
    )
    past_events = [e for e in past_events if e.end_time < now][:10]

    visible_upcoming = _filter_private_events(upcoming_events, request.user)
    visible_past = _filter_private_events(past_events, request.user)

    # Build attendance lookup for past events (only 'attended' status)
    attended_ids = set()
    if request.user.is_authenticated:
        attended_ids = set(
            EventRegistration.objects.filter(
                event__in=visible_past,
                user=request.user,
                status="attended",
            ).values_list("event_id", flat=True)
        )

    past_events_with_attendance = [
        (event, event.id in attended_ids) for event in visible_past
    ]

    # Build ItemList JSON-LD in Python to avoid template rendering issues
    # (escapejs produces \x27 for apostrophes, which is invalid JSON)
    has_profile = False
    if request.user.is_authenticated:
        has_profile = CrushProfile.objects.filter(user=request.user).exists()

    item_list_elements = []
    for position, event in enumerate(visible_upcoming, start=1):
        if not event.id:
            continue
        event_url = reverse("crush_lu:event_detail", args=[event.id])
        description = event.description or ""
        words = description.split()
        if len(words) > 50:
            description = " ".join(words[:50]) + " \u2026"

        if request.user.is_authenticated and has_profile:
            location_data = {
                "@type": "Place",
                "name": event.location or "",
                "address": {
                    "@type": "PostalAddress",
                    "streetAddress": event.address or "",
                    "addressLocality": event.location or "",
                    "addressCountry": "LU",
                },
            }
        else:
            canton = event.canton or "Luxembourg"
            location_data = {
                "@type": "Place",
                "name": canton,
                "address": {
                    "@type": "PostalAddress",
                    "addressLocality": canton,
                    "addressCountry": "LU",
                },
            }

        if event.is_full:
            availability = "https://schema.org/SoldOut"
        elif event.is_registration_open:
            availability = "https://schema.org/InStock"
        else:
            availability = "https://schema.org/OutOfStock"

        # Event image URL (fallback to social preview)
        if event.image:
            image_url = event.image.url
        else:
            image_url = "https://crush.lu/static/crush_lu/crush_social_preview.jpg"

        # Performer list from assigned coaches
        performers = [
            {"@type": "Person", "name": coach.user.first_name}
            for coach in event.coaches.all()
        ]

        # Description fallback for events with empty descriptions
        if not description:
            description = "Dating event in Luxembourg organized by Crush.lu"

        # Map event languages for inLanguage
        lang_map = {"en": "en", "de": "de", "fr": "fr"}
        event_languages = [
            lang_map[lang] for lang in (event.languages or []) if lang in lang_map
        ]

        event_item = {
            "@type": "SocialEvent",
            "name": event.title or "",
            "description": description,
            "startDate": event.date_time.isoformat(),
            "endDate": event.end_time.isoformat(),
            "image": image_url,
            "eventStatus": (
                "https://schema.org/EventCancelled"
                if event.is_cancelled
                else "https://schema.org/EventScheduled"
            ),
            "eventAttendanceMode": "https://schema.org/OfflineEventAttendanceMode",
            "location": location_data,
            "organizer": {
                "@type": "Organization",
                "name": "Crush.lu",
                "url": "https://crush.lu",
            },
            "offers": {
                "@type": "Offer",
                "url": f"https://crush.lu{event_url}",
                "price": format(event.registration_fee, ".2f"),
                "priceCurrency": "EUR",
                "availability": availability,
                "validFrom": event.created_at.isoformat(),
            },
            "url": f"https://crush.lu{event_url}",
            "audience": {
                "@type": "PeopleAudience",
                "suggestedMinAge": event.min_age,
                "suggestedMaxAge": event.max_age,
            },
        }
        if event_languages:
            event_item["inLanguage"] = (
                event_languages if len(event_languages) > 1 else event_languages[0]
            )

        if performers:
            event_item["performer"] = performers

        item_list_elements.append(
            {
                "@type": "ListItem",
                "position": position,
                "item": event_item,
            }
        )

    event_list_jsonld = json.dumps(
        {
            "@context": "https://schema.org",
            "@type": "ItemList",
            "name": str(_("Upcoming Dating Events in Luxembourg")),
            "description": str(
                _(
                    "Speed dating, social mixers, and singles meetups organized by Crush.lu"
                )
            ),
            "itemListElement": item_list_elements,
        },
        ensure_ascii=False,
    )

    # Active polls for the feedback banner
    active_polls = [
        p for p in EventPoll.objects.filter(is_published=True) if p.is_active
    ]

    context = {
        "upcoming_event_list": visible_upcoming,
        "past_events_with_attendance": past_events_with_attendance,
        "event_list_jsonld": event_list_jsonld,
        "active_polls": active_polls,
    }
    return render(request, "crush_lu/event_list.html", context)


@crush_login_required
def my_events(request):
    """
    Personal calendar of events the current user has registered for.

    Shows upcoming registrations (confirmed / waitlist / pending payment)
    and past attendance with mutual-match counts back into engagement.
    """
    from .models import EventConnection

    now = timezone.now()

    registrations = list(
        EventRegistration.objects.filter(user=request.user)
        .exclude(status="cancelled")
        .select_related("event")
        .order_by("event__date_time")
    )

    upcoming, past = [], []
    for reg in registrations:
        event = reg.event
        if not event or not event.is_published or event.is_cancelled:
            continue
        if event.end_time >= now:
            upcoming.append(reg)
        else:
            past.append(reg)

    past.sort(key=lambda r: r.event.date_time, reverse=True)

    # Count mutual matches per past attended event (single query, annotated)
    attended_event_ids = [r.event_id for r in past if r.status == "attended"]
    mutual_counts = {}
    if attended_event_ids:
        connections = EventConnection.objects.annotate_is_mutual().filter(
            event_id__in=attended_event_ids,
            requester=request.user,
        )
        for conn in connections:
            if conn.is_mutual_annotated:
                mutual_counts[conn.event_id] = mutual_counts.get(conn.event_id, 0) + 1

    # Event Lobby CTA per card. participant_gate/may_learn cost queries, so
    # only evaluate for attended registrations still in a live/recap phase
    # (at most one or two per user) — everything else renders no lobby CTA.
    from .services.event_lobby import (
        PHASE_CLOSED,
        event_lobby_phase,
        lobby_cta,
    )

    def _card_lobby_cta(reg):
        if reg.status != "attended":
            return None
        if event_lobby_phase(reg.event, now) == PHASE_CLOSED:
            return None
        return lobby_cta(request.user, reg.event, registration=reg, now=now)

    upcoming_with_meta = [
        {
            "registration": reg,
            "event": reg.event,
            "is_waitlist": reg.status == "waitlist",
            "is_pending_payment": reg.status == "pending",
            "can_cancel": reg.event.date_time > now
            and reg.status in ("pending", "confirmed", "waitlist"),
            "lobby_cta": _card_lobby_cta(reg),
        }
        for reg in upcoming
    ]

    past_with_meta = [
        {
            "registration": reg,
            "event": reg.event,
            "attended": reg.status == "attended",
            "no_show": reg.status == "no_show",
            "mutual_matches": mutual_counts.get(reg.event_id, 0),
            "lobby_cta": _card_lobby_cta(reg),
        }
        for reg in past
    ]

    context = {
        "upcoming_registrations": upcoming_with_meta,
        "past_registrations": past_with_meta,
    }
    return render(request, "crush_lu/my_events.html", context)


def event_detail(request, event_id):
    """Event detail page"""
    event = get_object_or_404(MeetupEvent, id=event_id, is_published=True)

    # Check if user is registered
    registration = None
    if request.user.is_authenticated:
        registration = (
            EventRegistration.objects.filter(event=event, user=request.user)
            .exclude(status="cancelled")
            .first()
        )

    # For private events, verify access
    if event.is_private_invitation and not registration:
        is_invited = event.invited_users.filter(id=request.user.id).exists()
        has_approved_invitation = EventInvitation.objects.filter(
            event=event, created_user=request.user, approval_status="approved"
        ).exists()

        if not is_invited and not has_approved_invitation:
            messages.error(request, _("This event is by invitation only."))
            return redirect("crush_lu:event_list")

    # Fetch user profile for template display logic
    user_profile = None
    if request.user.is_authenticated:
        user_profile = CrushProfile.objects.filter(user=request.user).first()

    # Language requirement check
    language_requirement_met = True
    if event.languages and request.user.is_authenticated:
        language_requirement_met, _msg = event.user_meets_language_requirement(
            request.user
        )

    event_coaches = event.coaches.filter(is_active=True).select_related("user")

    # Build JSON-LD structured data in Python to guarantee valid JSON
    # (Django's escapejs produces \x27 for apostrophes, which is invalid JSON)
    if request.user.is_authenticated:
        location_data = {
            "@type": "Place",
            "name": event.location,
            "address": {
                "@type": "PostalAddress",
                "streetAddress": event.address,
                "addressLocality": event.location,
                "addressCountry": "LU",
            },
        }
    else:
        canton = event.canton or "Luxembourg"
        location_data = {
            "@type": "Place",
            "name": canton,
            "address": {
                "@type": "PostalAddress",
                "addressLocality": canton,
                "addressRegion": canton,
                "addressCountry": "LU",
            },
        }

    # Build performer list from event coaches (first name only for privacy)
    performers = [
        {"@type": "Person", "name": coach.user.first_name}
        for coach in event_coaches
        if coach.user.first_name
    ]

    event_url = reverse("crush_lu:event_detail", args=[event.id])

    # Map event_type to schema.org Event sub-types for richer snippets
    event_type_schema = {
        "speed_dating": "SocialEvent",
        "mixer": "SocialEvent",
        "activity": "SocialEvent",
        "themed": "SocialEvent",
        "quiz_night": "SocialEvent",
    }
    schema_type = event_type_schema.get(event.event_type, "SocialEvent")

    # Map event languages to ISO codes for inLanguage
    lang_map = {"en": "en", "de": "de", "fr": "fr"}
    event_languages = [
        lang_map[lang] for lang in (event.languages or []) if lang in lang_map
    ]

    event_jsonld_data = {
        "@context": "https://schema.org",
        "@type": schema_type,
        "name": event.title,
        "description": event.description,
        "startDate": event.date_time.isoformat(),
        "endDate": event.end_time.isoformat(),
        "eventStatus": (
            "https://schema.org/EventCancelled"
            if event.is_cancelled
            else "https://schema.org/EventScheduled"
        ),
        "eventAttendanceMode": "https://schema.org/OfflineEventAttendanceMode",
        "location": location_data,
        "organizer": {
            "@type": "Organization",
            "name": "Crush.lu",
            "url": "https://crush.lu",
        },
        "offers": {
            "@type": "Offer",
            "url": f"https://crush.lu{event_url}",
            "price": format(event.registration_fee, ".2f"),
            "priceCurrency": "EUR",
            "availability": (
                "https://schema.org/SoldOut"
                if event.is_full
                else (
                    "https://schema.org/InStock"
                    if event.is_registration_open
                    else "https://schema.org/OutOfStock"
                )
            ),
            "validFrom": event.created_at.isoformat(),
        },
        "maximumAttendeeCapacity": event.max_participants,
        "remainingAttendeeCapacity": event.spots_remaining,
        "typicalAgeRange": f"{event.min_age}-{event.max_age}",
        "image": (
            event.image.url
            if event.image
            else "https://crush.lu/static/crush_lu/crush_social_preview.jpg"
        ),
        "audience": {
            "@type": "PeopleAudience",
            "suggestedMinAge": event.min_age,
            "suggestedMaxAge": event.max_age,
        },
    }
    if event_languages:
        event_jsonld_data["inLanguage"] = (
            event_languages if len(event_languages) > 1 else event_languages[0]
        )
    if performers:
        event_jsonld_data["performer"] = performers
    event_jsonld = json.dumps(event_jsonld_data, ensure_ascii=False)

    event_list_url = reverse("crush_lu:event_list")
    breadcrumb_jsonld = json.dumps(
        {
            "@context": "https://schema.org",
            "@type": "BreadcrumbList",
            "itemListElement": [
                {
                    "@type": "ListItem",
                    "position": 1,
                    "name": str(_("Home")),
                    "item": "https://crush.lu/",
                },
                {
                    "@type": "ListItem",
                    "position": 2,
                    "name": str(_("Events")),
                    "item": f"https://crush.lu{event_list_url}",
                },
                {
                    "@type": "ListItem",
                    "position": 3,
                    "name": event.title,
                    "item": f"https://crush.lu{event_url}",
                },
            ],
        },
        ensure_ascii=False,
    )

    now = timezone.now()
    is_past = event.end_time < now
    can_cancel = bool(
        registration
        and registration.status not in ("attended", "cancelled", "no_show")
        and event.date_time > now
    )

    # Premium (coach-assigned) members can claim reserved seats, so fullness is
    # evaluated against the full capacity for them and public capacity otherwise.
    user_is_premium = bool(user_profile and user_profile.assigned_coach_id)
    event_full_for_user = event.is_full_for(is_premium=user_is_premium)
    # A reserved seat is available to this premium member specifically when the
    # event is publicly full but not yet at total capacity.
    premium_reserved_seat_available = (
        user_is_premium
        and event.is_full_for(is_premium=False)
        and not event_full_for_user
    )

    # Event Lobby entry point: one per-user CTA state (or None while the
    # feature is disabled) — see services.event_lobby.lobby_cta for the
    # disclosure rules (§5.3 as amended 2026-07-18).
    from .services.event_lobby import lobby_cta

    event_lobby_cta = lobby_cta(request.user, event, registration=registration)

    context = {
        "event": event,
        "is_past": is_past,
        "can_cancel": can_cancel,
        "user_registration": registration,
        "user_profile": user_profile,
        "user_is_premium": user_is_premium,
        "event_full_for_user": event_full_for_user,
        "premium_reserved_seat_available": premium_reserved_seat_available,
        "language_requirement_met": language_requirement_met,
        "event_languages_display": event.get_languages_display,
        "event_coaches": event_coaches,
        "event_jsonld": event_jsonld,
        "breadcrumb_jsonld": breadcrumb_jsonld,
        "event_lobby_cta": event_lobby_cta,
    }
    return render(request, "crush_lu/event_detail.html", context)


def _ical_escape(text):
    """Escape text per RFC 5545 section 3.3.11."""
    return (
        text.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def _ical_fold(line):
    """Fold a content line to max 75 octets per RFC 5545 section 3.1."""
    encoded = line.encode("utf-8")
    if len(encoded) <= 75:
        return line
    parts = []
    first = True
    while encoded:
        # First chunk: 75 octets max. Continuations: 74 (leading space = 1 octet)
        limit = 75 if first else 74
        if len(encoded) <= limit:
            parts.append(encoded.decode("utf-8"))
            break
        cut = limit
        # Don't split in the middle of a multi-byte character
        while cut > 0 and (encoded[cut] & 0xC0) == 0x80:
            cut -= 1
        parts.append(encoded[:cut].decode("utf-8"))
        encoded = encoded[cut:]
        first = False
    return "\r\n ".join(parts)


def event_calendar_download(request, event_id):
    """Generate .ics calendar file for event (RFC 5545 compliant)."""
    event = get_object_or_404(MeetupEvent, id=event_id, is_published=True)

    from datetime import timezone as dt_timezone

    end_time = event.date_time + timedelta(minutes=event.duration_minutes)

    start_utc = event.date_time.astimezone(dt_timezone.utc)
    end_utc = end_time.astimezone(dt_timezone.utc)

    dtstart = start_utc.strftime("%Y%m%dT%H%M%SZ")
    dtend = end_utc.strftime("%Y%m%dT%H%M%SZ")
    dtstamp = timezone.now().astimezone(dt_timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    if request.user.is_authenticated and hasattr(request.user, "crushprofile"):
        location = f"{event.location}, {event.address}"
    else:
        location = event.canton or "Luxembourg"

    event_url = request.build_absolute_uri(
        reverse("crush_lu:event_detail", kwargs={"event_id": event.id})
    )

    uid = f"event-{event.id}@crush.lu"
    description = _ical_escape(f"{event.description}\n\nRegister: {event_url}")

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Crush.lu//Event Calendar//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{dtstamp}",
        f"DTSTART:{dtstart}",
        f"DTEND:{dtend}",
        _ical_fold(f"SUMMARY:{_ical_escape(event.title)}"),
        _ical_fold(f"DESCRIPTION:{description}"),
        _ical_fold(f"LOCATION:{_ical_escape(location)}"),
        _ical_fold(f"URL:{event_url}"),
        "STATUS:CONFIRMED",
        "SEQUENCE:0",
        "END:VEVENT",
        "END:VCALENDAR",
    ]

    ics_content = "\r\n".join(lines) + "\r\n"

    response = HttpResponse(ics_content, content_type="text/calendar; charset=utf-8")
    filename = f"crush-event-{event.id}.ics"
    response["Content-Disposition"] = f'inline; filename="{filename}"'
    response["Cache-Control"] = "no-cache"

    return response


@crush_login_required
@ratelimit(key="user", rate="5/h", method="POST")
def event_register(request, event_id):
    """Register for an event - bypasses approval for invited guests"""
    event = get_object_or_404(MeetupEvent, id=event_id)

    # FOR PRIVATE INVITATION EVENTS: Bypass normal profile approval flow
    if event.is_private_invitation:
        is_invited_existing_user = event.invited_users.filter(
            id=request.user.id
        ).exists()

        external_invitation = EventInvitation.objects.filter(
            event=event, created_user=request.user, approval_status="approved"
        ).first()

        if not is_invited_existing_user and not external_invitation:
            messages.error(
                request, _("You do not have an approved invitation for this event.")
            )
            return redirect("crush_lu:event_detail", event_id=event_id)

        if is_invited_existing_user:
            try:
                profile = CrushProfile.objects.get(user=request.user)
            except CrushProfile.DoesNotExist:
                messages.warning(
                    request,
                    _(
                        "Please complete your profile before registering for events. "
                        "This is required for all users, even with invitations."
                    ),
                )
                return redirect("crush_lu:create_profile")
        else:
            try:
                profile = CrushProfile.objects.get(user=request.user)
            except CrushProfile.DoesNotExist:
                logger.error(
                    f"Security issue: External guest {request.user.email} trying to register "
                    f"without profile. Invitation ID: {external_invitation.id if external_invitation else 'None'}"
                )
                messages.error(
                    request,
                    _(
                        "Your profile is missing. Please contact support for assistance."
                    ),
                )
                return redirect("crush_lu:event_detail", event_id=event_id)
    else:
        if event.profile_requirement == "completed":
            # Entry event: open to anyone with a COMPLETED profile (built +
            # phone verified), whether or not they are verified yet. This is
            # where unverified users get verified in person by a coach.
            # Allowlist on purpose — "!= incomplete" would wrongly admit
            # rejected profiles (statuses: incomplete/pending/verified/rejected).
            try:
                profile = CrushProfile.objects.get(user=request.user)
                # Already-verified members always qualify. Unverified users need
                # a completed profile (submitted + phone verified) — they get
                # verified in person at the event.
                profile_ready = profile.verification_status == "verified" or (
                    profile.verification_status == "pending" and profile.phone_verified
                )
                if not profile_ready:
                    messages.warning(
                        request,
                        _(
                            "Please complete your profile before registering. "
                            "You'll get verified in person when you come to the event."
                        ),
                    )
                    return redirect("crush_lu:create_profile")
            except CrushProfile.DoesNotExist:
                messages.error(
                    request,
                    _(
                        "This event requires a Crush profile. Please create one to register."
                    ),
                )
                return redirect("crush_lu:create_profile")
        elif event.profile_requirement == "approved":
            try:
                profile = CrushProfile.objects.get(user=request.user)
                if not profile.is_approved:
                    messages.error(
                        request,
                        _(
                            "This event is for verified members only. Get verified at an entry event or with LuxID first."
                        ),
                    )
                    return redirect("crush_lu:event_detail", event_id=event_id)
            except CrushProfile.DoesNotExist:
                messages.error(
                    request,
                    _(
                        "This event requires a Crush profile. Please create one to register."
                    ),
                )
                return redirect("crush_lu:create_profile")
        elif event.profile_requirement == "coach_assigned":
            try:
                profile = CrushProfile.objects.get(user=request.user)
                if not profile.assigned_coach_id:
                    messages.error(
                        request,
                        _(
                            "This is a premium event for members with a personal coach. "
                            "Attend an event to get your coach assigned."
                        ),
                    )
                    return redirect("crush_lu:event_detail", event_id=event_id)
            except CrushProfile.DoesNotExist:
                messages.error(
                    request,
                    _(
                        "This event requires a Crush profile. Please create one to register."
                    ),
                )
                return redirect("crush_lu:create_profile")
        elif event.profile_requirement == "unverified":
            try:
                profile = CrushProfile.objects.get(user=request.user)
                if profile.is_approved:
                    messages.error(
                        request,
                        _(
                            "This event is exclusively for members whose profile has not yet been verified by a coach."
                        ),
                    )
                    return redirect("crush_lu:event_detail", event_id=event_id)
            except CrushProfile.DoesNotExist:
                messages.error(
                    request,
                    _(
                        "This event requires a Crush profile. Please create one to register."
                    ),
                )
                return redirect("crush_lu:create_profile")
        elif event.profile_requirement == "profile_exists":
            try:
                profile = CrushProfile.objects.get(user=request.user)
                if profile.is_approved:
                    messages.error(
                        request,
                        _(
                            "This event is exclusively for members whose profile has not yet been verified by a coach. Since your profile is already approved, you are not eligible for this event."
                        ),
                    )
                    return redirect("crush_lu:event_detail", event_id=event_id)
            except CrushProfile.DoesNotExist:
                messages.error(
                    request,
                    _(
                        "This event requires a Crush profile. Please create one to register."
                    ),
                )
                return redirect("crush_lu:create_profile")
        else:
            try:
                profile = CrushProfile.objects.get(user=request.user)
            except CrushProfile.DoesNotExist:
                profile = None

    # Age verification — enforce event.min_age / event.max_age against the
    # user's date_of_birth on the profile. A checkbox self-attestation is NOT
    # sufficient for age-restricted events: we require a profile with a DOB.
    event_has_age_restriction = event.min_age > 18 or event.max_age < 99
    if event_has_age_restriction:
        if profile is None or profile.age is None:
            messages.error(
                request,
                _(
                    "This event has age restrictions. Please complete your "
                    "profile with your date of birth to verify your age."
                ),
            )
            return redirect("crush_lu:create_profile")
        if not (event.min_age <= profile.age <= event.max_age):
            messages.error(
                request,
                _(
                    "This event is restricted to ages %(min)d–%(max)d. "
                    "Your profile does not meet these age requirements."
                )
                % {"min": event.min_age, "max": event.max_age},
            )
            return redirect("crush_lu:event_detail", event_id=event_id)

    # Language requirement check
    if event.languages:
        meets_req, error_msg = event.user_meets_language_requirement(request.user)
        if not meets_req:
            messages.error(request, error_msg)
            return redirect("crush_lu:event_detail", event_id=event_id)

    if (
        EventRegistration.objects.filter(event=event, user=request.user)
        .exclude(status="cancelled")
        .exists()
    ):
        messages.warning(request, _("You are already registered for this event."))
        return redirect("crush_lu:event_detail", event_id=event_id)

    if not event.is_registration_accepting:
        # The event detail page already shows a "Registration is closed" banner,
        # so skip the redundant (and alarming, red) flash on top of it.
        return redirect("crush_lu:event_detail", event_id=event_id)

    # Self-attestation checkbox only appears for events with NO age restriction
    # AND no profile on file. Age-restricted events have already forced profile
    # creation above (see event_has_age_restriction), so the checkbox is never
    # the primary age signal for those. It remains as a legal safeguard for
    # open events where a profile isn't required.
    requires_age_confirmation = profile is None and not event_has_age_restriction

    # Gender selection needed when event uses per-gender caps and user has no gender
    requires_gender_selection = event.gender_limits_active and (
        profile is None or not profile.gender
    )

    if request.method == "POST":
        form = EventRegistrationForm(
            request.POST,
            event=event,
            requires_age_confirmation=requires_age_confirmation,
            requires_gender_selection=requires_gender_selection,
        )
        if form.is_valid():
            # Use select_for_update + atomic to prevent race condition where
            # concurrent registrations could exceed max_participants
            with transaction.atomic():
                # Lock the event row to get accurate capacity count
                locked_event = MeetupEvent.objects.select_for_update().get(id=event_id)

                # Re-check registration deadline under lock to prevent race condition
                if not locked_event.is_registration_accepting:
                    # Detail page shows the "closed" banner; skip the redundant flash.
                    return redirect("crush_lu:event_detail", event_id=event_id)

                # Defense-in-depth: re-verify age under lock against the freshly
                # locked event, in case event.min_age / max_age or the user's
                # DOB changed concurrently. Derive the restriction flag from
                # the *locked* event — the pre-lock flag may be stale if an
                # admin tightened the age bounds after the initial read.
                locked_has_age_restriction = (
                    locked_event.min_age > 18 or locked_event.max_age < 99
                )
                if locked_has_age_restriction:
                    locked_profile = CrushProfile.objects.filter(
                        user=request.user
                    ).first()
                    if (
                        locked_profile is None
                        or locked_profile.age is None
                        or not (
                            locked_event.min_age
                            <= locked_profile.age
                            <= locked_event.max_age
                        )
                    ):
                        messages.error(
                            request,
                            _("This event is restricted to ages " "%(min)d–%(max)d.")
                            % {
                                "min": locked_event.min_age,
                                "max": locked_event.max_age,
                            },
                        )
                        return redirect("crush_lu:event_detail", event_id=event_id)

                # If the user submitted a gender, persist it to their profile
                submitted_gender = form.cleaned_data.get("gender")
                if requires_gender_selection and submitted_gender:
                    if profile is None:
                        profile = CrushProfile.objects.create(
                            user=request.user, gender=submitted_gender
                        )
                    else:
                        profile.gender = submitted_gender
                        profile.save(update_fields=["gender"])

                cancelled_registration = EventRegistration.objects.filter(
                    event=locked_event, user=request.user, status="cancelled"
                ).first()

                if cancelled_registration:
                    registration = cancelled_registration
                    # A re-registration is treated as brand new (its
                    # registered_at is reset below), so drop any stale hunt-team
                    # membership tied to this row. Otherwise reconfirming it
                    # would silently reactivate the old CacheTeamMember: the
                    # active-only member_count() freed the slot on cancellation,
                    # a replacement may have taken it, and the team would then
                    # exceed team_size_max. The user re-joins a team afresh.
                    registration.cache_memberships.all().delete()
                    registration.dietary_restrictions = form.cleaned_data.get(
                        "dietary_restrictions", ""
                    )
                    registration.bringing_guest = form.cleaned_data.get(
                        "bringing_guest", False
                    )
                    registration.guest_name = form.cleaned_data.get("guest_name", "")
                    # Policy: a user who cancelled and re-registers is treated
                    # like a new registration — their original `registered_at`
                    # is discarded and they go to the back of the waitlist (if
                    # the event is full). This prevents queue-jumping via
                    # cancel-then-re-register while the event is at capacity.
                    registration.registered_at = timezone.now()
                else:
                    registration = form.save(commit=False)
                    registration.event = locked_event
                    registration.user = request.user

                # Determine confirmed vs waitlist using both total and gender caps.
                # Premium (coach-assigned) members can claim reserved seats, so
                # their fullness is measured against the full capacity.
                user_gender = getattr(profile, "gender", None)
                is_premium = bool(profile and profile.assigned_coach_id)
                total_full = locked_event.is_full_for(is_premium=is_premium)
                gender_pool_full = (
                    locked_event.gender_limits_active
                    and user_gender
                    and locked_event.is_gender_pool_full(user_gender)
                )

                if total_full or gender_pool_full:
                    registration.status = "waitlist"
                    if gender_pool_full and not total_full:
                        messages.info(
                            request,
                            _(
                                "All spots for your gender group are taken. "
                                "You have been added to the waitlist."
                            ),
                        )
                    else:
                        messages.info(
                            request,
                            _("Event is full. You have been added to the waitlist."),
                        )
                else:
                    registration.status = "confirmed"
                    messages.success(
                        request, _("Successfully registered for the event!")
                    )

                registration.save()

            try:
                if registration.status == "confirmed":
                    send_event_registration_confirmation(registration, request)
                elif registration.status == "waitlist":
                    send_event_waitlist_notification(registration, request)
            except Exception as e:
                logger.error(f"Failed to send event registration email: {e}")

            if request.headers.get("HX-Request"):
                waitlist_position = None
                if registration.status == "waitlist":
                    waitlist_position = (
                        EventRegistration.objects.filter(
                            event=event,
                            status="waitlist",
                            registered_at__lt=registration.registered_at,
                        ).count()
                        + 1
                    )
                return render(
                    request,
                    "crush_lu/_event_registration_success.html",
                    {
                        "event": event,
                        "registration": registration,
                        "waitlist_position": waitlist_position,
                    },
                )
            return redirect("crush_lu:dashboard")
        else:
            if request.headers.get("HX-Request"):
                return render(
                    request,
                    "crush_lu/_event_registration_form.html",
                    {
                        "event": event,
                        "form": form,
                        "requires_age_confirmation": requires_age_confirmation,
                        "requires_gender_selection": requires_gender_selection,
                    },
                )
    else:
        form = EventRegistrationForm(
            event=event,
            requires_age_confirmation=requires_age_confirmation,
            requires_gender_selection=requires_gender_selection,
        )

    context = {
        "event": event,
        "form": form,
        "requires_age_confirmation": requires_age_confirmation,
        "requires_gender_selection": requires_gender_selection,
    }
    return render(request, "crush_lu/event_register.html", context)


@crush_login_required
def event_cancel(request, event_id):
    """Cancel event registration"""
    event = get_object_or_404(MeetupEvent, id=event_id)
    registration = get_object_or_404(EventRegistration, event=event, user=request.user)

    if request.method == "POST":
        promoted = None

        with transaction.atomic():
            locked_event = MeetupEvent.objects.select_for_update().get(id=event_id)
            registration = EventRegistration.objects.select_for_update().get(
                pk=registration.pk
            )
            if registration.status in ("cancelled", "no_show"):
                messages.info(request, _("Your registration was already cancelled."))
                return redirect("crush_lu:dashboard")

            now = timezone.now()
            if registration.status == "attended" or locked_event.end_time <= now:
                messages.error(
                    request,
                    _(
                        "This event has already taken place. If something is wrong, "
                        "contact your coach."
                    ),
                )
                return redirect("crush_lu:event_detail", event_id=event_id)
            if locked_event.date_time <= now:
                messages.error(
                    request,
                    _(
                        "This event has already started. If you can't make it, "
                        "contact your coach."
                    ),
                )
                return redirect("crush_lu:event_detail", event_id=event_id)

            registration.status = "cancelled"
            registration.save()

            messages.success(request, _("Your registration has been cancelled."))

            # Gender-aware waitlist promotion (DB only, inside transaction)
            if locked_event.date_time > now:
                promoted = _promote_from_waitlist(locked_event, request.user)

        # Send emails OUTSIDE the transaction so they are only dispatched
        # after a successful commit and don't hold the DB lock during SMTP I/O.
        try:
            send_event_cancellation_confirmation(request.user, event, request)
        except Exception as e:
            logger.error(f"Failed to send event cancellation email: {e}")

        if promoted:
            try:
                send_event_registration_confirmation(promoted, request)
            except Exception as e:
                logger.error(f"Failed to send waitlist promotion email: {e}")

        return redirect("crush_lu:dashboard")

    context = {
        "event": event,
        "registration": registration,
    }
    return render(request, "crush_lu/event_cancel.html", context)


@crush_login_required
def event_feedback(request, event_id):
    """Capture a single feedback response from an attendee.

    Open only to users whose registration is in 'attended' status, and only
    after the event has ended. Idempotent: a returning user lands on a
    "thanks" view instead of being able to submit twice.
    """
    event = get_object_or_404(MeetupEvent, id=event_id, is_published=True)
    now = timezone.now()

    if event.end_time > now:
        messages.info(request, _("Feedback opens once the event has ended."))
        return redirect("crush_lu:event_detail", event_id=event.id)

    registration = (
        EventRegistration.objects.filter(event=event, user=request.user)
        .exclude(status="cancelled")
        .first()
    )
    if not registration or registration.status != "attended":
        messages.error(
            request,
            _("Only attendees can leave feedback for this event."),
        )
        return redirect("crush_lu:event_detail", event_id=event.id)

    existing = EventFeedback.objects.filter(event=event, user=request.user).first()
    if existing and request.method != "POST":
        return render(
            request,
            "crush_lu/event_feedback.html",
            {"event": event, "submitted": True, "feedback": existing, "form": None},
        )

    if request.method == "POST":
        form = EventFeedbackForm(request.POST, instance=existing)
        if form.is_valid():
            feedback = form.save(commit=False)
            feedback.event = event
            feedback.user = request.user
            feedback.save()
            messages.success(request, _("Thanks for the feedback!"))
            return redirect("crush_lu:event_feedback", event_id=event.id)
    else:
        form = EventFeedbackForm()

    return render(
        request,
        "crush_lu/event_feedback.html",
        {"event": event, "submitted": False, "feedback": None, "form": form},
    )
