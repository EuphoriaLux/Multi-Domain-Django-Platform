from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.urls import reverse
from django.http import HttpResponse
from django.views.decorators.http import require_http_methods
from datetime import timedelta
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


def event_list(request):
    """List of upcoming events"""
    events = MeetupEvent.objects.filter(
        is_published=True, is_cancelled=False, date_time__gte=timezone.now()
    ).order_by("date_time")

    # Filter out private invitation events unless user is invited
    visible_events = []
    for event in events:
        if event.is_private_invitation:
            # Only show if user is invited
            if request.user.is_authenticated and (
                event.invited_users.filter(id=request.user.id).exists()
                or EventInvitation.objects.filter(
                    event=event,
                    created_user=request.user,
                    approval_status="approved",
                ).exists()
            ):
                visible_events.append(event)
        else:
            visible_events.append(event)

    context = {
        "events": visible_events,
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
        ).first()

    # For private events, verify access
    if event.is_private_invitation and not registration:
        is_invited = event.invited_users.filter(id=request.user.id).exists()
        has_approved_invitation = EventInvitation.objects.filter(
            event=event, created_user=request.user, approval_status="approved"
        ).exists()

        if not is_invited and not has_approved_invitation:
            messages.error(request, _("This event is by invitation only."))
            return redirect("crush_lu:event_list")

    context = {
        "event": event,
        "registration": registration,
    }
    return render(request, "crush_lu/event_detail.html", context)


def event_calendar_download(request, event_id):
    """Generate .ics calendar file for event"""
    event = get_object_or_404(MeetupEvent, id=event_id, is_published=True)

    from datetime import timezone as dt_timezone
    end_time = event.date_time + timedelta(minutes=event.duration_minutes)

    start_utc = event.date_time.astimezone(dt_timezone.utc)
    end_utc = end_time.astimezone(dt_timezone.utc)

    dtstart = start_utc.strftime('%Y%m%dT%H%M%SZ')
    dtend = end_utc.strftime('%Y%m%dT%H%M%SZ')
    dtstamp = timezone.now().astimezone(dt_timezone.utc).strftime('%Y%m%dT%H%M%SZ')

    if request.user.is_authenticated and hasattr(request.user, 'crushprofile'):
        location = f"{event.location}, {event.address}"
    else:
        location = event.canton or "Luxembourg"

    event_url = request.build_absolute_uri(
        reverse('crush_lu:event_detail', kwargs={'event_id': event.id})
    )

    description = event.description.replace('\n', '\\n').replace(',', '\\,').replace(';', '\\;')

    uid = f"event-{event.id}@crush.lu"

    ics_content = f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Crush.lu//Event Calendar//EN
CALSCALE:GREGORIAN
METHOD:PUBLISH
BEGIN:VEVENT
UID:{uid}
DTSTAMP:{dtstamp}
DTSTART:{dtstart}
DTEND:{dtend}
SUMMARY:{event.title}
DESCRIPTION:{description}\\n\\nRegister: {event_url}
LOCATION:{location}
URL:{event_url}
STATUS:CONFIRMED
SEQUENCE:0
END:VEVENT
END:VCALENDAR"""

    response = HttpResponse(ics_content, content_type='text/calendar; charset=utf-8')
    filename = f"crush-event-{event.id}.ics"
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    response['Cache-Control'] = 'no-cache'

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

    if EventRegistration.objects.filter(event=event, user=request.user).exclude(status='cancelled').exists():
        messages.warning(request, _("You are already registered for this event."))
        return redirect("crush_lu:event_detail", event_id=event_id)

    if not event.is_registration_open:
        messages.error(request, _("Registration is not available for this event."))
        return redirect("crush_lu:event_detail", event_id=event_id)

    if request.method == "POST":
        form = EventRegistrationForm(request.POST, event=event)
        if form.is_valid():
            cancelled_registration = EventRegistration.objects.filter(
                event=event, user=request.user, status='cancelled'
            ).first()

            if cancelled_registration:
                registration = cancelled_registration
                registration.dietary_restrictions = form.cleaned_data.get('dietary_restrictions', '')
                registration.bringing_guest = form.cleaned_data.get('bringing_guest', False)
                registration.guest_name = form.cleaned_data.get('guest_name', '')
            else:
                registration = form.save(commit=False)
                registration.event = event
                registration.user = request.user

            if event.is_full:
                registration.status = "waitlist"
                messages.info(
                    request, _("Event is full. You have been added to the waitlist.")
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
                    },
                )
    else:
        form = EventRegistrationForm(event=event)

    context = {
        "event": event,
        "form": form,
    }
    return render(request, "crush_lu/event_register.html", context)


@crush_login_required
def event_cancel(request, event_id):
    """Cancel event registration"""
    event = get_object_or_404(MeetupEvent, id=event_id)
    registration = get_object_or_404(EventRegistration, event=event, user=request.user)

    if request.method == "POST":
        registration.status = "cancelled"
        registration.save()
        messages.success(request, _("Your registration has been cancelled."))

        try:
            send_event_cancellation_confirmation(request.user, event, request)
        except Exception as e:
            logger.error(f"Failed to send event cancellation email: {e}")

        # Check if there's a waitlisted user to promote
        next_waitlisted = (
            EventRegistration.objects.filter(event=event, status="waitlist")
            .order_by("registered_at")
            .first()
        )

        if next_waitlisted:
            next_waitlisted.status = "confirmed"
            next_waitlisted.save()
            try:
                send_event_registration_confirmation(next_waitlisted, request)
            except Exception as e:
                logger.error(f"Failed to send waitlist promotion email: {e}")

        return redirect("crush_lu:dashboard")

    context = {
        "event": event,
        "registration": registration,
    }
    return render(request, "crush_lu/event_cancel.html", context)
