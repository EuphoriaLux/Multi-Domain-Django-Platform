from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.urls import reverse
from django.http import HttpResponse
from django.views.decorators.http import require_http_methods
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
)
from .forms import EventRegistrationForm
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
    waitlisted = EventRegistration.objects.filter(
        event=event, status="waitlist"
    ).select_related("user__crushprofile").order_by("registered_at")

    if not waitlisted.exists():
        return None

    # If gender limits are not active, just promote first in line
    if not event.gender_limits_active:
        if not event.is_full:
            candidate = waitlisted.first()
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
            for candidate in waitlisted:
                cand_gender = getattr(
                    getattr(candidate.user, "crushprofile", None), "gender", None
                )
                if cand_gender in pool_codes:
                    if (
                        not event.is_full
                        and not event.is_gender_pool_full(cand_gender)
                    ):
                        candidate.status = "confirmed"
                        candidate.save()
                        return candidate

    # 2. Try any waitlisted user whose pool has room
    for candidate in waitlisted:
        if event.is_full:
            break
        cand_gender = getattr(
            getattr(candidate.user, "crushprofile", None), "gender", None
        )
        if cand_gender and event.is_gender_pool_full(cand_gender):
            continue
        candidate.status = "confirmed"
        candidate.save()
        return candidate

    return None


def _filter_private_events(events, user):
    """Filter out private invitation events unless user is invited."""
    visible = []
    for event in events:
        if event.is_private_invitation:
            if user.is_authenticated and (
                event.invited_users.filter(id=user.id).exists()
                or EventInvitation.objects.filter(
                    event=event,
                    created_user=user,
                    approval_status="approved",
                ).exists()
            ):
                visible.append(event)
        else:
            visible.append(event)
    return visible


def event_list(request):
    """List of upcoming and past events"""
    now = timezone.now()

    upcoming_events = MeetupEvent.objects.filter(
        is_published=True, is_cancelled=False, date_time__gte=now
    ).order_by("date_time")

    past_events = MeetupEvent.objects.filter(
        is_published=True, is_cancelled=False, date_time__lt=now
    ).order_by("-date_time")[:10]

    visible_upcoming = _filter_private_events(upcoming_events, request.user)
    visible_past = _filter_private_events(past_events, request.user)

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

        item_list_elements.append(
            {
                "@type": "ListItem",
                "position": position,
                "item": {
                    "@type": "Event",
                    "name": event.title or "",
                    "description": description,
                    "startDate": event.date_time.isoformat(),
                    "eventStatus": "https://schema.org/EventCancelled"
                    if event.is_cancelled
                    else "https://schema.org/EventScheduled",
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
                    },
                    "url": f"https://crush.lu{event_url}",
                },
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

    context = {
        "upcoming_event_list": visible_upcoming,
        "past_event_list": visible_past,
        "event_list_jsonld": event_list_jsonld,
    }
    return render(request, "crush_lu/event_list.html", context)


def event_detail(request, event_id):
    """Event detail page"""
    event = get_object_or_404(MeetupEvent, id=event_id, is_published=True)

    # Check if user is registered
    registration = None
    if request.user.is_authenticated:
        registration = EventRegistration.objects.filter(
            event=event, user=request.user
        ).exclude(status="cancelled").first()

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
    event_jsonld_data = {
        "@context": "https://schema.org",
        "@type": "Event",
        "name": event.title,
        "description": event.description,
        "startDate": event.date_time.isoformat(),
        "endDate": event.end_time.isoformat(),
        "eventStatus": "https://schema.org/EventCancelled"
        if event.is_cancelled
        else "https://schema.org/EventScheduled",
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
            "availability": "https://schema.org/SoldOut"
            if event.is_full
            else "https://schema.org/InStock"
            if event.is_registration_open
            else "https://schema.org/OutOfStock",
            "validFrom": event.created_at.isoformat(),
        },
        "maximumAttendeeCapacity": event.max_participants,
        "remainingAttendeeCapacity": event.spots_remaining,
        "typicalAgeRange": f"{event.min_age}-{event.max_age}",
        "image": event.image.url
        if event.image
        else "https://crush.lu/static/crush_lu/crush_social_preview.jpg",
    }
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

    context = {
        "event": event,
        "user_registration": registration,
        "user_profile": user_profile,
        "language_requirement_met": language_requirement_met,
        "event_languages_display": event.get_languages_display,
        "event_coaches": event_coaches,
        "event_jsonld": event_jsonld,
        "breadcrumb_jsonld": breadcrumb_jsonld,
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
    dtstamp = (
        timezone.now().astimezone(dt_timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    )

    if request.user.is_authenticated and hasattr(request.user, "crushprofile"):
        location = f"{event.location}, {event.address}"
    else:
        location = event.canton or "Luxembourg"

    event_url = request.build_absolute_uri(
        reverse("crush_lu:event_detail", kwargs={"event_id": event.id})
    )

    uid = f"event-{event.id}@crush.lu"
    description = _ical_escape(
        f"{event.description}\n\nRegister: {event_url}"
    )

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

    response = HttpResponse(
        ics_content, content_type="text/calendar; charset=utf-8"
    )
    filename = f"crush-event-{event.id}.ics"
    response["Content-Disposition"] = f'inline; filename="{filename}"'
    response["Cache-Control"] = "no-cache"

    return response


@crush_login_required
@ratelimit(key='user', rate='5/h', method='POST')
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
        if event.require_approved_profile:
            try:
                profile = CrushProfile.objects.get(user=request.user)
                if not profile.is_approved:
                    messages.error(
                        request,
                        _(
                            "This event requires an approved profile. Your profile is currently under review."
                        ),
                    )
                    return redirect("crush_lu:event_detail", event_id=event_id)
            except CrushProfile.DoesNotExist:
                messages.error(
                    request,
                    _("This event requires a Crush profile. Please create one to register.")
                )
                return redirect("crush_lu:create_profile")
        else:
            try:
                profile = CrushProfile.objects.get(user=request.user)
            except CrushProfile.DoesNotExist:
                profile = None

    if profile is None and (event.min_age > 18 or event.max_age < 99):
        messages.error(
            request,
            _("This event has age restrictions. Please create a profile to verify your age.")
        )
        return redirect("crush_lu:create_profile")

    # Language requirement check
    if event.languages:
        meets_req, error_msg = event.user_meets_language_requirement(request.user)
        if not meets_req:
            messages.error(request, error_msg)
            return redirect("crush_lu:event_detail", event_id=event_id)

    if EventRegistration.objects.filter(event=event, user=request.user).exclude(status='cancelled').exists():
        messages.warning(request, _("You are already registered for this event."))
        return redirect("crush_lu:event_detail", event_id=event_id)

    if not event.is_registration_accepting:
        messages.error(request, _("Registration is not available for this event."))
        return redirect("crush_lu:event_detail", event_id=event_id)

    # Age confirmation needed when user has no profile (age unverified)
    requires_age_confirmation = profile is None

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
                    messages.error(request, _("Registration is not available for this event."))
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
                    event=locked_event, user=request.user, status='cancelled'
                ).first()

                if cancelled_registration:
                    registration = cancelled_registration
                    registration.dietary_restrictions = form.cleaned_data.get('dietary_restrictions', '')
                    registration.bringing_guest = form.cleaned_data.get('bringing_guest', False)
                    registration.guest_name = form.cleaned_data.get('guest_name', '')
                else:
                    registration = form.save(commit=False)
                    registration.event = locked_event
                    registration.user = request.user

                # Determine confirmed vs waitlist using both total and gender caps
                user_gender = getattr(profile, "gender", None)
                total_full = locked_event.is_full
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
                    messages.success(request, _("Successfully registered for the event!"))

                registration.save()

            try:
                if registration.status == "confirmed":
                    send_event_registration_confirmation(registration, request)
                elif registration.status == "waitlist":
                    send_event_waitlist_notification(registration, request)
            except Exception as e:
                logger.error(f"Failed to send event registration email: {e}")

            if request.headers.get("HX-Request"):
                return render(
                    request,
                    "crush_lu/_event_registration_success.html",
                    {
                        "event": event,
                        "registration": registration,
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
        with transaction.atomic():
            locked_event = MeetupEvent.objects.select_for_update().get(id=event_id)
            registration = EventRegistration.objects.select_for_update().get(
                pk=registration.pk
            )
            registration.status = "cancelled"
            registration.save()

            messages.success(request, _("Your registration has been cancelled."))

            try:
                send_event_cancellation_confirmation(request.user, locked_event, request)
            except Exception as e:
                logger.error(f"Failed to send event cancellation email: {e}")

            # Gender-aware waitlist promotion
            promoted = _promote_from_waitlist(locked_event, request.user)
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
