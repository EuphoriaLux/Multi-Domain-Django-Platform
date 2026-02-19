"""
Connection-related views for Crush.lu
Handles event attendee connections, connection requests, and messaging
"""

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.utils.translation import gettext_lazy as _
from django.db.models import Q
from django.views.decorators.http import require_http_methods
import logging

logger = logging.getLogger(__name__)

from datetime import timedelta

from django.utils import timezone

from .models import (
    CrushProfile,
    MeetupEvent,
    EventRegistration,
    EventConnection,
    ConnectionMessage,
)
from .models.crush_spark import CrushSpark
from .decorators import crush_login_required, ratelimit
from .notification_service import (
    notify_new_message,
    notify_new_connection,
    notify_connection_accepted,
)


# Post-Event Connection Views
@crush_login_required
def event_attendees(request, event_id):
    """Show attendees after user has attended event - allows connection requests"""
    event = get_object_or_404(MeetupEvent, id=event_id)

    # Verify user attended this event
    user_registration = get_object_or_404(
        EventRegistration, event=event, user=request.user
    )

    if not user_registration.can_make_connections:
        messages.error(
            request, _("You must attend this event before making connections.")
        )
        return redirect("crush_lu:event_detail", event_id=event_id)

    # Get other attendees (status='attended')
    attendees = (
        EventRegistration.objects.filter(event=event, status="attended")
        .exclude(user=request.user)
        .select_related("user__crushprofile")
    )

    # Pre-fetch all connections for this user+event into dicts for O(1) lookups
    # This avoids N+1 queries (previously 1+2N queries in the loop)
    sent_connections = {
        c.recipient_id: c
        for c in EventConnection.objects.filter(requester=request.user, event=event)
    }
    received_connections = {
        c.requester_id: c
        for c in EventConnection.objects.filter(recipient=request.user, event=event)
    }

    # Pre-fetch sent sparks for this user+event
    sent_sparks = {
        s.recipient_id: s
        for s in CrushSpark.objects.filter(
            event=event, sender=request.user,
        ).exclude(status=CrushSpark.Status.CANCELLED)
    }

    # Spark deadline and remaining count
    deadline = event.date_time + timedelta(hours=event.spark_request_deadline_hours)
    spark_deadline_active = timezone.now() <= deadline
    spark_count = len(sent_sparks)
    sparks_remaining = event.max_sparks_per_event - spark_count

    # Build attendee data with connection status
    attendee_data = []
    for reg in attendees:
        attendee_user = reg.user
        connection_status = None
        connection_id = None

        if attendee_user.id in sent_connections:
            conn = sent_connections[attendee_user.id]
            if conn.status in ("accepted", "coach_reviewing", "coach_approved", "shared"):
                connection_status = "mutual"
            else:
                connection_status = "sent"
            connection_id = conn.id
        elif attendee_user.id in received_connections:
            conn = received_connections[attendee_user.id]
            if conn.status in ("accepted", "coach_reviewing", "coach_approved", "shared"):
                connection_status = "mutual"
            elif conn.status == "pending":
                connection_status = "received"
            else:
                connection_status = "sent"  # declined or other non-actionable
            connection_id = conn.id

        attendee_data.append(
            {
                "user": attendee_user,
                "profile": getattr(attendee_user, "crushprofile", None),
                "connection_status": connection_status,
                "connection_id": connection_id,
                "spark": sent_sparks.get(attendee_user.id),
            }
        )

    # Event coaches
    event_coaches = event.coaches.filter(is_active=True).select_related("user")

    # Own profile for "How Others See You" section
    own_profile = getattr(request.user, "crushprofile", None)

    context = {
        "event": event,
        "attendees": attendee_data,
        "spark_deadline_active": spark_deadline_active,
        "sparks_remaining": sparks_remaining,
        "event_coaches": event_coaches,
        "own_profile": own_profile,
    }
    return render(request, "crush_lu/event_attendees.html", context)


@crush_login_required
@ratelimit(key='user', rate='10/h', method='POST')
def request_connection(request, event_id, user_id):
    """Request connection with another event attendee"""
    event = get_object_or_404(MeetupEvent, id=event_id)
    recipient = get_object_or_404(CrushProfile, user_id=user_id).user

    # Verify requester attended the event
    requester_reg = get_object_or_404(EventRegistration, event=event, user=request.user)

    if not requester_reg.can_make_connections:
        messages.error(
            request, _("You must attend this event before making connections.")
        )
        return redirect("crush_lu:event_detail", event_id=event_id)

    # Verify recipient attended the event
    recipient_reg = get_object_or_404(EventRegistration, event=event, user=recipient)

    if not recipient_reg.can_make_connections:
        messages.error(request, _("This person did not attend the event."))
        return redirect("crush_lu:event_attendees", event_id=event_id)

    # Check if connection already exists
    existing = EventConnection.objects.filter(
        Q(requester=request.user, recipient=recipient, event=event)
        | Q(requester=recipient, recipient=request.user, event=event)
    ).first()

    if existing:
        messages.warning(request, _("Connection request already exists."))
        return redirect("crush_lu:event_attendees", event_id=event_id)

    if request.method == "POST":
        note = request.POST.get("note", "").strip()

        # Create connection request
        connection = EventConnection.objects.create(
            requester=request.user,
            recipient=recipient,
            event=event,
            requester_note=note,
        )

        # Check if this is mutual (recipient already requested requester)
        reverse_connection = EventConnection.objects.filter(
            requester=recipient, recipient=request.user, event=event
        ).first()

        if reverse_connection:
            # Mutual interest! Both move to accepted
            connection.status = "accepted"
            connection.save()
            reverse_connection.status = "accepted"
            reverse_connection.save()

            # Assign coach to facilitate
            connection.assign_coach()
            reverse_connection.assigned_coach = connection.assigned_coach
            reverse_connection.save()

            # Notify both users about mutual connection
            try:
                notify_connection_accepted(
                    recipient=recipient,
                    connection=connection,
                    accepter=request.user,
                    request=request,
                )
                notify_connection_accepted(
                    recipient=request.user,
                    connection=reverse_connection,
                    accepter=recipient,
                    request=request,
                )
            except Exception as e:
                logger.error(f"Failed to send mutual connection notifications: {e}")

            messages.success(
                request,
                f"Mutual connection! 🎉 A coach will help facilitate your introduction.",
            )
        else:
            # Notify recipient about the connection request
            try:
                notify_new_connection(
                    recipient=recipient,
                    connection=connection,
                    requester=request.user,
                    request=request,
                )
            except Exception as e:
                logger.error(f"Failed to send connection request notification: {e}")

            messages.success(request, _("Connection request sent!"))

        return redirect("crush_lu:event_attendees", event_id=event_id)

    context = {
        "event": event,
        "recipient": recipient,
    }
    return render(request, "crush_lu/request_connection.html", context)


@crush_login_required
@require_http_methods(["GET", "POST"])
@ratelimit(key='user', rate='10/h', method='POST')
def request_connection_inline(request, event_id, user_id):
    """HTMX: Inline connection request form and processing"""
    event = get_object_or_404(MeetupEvent, id=event_id)
    recipient = get_object_or_404(CrushProfile, user_id=user_id).user

    # Verify requester attended the event
    requester_reg = get_object_or_404(EventRegistration, event=event, user=request.user)

    if not requester_reg.can_make_connections:
        return render(
            request,
            "crush_lu/_htmx_error.html",
            {"message": "You must attend this event before making connections."},
        )

    # Verify recipient attended the event
    recipient_reg = get_object_or_404(EventRegistration, event=event, user=recipient)

    if not recipient_reg.can_make_connections:
        return render(
            request,
            "crush_lu/_htmx_error.html",
            {"message": "This person did not attend the event."},
        )

    # Check if connection already exists
    existing = EventConnection.objects.filter(
        Q(requester=request.user, recipient=recipient, event=event)
        | Q(requester=recipient, recipient=request.user, event=event)
    ).first()

    if existing:
        return render(
            request,
            "crush_lu/_htmx_error.html",
            {"message": "Connection request already exists."},
        )

    if request.method == "POST":
        note = request.POST.get("note", "").strip()

        # Create connection request
        connection = EventConnection.objects.create(
            requester=request.user,
            recipient=recipient,
            event=event,
            requester_note=note,
        )

        # Check if this is mutual (recipient already requested requester)
        reverse_connection = EventConnection.objects.filter(
            requester=recipient, recipient=request.user, event=event
        ).first()

        is_mutual = False
        if reverse_connection:
            # Mutual interest! Both move to accepted
            connection.status = "accepted"
            connection.save()
            reverse_connection.status = "accepted"
            reverse_connection.save()

            # Assign coach to facilitate
            connection.assign_coach()
            reverse_connection.assigned_coach = connection.assigned_coach
            reverse_connection.save()
            is_mutual = True

        return render(
            request,
            "crush_lu/_connection_request_success.html",
            {"recipient": recipient, "is_mutual": is_mutual},
        )

    # GET: Show inline form
    return render(
        request,
        "crush_lu/_request_connection_form.html",
        {
            "event": event,
            "recipient": recipient,
        },
    )


@crush_login_required
def connection_actions(request, event_id, user_id):
    """HTMX: Get current connection actions for an attendee"""
    event = get_object_or_404(MeetupEvent, id=event_id)
    target_user = get_object_or_404(CrushProfile, user_id=user_id).user

    # Determine connection status
    connection_status = None
    connection_id = None

    # Check if current user sent a request to target
    sent = EventConnection.objects.filter(
        requester=request.user, recipient=target_user, event=event
    ).first()

    if sent:
        if sent.status in ["accepted", "coach_reviewing", "coach_approved", "shared"]:
            connection_status = "mutual"
        else:
            connection_status = "sent"

    # Check if target user sent a request to current user
    received = EventConnection.objects.filter(
        requester=target_user, recipient=request.user, event=event
    ).first()

    if received:
        if received.status == "pending":
            connection_status = "received"
            connection_id = received.id
        elif received.status in [
            "accepted",
            "coach_reviewing",
            "coach_approved",
            "shared",
        ]:
            connection_status = "mutual"

    # Build attendee object for template
    attendee = {
        "user": target_user,
        "connection_status": connection_status,
        "connection_id": connection_id,
    }

    return render(
        request,
        "crush_lu/_attendee_connection_actions.html",
        {
            "attendee": attendee,
            "event": event,
        },
    )


@crush_login_required
@ratelimit(key='user', rate='10/h', method='POST')
@require_http_methods(["GET", "POST"])
def respond_connection(request, connection_id, action):
    """Accept or decline a connection request"""
    connection = get_object_or_404(
        EventConnection, id=connection_id, recipient=request.user
    )

    # Handle already-processed connections (e.g. auto-mutual-accept)
    if connection.status != "pending":
        if request.headers.get("HX-Request"):
            attendee = {
                "user": connection.requester,
                "connection_status": "mutual" if connection.status in ("accepted", "coach_reviewing", "coach_approved", "shared") else "declined",
                "connection_id": connection.id,
            }
            return render(
                request,
                "crush_lu/_attendee_connection_response.html",
                {"attendee": attendee, "action": "accept" if connection.status in ("accepted", "coach_reviewing", "coach_approved", "shared") else "decline"},
            )
        return redirect("crush_lu:my_connections")

    # Security: Verify user actually attended the event
    try:
        user_registration = EventRegistration.objects.get(
            event=connection.event, user=request.user
        )
        if not user_registration.can_make_connections:
            if request.headers.get("HX-Request"):
                return render(
                    request,
                    "crush_lu/_htmx_error.html",
                    {
                        "message": "You must have attended this event to respond to connections."
                    },
                )
            messages.error(
                request,
                _("You must have attended this event to respond to connections."),
            )
            return redirect("crush_lu:my_connections")
    except EventRegistration.DoesNotExist:
        if request.headers.get("HX-Request"):
            return render(
                request,
                "crush_lu/_htmx_error.html",
                {"message": "You are not registered for this event."},
            )
        messages.error(request, _("You are not registered for this event."))
        return redirect("crush_lu:my_connections")

    # Determine which template to use based on HX-Target
    # If coming from attendees page, target is connection-actions-{user_id}
    # If coming from my_connections, target is connection-{connection_id}
    hx_target = request.headers.get("HX-Target", "")
    is_attendees_page = "connection-actions-" in hx_target

    if action == "accept":
        connection.status = "accepted"
        connection.save()

        # Assign coach
        connection.assign_coach()

        # Notify requester that their connection was accepted
        try:
            notify_connection_accepted(
                recipient=connection.requester,
                connection=connection,
                accepter=request.user,
                request=request,
            )
        except Exception as e:
            logger.error(f"Failed to send connection accepted notification: {e}")

        # Return HTMX partial or redirect
        if request.headers.get("HX-Request"):
            if is_attendees_page:
                # For attendees page, return simpler response with attendee context
                attendee = {
                    "user": connection.requester,
                    "connection_status": "mutual",
                    "connection_id": connection.id,
                }
                return render(
                    request,
                    "crush_lu/_attendee_connection_response.html",
                    {"attendee": attendee, "action": "accept"},
                )
            return render(
                request,
                "crush_lu/_connection_response.html",
                {"connection": connection, "action": "accept"},
            )
        messages.success(
            request,
            _("Connection accepted! A coach will help facilitate your introduction."),
        )
    elif action == "decline":
        connection.status = "declined"
        connection.save()

        # Return HTMX partial or redirect
        if request.headers.get("HX-Request"):
            if is_attendees_page:
                attendee = {
                    "user": connection.requester,
                }
                return render(
                    request,
                    "crush_lu/_attendee_connection_response.html",
                    {"attendee": attendee, "action": "decline"},
                )
            return render(
                request,
                "crush_lu/_connection_response.html",
                {"connection": connection, "action": "decline"},
            )
        messages.info(request, _("Connection request declined."))
    else:
        if request.headers.get("HX-Request"):
            return render(
                request, "crush_lu/_htmx_error.html", {"message": "Invalid action."}
            )
        messages.error(request, _("Invalid action."))

    return redirect("crush_lu:my_connections")


@crush_login_required
def my_connections(request):
    """View all connections (sent, received, active)"""
    # Sent requests
    sent = (
        EventConnection.objects.filter(requester=request.user)
        .select_related("recipient__crushprofile", "event", "assigned_coach")
        .order_by("-requested_at")
    )

    # Received requests (pending only)
    received_pending = (
        EventConnection.objects.filter(recipient=request.user, status="pending")
        .select_related("requester__crushprofile", "event")
        .order_by("-requested_at")
    )

    # Active connections (accepted, coach_reviewing, coach_approved, shared)
    active = (
        EventConnection.objects.active_for_user(request.user)
        .select_related(
            "requester__crushprofile",
            "recipient__crushprofile",
            "event",
            "assigned_coach",
        )
        .order_by("-requested_at")
    )

    context = {
        "sent_requests": sent,
        "received_requests": received_pending,
        "active_connections": active,
    }
    return render(request, "crush_lu/my_connections.html", context)


@crush_login_required
@ratelimit(key='user', rate='20/h', method='POST')
def connection_detail(request, connection_id):
    """View connection details and provide consent"""
    connection = get_object_or_404(
        EventConnection,
        Q(requester=request.user) | Q(recipient=request.user),
        id=connection_id,
    )

    # Determine if current user is requester or recipient
    is_requester = connection.requester == request.user

    if request.method == "POST":
        # Handle consent
        if "consent" in request.POST:
            consent_value = request.POST.get("consent") == "yes"

            if is_requester:
                connection.requester_consents_to_share = consent_value
            else:
                connection.recipient_consents_to_share = consent_value

            connection.save()

            # Check if both consented and coach approved
            if connection.can_share_contacts:
                connection.status = "shared"
                connection.save()
                messages.success(request, _("Contact information is now shared!"))
            else:
                messages.success(request, _("Your consent has been recorded."))

            return redirect("crush_lu:connection_detail", connection_id=connection_id)

        # Handle message sending
        elif "message" in request.POST:
            message_text = request.POST.get("message", "").strip()
            if message_text and len(message_text) <= 2000:
                # Only allow messaging for accepted/shared connections
                if connection.status in [
                    "accepted",
                    "coach_reviewing",
                    "coach_approved",
                    "shared",
                ]:
                    # Determine the recipient
                    recipient = (
                        connection.recipient if is_requester else connection.requester
                    )

                    # Create the message
                    new_message = ConnectionMessage.objects.create(
                        connection=connection, sender=request.user, message=message_text
                    )

                    # Send notification to recipient (push first, email fallback)
                    try:
                        notify_new_message(
                            recipient=recipient, message=new_message, request=request
                        )
                    except Exception as e:
                        logger.error(f"Failed to send new message notification: {e}")

                    # For HTMX requests, return just the message partial
                    if request.headers.get("HX-Request"):
                        return render(
                            request,
                            "crush_lu/_connection_message.html",
                            {
                                "msg": new_message,
                                "is_own_message": True,
                            },
                        )

                    messages.success(request, _("Message sent!"))
                else:
                    messages.error(
                        request, _("You can only message accepted connections.")
                    )
            else:
                messages.error(
                    request, _("Please enter a valid message (max 2000 characters).")
                )

            return redirect("crush_lu:connection_detail", connection_id=connection_id)

    # Get the other person in the connection
    other_user = connection.recipient if is_requester else connection.requester

    # Get messages for this connection
    connection_messages = (
        ConnectionMessage.objects.filter(connection=connection)
        .select_related("sender")
        .order_by("sent_at")
    )

    context = {
        "connection": connection,
        "is_requester": is_requester,
        "other_user": other_user,
        "other_profile": getattr(other_user, "crushprofile", None),
        "messages": connection_messages,
    }
    return render(request, "crush_lu/connection_detail.html", context)
