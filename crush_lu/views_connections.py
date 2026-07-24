"""
Connection-related views for Crush.lu
Handles event attendee connections, connection requests, and messaging
"""

import re
from collections import OrderedDict

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.http import Http404, HttpResponse
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.db.models import Q
from django.views.decorators.http import require_http_methods
import logging

logger = logging.getLogger(__name__)


from django.utils import timezone

from .models import (
    CrushProfile,
    MeetupEvent,
    EventRegistration,
    EventConnection,
    ConnectionMessage,
)
from .decorators import crush_login_required, ratelimit
from .notification_service import (
    notify_new_message,
    notify_connection_accepted,
)

# Max length for a single connection message. Single source of truth for the
# server-side cap, the user-facing error, and the textarea maxlength/counter.
CONNECTION_MESSAGE_MAX_LENGTH = 500


def _approved_messages(connection):
    """Coach-approved messages for a connection, ready to render.

    Shared by the detail page and the polling endpoint so the two never drift.
    Selects sender + crushprofile because the bubble partial renders
    ``msg.sender.crushprofile.display_name`` (avoids an N+1 per message).
    """
    return (
        ConnectionMessage.objects.filter(connection=connection, coach_approved=True)
        .select_related("sender", "sender__crushprofile")
        .order_by("sent_at")
    )


# Post-Event Connection Views
@crush_login_required
def event_attendees(request, event_id):
    """Show attendees after user has attended event - allows connection requests"""
    event = get_object_or_404(MeetupEvent, id=event_id)

    # Verify user attended this event (status must be 'attended')
    user_registration = get_object_or_404(
        EventRegistration, event=event, user=request.user, status="attended"
    )

    if not user_registration.can_make_connections:
        messages.error(
            request, _("You must attend this event before making connections.")
        )
        return redirect("crush_lu:event_detail", event_id=event_id)

    # Live overlap gate (decision 2026-07-18): the named attendees list opens
    # only once the event ends — live-time socializing belongs to the
    # anonymous Event Lobby, which must not be undercut by a parallel page
    # exposing names mid-event.
    if timezone.now() < event.end_time:
        messages.info(
            request,
            _(
                "Connections open once the event ends. During the event, the "
                "Event Lobby is where Crush Connect members quietly signal "
                "who they'd like to meet."
            ),
        )
        return redirect("crush_lu:event_detail", event_id=event_id)

    # Upper bound too: once the window closes the named roster disappears with
    # it (spec §5.3 amendment — both post-event surfaces close together).
    # Members keep their existing connections on my_connections.
    if not event.connection_window_active:
        messages.info(
            request,
            _(
                "The connection window for this event has closed. "
                "Your connections are always available here."
            ),
        )
        return redirect("crush_lu:my_connections")

    # Get other attendees (status='attended'), hiding anyone in a block pair
    # with the viewer (symmetric — see services.blocking) and anyone hidden by
    # a pending/approved encounter removal (§9.1: removal pairs mirror block
    # semantics on the crush surfaces — neither flow is offered).
    from .services.blocking import blocked_user_ids
    from .services.event_lobby import hidden_encounter_user_ids

    blocked_ids = blocked_user_ids(request.user)
    hidden_ids = hidden_encounter_user_ids(request.user)

    attendees = (
        EventRegistration.objects.filter(event=event, status="attended")
        .exclude(user=request.user)
        .exclude(user_id__in=blocked_ids)
        .exclude(user_id__in=hidden_ids)
        .select_related("user__crushprofile")
        # Event Identity chips render per attendee — prefetch the taxonomy M2M
        # so the card row stays N+1-free (spec §7).
        .prefetch_related("user__crushprofile__interests_new")
    )

    # Pre-fetch all connections for this user+event into dicts for O(1) lookups
    # This avoids N+1 queries (previously 1+2N queries in the loop). Blocked
    # counterparts are excluded so a pre-existing request from a now-blocked
    # member never resurfaces (card, "someone wants to connect" hint, count).
    sent_connections = {
        c.recipient_id: c
        for c in EventConnection.objects.filter(
            requester=request.user, event=event
        ).exclude(recipient_id__in=blocked_ids)
    }
    # Received rows: pre-`shared` crush leads are private — the recipient must
    # see nothing (no Accept/Decline card, no hint, no count) until the
    # coach-facilitated introduction completes.
    received_connections = {
        c.requester_id: c
        for c in EventConnection.objects.filter(
            recipient=request.user, event=event
        )
        .excluding_unshared_crushes()
        .exclude(requester_id__in=blocked_ids)
    }

    # Spark feature is soft-removed; pre-fetch left at empty so any straggling
    # template reference resolves to a no-op spark (the new connection-only
    # event_attendees template does not render spark UI).
    sent_sparks = {}

    # Post-event connection window (replaces the old spark deadline; same
    # property is now the single source of truth — see event.connection_window_hours).
    deadline = event.connection_window_deadline
    connection_window_active = event.connection_window_active

    # Privacy-preserving "interest hint": how many people requested a connection
    # with this user for this event but haven't been reciprocated yet.
    incoming_pending_count = sum(
        1 for c in received_connections.values() if c.status == "pending"
    )

    # Build attendee data with connection status
    active_statuses = ("accepted", "coach_reviewing", "coach_approved", "shared")
    attendee_data = []
    for reg in attendees:
        attendee_user = reg.user
        connection_status = None
        connection_id = None

        sent_conn = sent_connections.get(attendee_user.id)
        recv_conn = received_connections.get(attendee_user.id)
        is_two_sided = sent_conn is not None and recv_conn is not None

        if sent_conn is not None:
            if (
                sent_conn.flow == EventConnection.FLOW_CRUSH
                and sent_conn.status != "shared"
            ):
                # Own "My Crush!" lead — neutral state regardless of the
                # actual lead status (declines and coach progress stay
                # invisible to the crusher; only `shared` is distinguishable).
                connection_status = "crush_sent"
            elif sent_conn.status in active_statuses:
                # Both sides requested independently → distinct mutual_match badge.
                # One-sided accept (recipient accepted our request) keeps the plain "mutual".
                connection_status = "mutual_match" if is_two_sided else "mutual"
            else:
                connection_status = "sent"
            connection_id = sent_conn.id
        elif recv_conn is not None:
            if recv_conn.status in active_statuses:
                connection_status = "mutual"
            elif recv_conn.status == "pending":
                connection_status = "received"
            else:
                connection_status = "sent"  # declined or other non-actionable
            connection_id = recv_conn.id

        attendee_data.append(
            {
                "user": attendee_user,
                "profile": getattr(attendee_user, "crushprofile", None),
                "connection_status": connection_status,
                "connection_id": connection_id,
                "spark": sent_sparks.get(attendee_user.id),
            }
        )

    # "My Crush!" counter (O9): 1 crush per event per member, free and
    # Connect alike, gender-independent. Supersedes the legacy cross-gender
    # mechanic for new declarations — every declaration is now a crush lead.
    from .services.crush_leads import crushes_remaining

    crushes_remaining_count = crushes_remaining(request.user, event)

    # One pair, one flow (§9.1): pairs visible in the requester's open Event
    # Lobby recap get the recap CTA instead of the My Crush! button. Computed
    # once per event (not per attendee) — the decision inputs are set-valued.
    from .services.event_lobby import (
        PHASE_RECAP,
        eligible_participations,
        event_lobby_phase,
        lobby_feature_enabled,
        viewer_participation,
    )

    recap_eligible_ids = set()
    if (
        lobby_feature_enabled()
        and event_lobby_phase(event) == PHASE_RECAP
        and viewer_participation(request.user, event) is not None
    ):
        recap_eligible_ids = set(
            eligible_participations(event).values_list("user_id", flat=True)
        )

    for attendee in attendee_data:
        # Removal pairs are already absent from the list (block semantics), so
        # set membership is exactly "target present in the requester's recap
        # roster" here.
        attendee["recap_cta"] = attendee["user"].id in recap_eligible_ids

    # Group attendees by gender
    gender_order = ["F", "M", "NB", "O", "P", ""]
    gender_labels = {
        "F": _("Women"),
        "M": _("Men"),
        "NB": _("Non-binary"),
        "O": _("Other"),
        "P": _("Prefer not to say"),
        "": _("Not specified"),
    }
    grouped_attendees = OrderedDict()
    for gender_code in gender_order:
        group = [
            a
            for a in attendee_data
            if (getattr(a["profile"], "gender", "") or "") == gender_code
        ]
        if group:
            grouped_attendees[gender_labels[gender_code]] = group

    # Event coaches
    event_coaches = event.coaches.filter(is_active=True).select_related("user")

    # Own profile for "How Others See You" section
    own_profile = getattr(request.user, "crushprofile", None)

    # Event Lobby cross-link: participants get the recap banner while the
    # 48h confirmation window is open; everyone else gets the static feature
    # promo (§5.3 as amended 2026-07-18 — existence is public, state is not).
    from .services.event_lobby import lobby_cta

    event_lobby_cta = lobby_cta(request.user, event, registration=user_registration)

    context = {
        "event": event,
        "event_lobby_cta": event_lobby_cta,
        "attendees": attendee_data,
        "grouped_attendees": grouped_attendees,
        "connection_window_active": connection_window_active,
        "connection_window_deadline": deadline,
        # Spark context vars kept (empty/inert) so any third-party include or
        # cached template fragment that still references them doesn't 500.
        "spark_deadline_active": False,
        "spark_deadline": None,
        "sparks_remaining": 0,
        "crushes_remaining": crushes_remaining_count,
        "event_coaches": event_coaches,
        "own_profile": own_profile,
        "incoming_pending_count": incoming_pending_count,
    }
    return render(request, "crush_lu/event_attendees.html", context)


@crush_login_required
@ratelimit(key="user", rate="10/h", method="POST")
def request_connection(request, event_id, user_id):
    """Request connection with another event attendee"""
    event = get_object_or_404(MeetupEvent, id=event_id)
    recipient = get_object_or_404(CrushProfile, user_id=user_id).user

    # Prevent self-connections
    if recipient == request.user:
        messages.error(request, _("You cannot connect with yourself."))
        return redirect("crush_lu:event_attendees", event_id=event_id)

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

    # Refuse if either party has blocked the other. Checked only AFTER the
    # attendance checks above so block status isn't probeable by a non-attendee
    # hitting arbitrary event/user URLs.
    from .services.blocking import is_blocked_pair

    if is_blocked_pair(request.user, recipient):
        messages.error(request, _("You cannot connect with this member."))
        return redirect("crush_lu:event_attendees", event_id=event_id)

    # One pair, one flow (§9.1): pairs visible in the requester's open Event
    # Lobby recap are redirected there instead of creating a crush lead —
    # enforced in the write endpoint, not just the button, so direct POSTs
    # can't bypass the invariant. Removal pairs get neither flow and are
    # rejected with the same non-disclosing message as a block.
    from .services.event_lobby import (
        CRUSH_FLOW_REDIRECT,
        CRUSH_FLOW_UNAVAILABLE,
        crush_flow_decision,
    )

    flow_decision = crush_flow_decision(request.user, recipient, event)
    if flow_decision == CRUSH_FLOW_REDIRECT:
        messages.info(
            request,
            _("You'll find them in your event recap — confirm you met."),
        )
        return redirect("crush_lu:event_lobby", event_id=event_id)
    if flow_decision == CRUSH_FLOW_UNAVAILABLE:
        messages.error(request, _("You cannot connect with this member."))
        return redirect("crush_lu:event_attendees", event_id=event_id)

    # Directional duplicate guard (§7): only a SAME-direction row blocks a new
    # declaration. A reverse row never does — reciprocal crushes create a
    # second, independent lead — and its existence is never disclosed.
    existing = EventConnection.objects.filter(
        requester=request.user, recipient=recipient, event=event
    ).first()

    if existing:
        messages.warning(
            request, _("You've already declared your crush on this person.")
        )
        return redirect("crush_lu:event_attendees", event_id=event_id)

    # Live overlap gate: no connection requests before the event ends
    # (decision 2026-07-18 — mirrors the attendees-page gate above).
    if timezone.now() < event.end_time:
        messages.info(
            request,
            _("Connections open once the event ends."),
        )
        return redirect("crush_lu:event_detail", event_id=event_id)

    # Post-event connection window — block new requests once it closes and
    # nudge the user toward the Crush Connect waitlist instead.
    if not event.connection_window_active:
        messages.info(
            request,
            _(
                "The connection window for this event has closed. "
                "Join the Crush Connect waitlist to keep meeting people year-round."
            ),
        )
        return redirect("crush_lu:crush_connect_teaser")

    if request.method == "POST":
        note = request.POST.get("note", "").strip()

        # Validate note length
        if len(note) > 300:
            messages.error(request, _("Note is too long (max 300 characters)."))
            return redirect("crush_lu:event_attendees", event_id=event_id)

        # Every post-event declaration is a "My Crush!" coach lead
        # (flow='crush'). The legacy mutual auto-accept/auto-share branch is
        # deliberately gone: a reciprocal declaration creates a second,
        # independent lead routed to its own coach — no status change, no
        # auto-consent, no member notification in either direction.
        from django.db import IntegrityError

        from .services.crush_leads import (
            CrushDeclarationLimitReached,
            declare_crush,
        )

        try:
            declare_crush(
                requester=request.user,
                recipient=recipient,
                event=event,
                requester_registration=requester_reg,
                note=note,
            )
        except CrushDeclarationLimitReached:
            messages.error(
                request,
                _(
                    "You've already declared your crush for this event. "
                    "Your Crush Coach will call you within 48 hours to talk about it."
                ),
            )
            return redirect("crush_lu:event_attendees", event_id=event_id)
        except IntegrityError:
            # Same-direction duplicate race — identical to the duplicate guard.
            messages.warning(
                request, _("You've already declared your crush on this person.")
            )
            return redirect("crush_lu:event_attendees", event_id=event_id)

        messages.success(
            request,
            _(
                "My Crush! declared 💕 It's completely private — they'll "
                "never know unless your coach makes the introduction. "
                "Your Crush Coach will call you within 48 hours to talk about it."
            ),
        )
        return redirect("crush_lu:event_attendees", event_id=event_id)

    context = {
        "event": event,
        "recipient": recipient,
    }
    return render(request, "crush_lu/request_connection.html", context)


@crush_login_required
@require_http_methods(["GET", "POST"])
@ratelimit(key="user", rate="10/h", method="POST")
def request_connection_inline(request, event_id, user_id):
    """HTMX: Inline connection request form and processing"""
    event = get_object_or_404(MeetupEvent, id=event_id)
    recipient = get_object_or_404(CrushProfile, user_id=user_id).user

    # Prevent self-connections
    if recipient == request.user:
        return render(
            request,
            "crush_lu/_htmx_error.html",
            {"message": "You cannot connect with yourself."},
        )

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

    # Refuse if either party has blocked the other. After the attendance checks
    # so block status isn't probeable by a non-attendee.
    from .services.blocking import is_blocked_pair

    if is_blocked_pair(request.user, recipient):
        return render(
            request,
            "crush_lu/_htmx_error.html",
            {"message": _("You cannot connect with this member.")},
        )

    # One pair, one flow (§9.1) — enforced in the write endpoint, not just
    # the button: direct POSTs for a recap-eligible pair redirect to the
    # recap instead of creating a crush lead. Removal pairs get neither flow
    # and are rejected with the same non-disclosing message as a block.
    from .services.event_lobby import (
        CRUSH_FLOW_REDIRECT,
        CRUSH_FLOW_UNAVAILABLE,
        crush_flow_decision,
    )

    flow_decision = crush_flow_decision(request.user, recipient, event)
    if flow_decision == CRUSH_FLOW_REDIRECT:
        # Also keyed on HX-Request, not the method alone: the button issues an
        # `hx-get` too, and a stale attendee page reaches this branch when the
        # pair became recap-eligible after render. A plain 302 there is
        # followed by the XHR, and HTMX swaps the whole lobby document into
        # the card's `#connection-actions-*` target instead of navigating.
        # POST keeps emitting the header unconditionally — this widens the
        # HTMX path rather than narrowing it.
        if request.method == "POST" or request.headers.get("HX-Request"):
            return HttpResponse(
                headers={
                    "HX-Redirect": reverse(
                        "crush_lu:event_lobby", kwargs={"event_id": event_id}
                    )
                }
            )
        messages.info(
            request,
            _("You'll find them in your event recap — confirm you met."),
        )
        return redirect("crush_lu:event_lobby", event_id=event_id)
    if flow_decision == CRUSH_FLOW_UNAVAILABLE:
        return render(
            request,
            "crush_lu/_htmx_error.html",
            {"message": _("You cannot connect with this member.")},
        )

    # Directional duplicate guard (§7): only a SAME-direction row blocks;
    # reciprocal declarations create a second, independent lead and a
    # reverse row's existence is never disclosed.
    existing = EventConnection.objects.filter(
        requester=request.user, recipient=recipient, event=event
    ).first()

    if existing:
        return render(
            request,
            "crush_lu/_htmx_error.html",
            {"message": _("You've already declared your crush on this person.")},
        )

    # Live overlap gate: no connection requests before the event ends
    # (decision 2026-07-18 — mirrors the attendees-page gate).
    if timezone.now() < event.end_time:
        return render(
            request,
            "crush_lu/_htmx_error.html",
            {"message": _("Connections open once the event ends.")},
        )

    # Post-event connection window — swap the request form for a Crush Connect CTA.
    if not event.connection_window_active:
        return render(request, "crush_lu/_connection_window_closed.html")

    if request.method == "POST":
        note = request.POST.get("note", "").strip()

        # Validate note length
        if len(note) > 300:
            return render(
                request,
                "crush_lu/_htmx_error.html",
                {"message": _("Note is too long (max 300 characters).")},
            )

        # Every post-event declaration is a "My Crush!" coach lead
        # (flow='crush'). The legacy mutual auto-accept/auto-share branch is
        # deliberately gone: reciprocal declarations stay independent leads
        # and no notification is sent in either direction.
        from django.db import IntegrityError

        from .services.crush_leads import (
            CrushDeclarationLimitReached,
            declare_crush,
        )

        try:
            declare_crush(
                requester=request.user,
                recipient=recipient,
                event=event,
                requester_registration=requester_reg,
                note=note,
            )
        except CrushDeclarationLimitReached:
            return render(
                request,
                "crush_lu/_htmx_error.html",
                {
                    "message": _(
                        "You've already declared your crush for this event. "
                        "Your Crush Coach will call you within 48 hours to talk about it."
                    )
                },
            )
        except IntegrityError:
            # Same-direction duplicate race — identical to the duplicate guard.
            return render(
                request,
                "crush_lu/_htmx_error.html",
                {"message": _("You've already declared your crush on this person.")},
            )

        return render(
            request,
            "crush_lu/_connection_request_success.html",
            {"recipient": recipient},
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
        if sent.flow == EventConnection.FLOW_CRUSH and sent.status != "shared":
            # Own "My Crush!" lead — neutral state (see event_attendees).
            connection_status = "crush_sent"
        elif sent.status in ["accepted", "coach_reviewing", "coach_approved", "shared"]:
            connection_status = "mutual"
        else:
            connection_status = "sent"

    # Check if target user sent a request to current user. Pre-`shared` crush
    # leads are private: for the recipient this endpoint must yield the
    # byte-identical no-connection representation.
    received = (
        EventConnection.objects.filter(
            requester=target_user, recipient=request.user, event=event
        )
        .excluding_unshared_crushes()
        .first()
    )

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

    # One pair, one flow (§9.1): recap-eligible pairs get the recap CTA
    # instead of the My Crush! button (mirrors the attendees page).
    from .services.event_lobby import (
        CRUSH_FLOW_REDIRECT,
        crush_flow_decision,
    )
    from .services.crush_leads import crushes_remaining

    attendee = {
        "user": target_user,
        "connection_status": connection_status,
        "connection_id": connection_id,
        "recap_cta": (
            crush_flow_decision(request.user, target_user, event)
            == CRUSH_FLOW_REDIRECT
        ),
    }

    return render(
        request,
        "crush_lu/event_attendees.html#connection_actions",
        {
            "attendee": attendee,
            "event": event,
            "crushes_remaining": crushes_remaining(request.user, event),
        },
    )


@crush_login_required
@ratelimit(key="user", rate="10/h", method="POST")
@require_http_methods(["POST"])
def respond_connection(request, connection_id, action):
    """Accept or decline a connection request (POST-only to prevent
    GET-based state changes via link-luring — finding M14)."""
    connection = get_object_or_404(
        EventConnection, id=connection_id, recipient=request.user
    )

    # The recipient's response endpoint is closed for crush leads (§5):
    # consent moves only through the coach. A guessed accept/decline URL
    # no-ops neutrally — no status change, no consent flags, no
    # notification, and the same non-disclosing message as a dead connection.
    if (
        connection.flow == EventConnection.FLOW_CRUSH
        and connection.status != "shared"
    ):
        if request.headers.get("HX-Request"):
            return render(
                request,
                "crush_lu/_htmx_error.html",
                {"message": _("This connection is no longer available.")},
            )
        messages.error(request, _("This connection is no longer available."))
        return redirect("crush_lu:my_connections")

    # Block guard: a block placed after the request arrived must stop the
    # pending → accepted transition (an old notification / known accept URL
    # could otherwise turn a blocked pair into a shared connection).
    from .services.blocking import is_blocked_pair

    if is_blocked_pair(request.user, connection.requester):
        if request.headers.get("HX-Request"):
            return render(
                request,
                "crush_lu/_htmx_error.html",
                {"message": _("This connection is no longer available.")},
            )
        messages.error(request, _("This connection is no longer available."))
        return redirect("crush_lu:my_connections")

    # Handle already-processed connections (e.g. auto-mutual-accept)
    if connection.status != "pending":
        if request.headers.get("HX-Request"):
            attendee = {
                "user": connection.requester,
                "connection_status": (
                    "mutual"
                    if connection.status
                    in ("accepted", "coach_reviewing", "coach_approved", "shared")
                    else "declined"
                ),
                "connection_id": connection.id,
            }
            return render(
                request,
                "crush_lu/_attendee_connection_response.html",
                {
                    "attendee": attendee,
                    "action": (
                        "accept"
                        if connection.status
                        in ("accepted", "coach_reviewing", "coach_approved", "shared")
                        else "decline"
                    ),
                },
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
        if connection.is_same_gender:
            # Same-gender: skip coach review, auto-share
            connection.status = "shared"
            connection.requester_consents_to_share = True
            connection.recipient_consents_to_share = True
            connection.shared_at = timezone.now()
            connection.save()
        else:
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
        if connection.is_same_gender:
            messages.success(
                request,
                _("Connection accepted! Contact info is now shared."),
            )
        else:
            messages.success(
                request,
                _(
                    "Connection accepted! A coach will help facilitate your introduction."
                ),
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
    # Hide connections whose counterpart is in a block pair with the viewer.
    from .services.blocking import blocked_user_ids

    blocked_ids = blocked_user_ids(request.user)

    # Sent requests
    sent = (
        EventConnection.objects.filter(requester=request.user)
        .exclude(recipient_id__in=blocked_ids)
        .select_related("recipient__crushprofile", "event", "assigned_coach")
        .order_by("-requested_at")
    )

    # Received requests (pending only). Crush leads are private — they never
    # appear in the recipient's inbox with an Accept/Decline card.
    received_pending = (
        EventConnection.objects.filter(recipient=request.user, status="pending")
        .exclude(flow=EventConnection.FLOW_CRUSH)
        .exclude(requester_id__in=blocked_ids)
        .select_related("requester__crushprofile", "event")
        .order_by("-requested_at")
    )

    # Active connections (accepted, coach_reviewing, coach_approved, shared).
    # Pre-`shared` crush rows are excluded for BOTH sides: the recipient must
    # see nothing, and the crusher's lead renders in Sent Requests below in
    # its neutral "with your coach" state instead.
    active = (
        EventConnection.objects.active_for_user(request.user)
        .excluding_unshared_crushes()
        .exclude(requester_id__in=blocked_ids)
        .exclude(recipient_id__in=blocked_ids)
        .select_related(
            "requester__crushprofile",
            "recipient__crushprofile",
            "event",
            "assigned_coach",
        )
        .order_by("-requested_at")
    )

    # People I've Met cross-link — only when the member actually has confirmed
    # encounters (an empty collection link is noise for everyone else).
    from .services.event_lobby import get_people_ive_met, lobby_feature_enabled

    people_ive_met_count = (
        len(get_people_ive_met(request.user)) if lobby_feature_enabled() else 0
    )

    context = {
        "sent_requests": sent,
        "received_requests": received_pending,
        "active_connections": active,
        "people_ive_met_count": people_ive_met_count,
    }
    return render(request, "crush_lu/my_connections.html", context)


@crush_login_required
@ratelimit(key="user", rate="20/h", method="POST")
def connection_detail(request, connection_id):
    """View connection details and provide consent"""
    connection = get_object_or_404(
        EventConnection,
        Q(requester=request.user) | Q(recipient=request.user),
        id=connection_id,
    )

    # Determine if current user is requester or recipient
    is_requester = connection.requester == request.user

    # A crush lead is private until the introduction completes (`shared`):
    # the recipient must not discover the row from any page, including a
    # bookmarked or guessed detail URL — same 404 as a non-existent id.
    crush_lead_neutral = (
        connection.flow == EventConnection.FLOW_CRUSH
        and connection.status != "shared"
    )
    if crush_lead_neutral and not is_requester:
        raise Http404

    # Block guard: once either party blocks the other, the connection is dead.
    from .services.blocking import is_blocked_pair

    other_user = connection.recipient if is_requester else connection.requester
    if is_blocked_pair(request.user, other_user):
        messages.error(request, _("This connection is no longer available."))
        return redirect("crush_lu:my_connections")

    if request.method == "POST":
        # Crush leads: member-facing consent and chat stay closed in every
        # pre-`shared` status — consent is given verbally to the coach and
        # recorded coach-side (Phase D); there is no chat before the
        # introduction (§7 messaging lock, enforced server-side).
        if crush_lead_neutral:
            return redirect(
                "crush_lu:connection_detail", connection_id=connection_id
            )

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
            if message_text and len(message_text) <= CONNECTION_MESSAGE_MAX_LENGTH:
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
                # Literal 500 keeps the existing translation catalog entry intact;
                # CONNECTION_MESSAGE_MAX_LENGTH governs the actual cap above.
                messages.error(
                    request, _("Please enter a valid message (max 500 characters).")
                )

            return redirect("crush_lu:connection_detail", connection_id=connection_id)

    # Get the other person in the connection
    other_user = connection.recipient if is_requester else connection.requester
    other_profile = getattr(other_user, "crushprofile", None)

    # Get messages for this connection (exclude coach-hidden messages)
    thread_messages = _approved_messages(connection)

    # Can the current user send messages? (Never on a pre-`shared` crush lead.)
    can_message = not crush_lead_neutral and connection.status in [
        "accepted",
        "coach_reviewing",
        "coach_approved",
        "shared",
    ]

    # Does the current user need to give consent?
    user_needs_consent = False
    if not crush_lead_neutral and connection.status == "coach_approved":
        if is_requester and not connection.requester_consents_to_share:
            user_needs_consent = True
        elif not is_requester and not connection.recipient_consents_to_share:
            user_needs_consent = True

    # Has the current user already consented (waiting for the other)?
    user_already_consented = False
    if (
        not crush_lead_neutral
        and connection.status == "coach_approved"
        and not user_needs_consent
    ):
        user_already_consented = True

    # WhatsApp number (clean phone for wa.me link, only when shared)
    whatsapp_number = ""
    if connection.status == "shared" and other_profile and other_profile.phone_number:
        whatsapp_number = re.sub(r"[^\d+]", "", other_profile.phone_number)

    context = {
        "connection": connection,
        "is_requester": is_requester,
        "other_user": other_user,
        "other_profile": other_profile,
        "messages": thread_messages,
        "can_message": can_message,
        "message_max_length": CONNECTION_MESSAGE_MAX_LENGTH,
        "user_needs_consent": user_needs_consent,
        "user_already_consented": user_already_consented,
        "whatsapp_number": whatsapp_number,
        # Pre-`shared` crush lead: the requester sees only this neutral
        # "with your coach" state — identical whether the lead is pending,
        # mid-coach-workflow, or silently declined.
        "crush_lead_neutral": crush_lead_neutral,
    }
    return render(request, "crush_lu/connection_detail.html", context)


@crush_login_required
@require_http_methods(["GET"])
def connection_messages(request, connection_id):
    """HTMX polling endpoint: the rendered message list for a connection.

    Returns the same list the page renders inline, so the thread picks up the
    other member's replies without a reload. Status 286 tells HTMX to stop
    polling (dead connection or blocked pair).
    """
    connection = get_object_or_404(
        EventConnection,
        Q(requester=request.user) | Q(recipient=request.user),
        id=connection_id,
    )

    from .services.blocking import is_blocked_pair

    is_requester = connection.requester == request.user
    other_user = connection.recipient if is_requester else connection.requester

    # The recipient of a private crush must not learn the row exists — and a
    # 286 here would tell them, because an unrelated id 404s. This endpoint is
    # a GET with no rate limit, so the difference is enumerable: walk the ids
    # and every 286 marks a hidden admirer. Same 404 as `connection_detail`.
    crush_lead_neutral = (
        connection.flow == EventConnection.FLOW_CRUSH
        and connection.status != "shared"
    )
    if crush_lead_neutral and not is_requester:
        raise Http404

    if connection.status == "declined" or is_blocked_pair(request.user, other_user):
        return HttpResponse(status=286)

    # Messaging locked for crush leads in every pre-`shared` status (§7) —
    # enforced server-side, byte-identical to a dead connection. The
    # requester keeps 286 so their own poll stops cleanly.
    if crush_lead_neutral:
        return HttpResponse(status=286)

    msgs = _approved_messages(connection)
    return render(
        request,
        "crush_lu/_connection_messages_list.html",
        {"messages": msgs, "connection": connection},
    )
