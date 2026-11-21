"""
Connection-related views for Crush.lu
Handles event attendee connections, connection requests, and messaging
"""

import re

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.http import Http404, HttpResponse
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.db import transaction
from django.db.models import Q
from django.views.decorators.http import require_http_methods
import logging

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

logger = logging.getLogger(__name__)

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
    """Post-event swipe deck (decision 2026-11-20, replaces "My Crush!").

    Same URL and the same gates as the old named attendee list, so every
    existing link (dashboard, recap email, push) lands here: the member must
    have attended and be verified, the deck opens once the event ends and
    closes with the event's connection window.
    """
    from .services import event_swipes

    event = get_object_or_404(MeetupEvent, id=event_id)

    user_registration = get_object_or_404(
        EventRegistration, event=event, user=request.user, status="attended"
    )

    if not user_registration.can_make_connections:
        messages.error(
            request, _("You must attend this event before making connections.")
        )
        return redirect("crush_lu:event_detail", event_id=event_id)

    # Live overlap gate (decision 2026-07-18): live-time socializing belongs
    # to the anonymous Event Lobby; the deck opens when the event ends.
    if timezone.now() < event.end_time:
        messages.info(
            request,
            _(
                "Swiping opens once the event ends. During the event, the "
                "Event Lobby is where Crush Connect members quietly signal "
                "who they'd like to meet."
            ),
        )
        return redirect("crush_lu:event_detail", event_id=event_id)

    mode, campaign = event_swipes.swipe_mode(event)
    if mode is None:
        messages.info(
            request,
            _(
                "Swiping for this event has closed. "
                "Your matches are always available here."
            ),
        )
        return redirect("crush_lu:my_connections")
    if mode == event_swipes.MODE_CAMPAIGN and event_swipes.hidden_from_past_decks(
        request.user
    ):
        return redirect("crush_lu:past_event_swipes")

    deck = event_swipes.swipe_deck(request.user, event)
    total = event_swipes.deck_size(request.user, event)

    matches = [
        {
            "connection": conn,
            "other": (
                conn.recipient
                if conn.requester_id == request.user.id
                else conn.requester
            ),
        }
        for conn in event_swipes.matches_for_user(request.user, event)
    ]
    for match in matches:
        match["profile"] = getattr(match["other"], "crushprofile", None)

    from .services.event_lobby import lobby_cta

    context = {
        "event": event,
        "event_lobby_cta": lobby_cta(
            request.user, event, registration=user_registration
        ),
        "deck": deck,
        "deck_total": total,
        "deck_done": total - len(deck),
        "matches": matches,
        "connection_window_active": True,
        "connection_window_deadline": (
            campaign.ends_at if campaign else event.connection_window_deadline
        ),
        "swipe_campaign": campaign,
        "own_profile": getattr(request.user, "crushprofile", None),
    }
    return render(request, "crush_lu/event_attendees.html", context)


@crush_login_required
def past_event_swipes(request):
    """December campaign hub: the member's past events, each with its deck.

    Also carries the "don't show me in past-event decks" switch, which the
    campaign messages link to.
    """
    from .models import PastEventDeckOptOut
    from .services import event_swipes

    campaign = event_swipes.active_campaign()
    hidden = PastEventDeckOptOut.objects.filter(user=request.user).exists()
    on_break = bool(
        getattr(getattr(request.user, "crushprofile", None), "on_break_at", None)
    )
    rows = []
    if campaign is not None and not hidden and not on_break:
        rows = event_swipes.past_event_decks(request.user, campaign)
    return render(
        request,
        "crush_lu/past_event_swipes.html",
        {
            "campaign": campaign,
            "rows": rows,
            "hidden": hidden,
            "on_break": on_break,
            "remaining_total": sum(row["remaining"] for row in rows),
        },
    )


@crush_login_required
@require_http_methods(["POST"])
@ratelimit(key="user", rate="30/h", method="POST")
def past_event_swipes_visibility(request):
    """Show or hide the member in past-event (campaign) decks."""
    from .models import PastEventDeckOptOut

    if request.POST.get("visible") == "1":
        PastEventDeckOptOut.objects.filter(user=request.user).delete()
        messages.success(request, _("You appear in past-event decks again."))
    else:
        PastEventDeckOptOut.objects.get_or_create(user=request.user)
        messages.success(
            request,
            _("Done. Nobody will see you in past-event decks any more."),
        )
    return redirect("crush_lu:past_event_swipes")


def _wants_json(request):
    return request.headers.get(
        "x-requested-with"
    ) == "XMLHttpRequest" or "application/json" in request.headers.get("accept", "")


@crush_login_required
@require_http_methods(["POST"])
@ratelimit(key="user", rate="300/h", method="POST")
def event_swipe(request, event_id, user_id):
    """Record a like/pass from the deck; JSON for the card UI, redirect otherwise."""
    from django.http import JsonResponse

    from .services import event_swipes
    from .services.event_swipes import (
        SwipeNotAllowed,
        record_swipe,
        swipe_error_message,
    )

    event = get_object_or_404(MeetupEvent, id=event_id)
    target_profile = (
        CrushProfile.objects.filter(user_id=user_id).select_related("user").first()
    )
    decision = request.POST.get("decision", "")

    error = None
    result = None
    if decision not in ("like", "pass"):
        error = _("Choose like or pass.")
    elif target_profile is None:
        error = _("This person is not available.")
    else:
        try:
            result = record_swipe(
                request.user,
                event,
                target_profile.user,
                liked=decision == "like",
                request=request,
            )
        except SwipeNotAllowed as exc:
            error = swipe_error_message(exc.code)

    if _wants_json(request):
        if error:
            return JsonResponse({"ok": False, "error": str(error)}, status=400)
        payload = {"ok": True, "match": result["match"]}
        if result["match"]:
            payload["match_name"] = target_profile.display_name
            url_name, url_kwargs = event_swipes.match_chat_route(result["connection"])
            payload["chat_url"] = reverse(url_name, kwargs=url_kwargs)
        return JsonResponse(payload)

    if error:
        messages.error(request, error)
    elif result["match"]:
        messages.success(
            request,
            _("It's a match with %(name)s! Your chat is open.")
            % {"name": target_profile.display_name},
        )
    return redirect("crush_lu:event_attendees", event_id=event_id)


def _crush_requests_replaced(request, event_id):
    """ "My Crush!" requests were replaced by swiping (decision 2026-11-20).

    The old URLs stay so stale pages and bookmarks do not 404: they create
    nothing and send the member to the swipe deck. Existing crush leads keep
    their coach workflow untouched.
    """
    target = reverse("crush_lu:event_attendees", kwargs={"event_id": event_id})
    if request.headers.get("HX-Request"):
        response = HttpResponse(status=204)
        response["HX-Redirect"] = target
        return response
    messages.info(
        request, _("Swipe through the attendees — a mutual like opens a chat.")
    )
    return redirect(target)


@crush_login_required
def request_connection(request, event_id, user_id):
    """Retired "My Crush!" declaration endpoint — see ``_crush_requests_replaced``."""
    get_object_or_404(MeetupEvent, id=event_id)
    return _crush_requests_replaced(request, event_id)


@crush_login_required
def request_connection_inline(request, event_id, user_id):
    """Retired HTMX variant of ``request_connection``."""
    get_object_or_404(MeetupEvent, id=event_id)
    return _crush_requests_replaced(request, event_id)


@crush_login_required
def connection_actions(request, event_id, user_id):
    """Retired HTMX fragment of the old attendee list — see ``_crush_requests_replaced``."""
    get_object_or_404(MeetupEvent, id=event_id)
    return _crush_requests_replaced(request, event_id)


def _refresh_if_from_dashboard(request, response, message=None, level=messages.SUCCESS):
    """Reload the dashboard after an inline accept/decline.

    The dashboard embeds the same request card as My Connections (UX Wave 3,
    finding 5-02), but its counts, header link and stats row sit outside the
    swapped card. A full refresh keeps them honest, and the queued message
    confirms the action on the reloaded page.
    """
    from urllib.parse import urlparse

    path = urlparse(request.headers.get("HX-Current-URL", "")).path
    if path.rstrip("/").endswith("/dashboard"):
        if message:
            messages.add_message(request, level, message)
        response["HX-Refresh"] = "true"
    return response


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
    if connection.flow == EventConnection.FLOW_CRUSH and connection.status != "shared":
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
    from .services.event_lobby import hidden_encounter_user_ids

    # A safety-removed encounter (removal_pending / removed) is treated like a
    # block: the pair stays mutually invisible, so it can't be accepted here.
    if is_blocked_pair(
        request.user, connection.requester
    ) or connection.requester_id in hidden_encounter_user_ids(request.user):
        if request.headers.get("HX-Request"):
            # A block placed after the dashboard rendered leaves its header,
            # badge and stats stale: refresh it like the other outcomes.
            unavailable = _("This connection is no longer available.")
            return _refresh_if_from_dashboard(
                request,
                render(request, "crush_lu/_htmx_error.html", {"message": unavailable}),
                unavailable,
                messages.ERROR,
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
            return _refresh_if_from_dashboard(
                request,
                render(
                    request,
                    "crush_lu/_attendee_connection_response.html",
                    {
                        "attendee": attendee,
                        "action": (
                            "accept"
                            if connection.status
                            in (
                                "accepted",
                                "coach_reviewing",
                                "coach_approved",
                                "shared",
                            )
                            else "decline"
                        ),
                    },
                ),
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
            # Same-gender: skip coach review, auto-share. Email and phone
            # stay opt-in, chosen on the shared connection page (#1146).
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
            return _refresh_if_from_dashboard(
                request,
                render(
                    request,
                    "crush_lu/_connection_response.html",
                    {"connection": connection, "action": "accept"},
                ),
                (
                    _(
                        "Connection accepted! You can now see each other's profile details."
                    )
                    if connection.is_same_gender
                    else _(
                        "Connection accepted! A coach will help facilitate "
                        "your introduction."
                    )
                ),
            )
        if connection.is_same_gender:
            messages.success(
                request,
                _("Connection accepted! You can now see each other's profile details."),
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
            return _refresh_if_from_dashboard(
                request,
                render(
                    request,
                    "crush_lu/_connection_response.html",
                    {"connection": connection, "action": "decline"},
                ),
                _("Connection request declined."),
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
    # Hide connections whose counterpart is in a block pair with the viewer,
    # or behind a pending/approved encounter removal — those pairs stay
    # mutually invisible, as on the attendee list (and the photo gate
    # refuses their photos).
    from .services.blocking import blocked_user_ids
    from .services.event_lobby import hidden_encounter_user_ids

    blocked_ids = blocked_user_ids(request.user) | hidden_encounter_user_ids(
        request.user
    )

    # Sent requests
    sent = (
        EventConnection.objects.filter(requester=request.user)
        .exclude(recipient_id__in=blocked_ids)
        # A closed "My Crush!" lead promises nothing any more ("your coach
        # will call you"), so it leaves the list.
        .exclude(flow=EventConnection.FLOW_CRUSH, status="declined")
        .select_related("recipient__crushprofile", "event", "assigned_coach")
        # has_public_photo / profile_photo read both per card.
        .prefetch_related(
            "recipient__crushprofile__photo_review_states",
            "recipient__crushprofile__published_photos",
            "recipient__crushprofile__photo_framings",
        )
        .order_by("-requested_at")
    )

    # Received requests (pending only). Crush leads are private — they never
    # appear in the recipient's inbox with an Accept/Decline card.
    received_pending = (
        EventConnection.objects.filter(recipient=request.user, status="pending")
        .exclude(flow=EventConnection.FLOW_CRUSH)
        .exclude(requester_id__in=blocked_ids)
        .select_related("requester__crushprofile", "event")
        .prefetch_related(
            "requester__crushprofile__photo_review_states",
            "requester__crushprofile__published_photos",
            "requester__crushprofile__photo_framings",
        )
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
        .prefetch_related(
            "requester__crushprofile__photo_review_states",
            "requester__crushprofile__published_photos",
            "recipient__crushprofile__photo_review_states",
            "recipient__crushprofile__published_photos",
            "requester__crushprofile__photo_framings",
            "recipient__crushprofile__photo_framings",
        )
        .order_by("-requested_at")
    )

    # People I've Met cross-link — only when the member actually has confirmed
    # encounters (an empty collection link is noise for everyone else).
    from .services.event_lobby import get_people_ive_met, lobby_feature_enabled

    people_ive_met_count = (
        len(get_people_ive_met(request.user)) if lobby_feature_enabled() else 0
    )

    # Gates the empty state's secondary CTA (finding 5-14): offer Crush
    # Connect only to members the hub will actually let in, using the hub's
    # own gate so the link never bounces to the teaser.
    from .views_crush_connect import _hub_access_blocker

    is_verified = _hub_access_blocker(request.user) is None

    context = {
        "sent_requests": sent,
        "received_requests": received_pending,
        "active_connections": active,
        "people_ive_met_count": people_ive_met_count,
        "is_verified": is_verified,
    }
    return render(request, "crush_lu/my_connections.html", context)


EMAIL_OPT_IN_MIGRATION = "0255_eventconnection_recipient_shares_email_and_more"
PHONE_OPT_IN_MIGRATION = "0258_eventconnection_shares_phone_opt_in"


_OPT_IN_SINCE_CACHE = {}


def _opt_in_available_since(migration_name):
    """When this database gained the opt-in column: the migration's applied
    time, i.e. the deploy that started asking. None if it cannot be read
    (not cached, so a later recorder row is picked up without a restart)."""
    if migration_name in _OPT_IN_SINCE_CACHE:
        return _OPT_IN_SINCE_CACHE[migration_name]
    from django.db.migrations.recorder import MigrationRecorder

    applied = (
        MigrationRecorder.Migration.objects.filter(app="crush_lu", name=migration_name)
        .values_list("applied", flat=True)
        .first()
    )
    if applied is None:
        logger.warning("No applied time recorded for migration %s", migration_name)
    else:
        _OPT_IN_SINCE_CACHE[migration_name] = applied
    return applied


def _made_contact_choice(connection, side):
    """True once ``side`` has saved email/phone opt-ins, on the consent form
    or on the shared page (both log ``contact_choices``)."""
    return any(
        action.get("type") == "contact_choices"
        and (action.get("details") or {}).get("side") == side
        for action in connection.system_actions or []
    )


def _predates_choice(connection, migration_name):
    """True only when the row is known to have been shared before that
    contact choice existed (#1146). ``shared_at`` is the only record of the
    share time: the earlier consent path set just ``status``, and
    requested/responded/coach-approved times only bound the share from below,
    so a row without ``shared_at`` is never claimed to predate the choice."""
    since = _opt_in_available_since(migration_name)
    shared_at = connection.shared_at
    return since is not None and shared_at is not None and shared_at < since


def _save_contact_choices(request, connection, is_requester):
    """Write the member's own email/phone opt-ins on a shared connection."""
    side = "requester" if is_requester else "recipient"
    profile = getattr(request.user, "crushprofile", None)
    with transaction.atomic():
        locked = EventConnection.objects.select_for_update().get(pk=connection.pk)
        if locked.status != "shared":
            messages.info(
                request,
                _(
                    "This connection has already moved on — refresh the page to see its current status."
                ),
            )
            return
        setattr(locked, f"{side}_shares_email", "share_email" in request.POST)
        setattr(
            locked,
            f"{side}_shares_phone",
            "share_phone" in request.POST and bool(profile and profile.phone_number),
        )
        locked.log_system_action("contact_choices", actor="member", side=side)
        locked.save(
            update_fields=[
                f"{side}_shares_email",
                f"{side}_shares_phone",
                "system_actions",
            ]
        )
    messages.success(request, _("Saved"))


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
        connection.flow == EventConnection.FLOW_CRUSH and connection.status != "shared"
    )
    if crush_lead_neutral and not is_requester:
        raise Http404

    # Block guard: once either party blocks the other, the connection is dead.
    # An encounter removal hides the pair the same way.
    from .services.blocking import is_blocked_pair
    from .services.event_lobby import hidden_encounter_user_ids

    other_user = connection.recipient if is_requester else connection.requester
    if is_blocked_pair(
        request.user, other_user
    ) or other_user.pk in hidden_encounter_user_ids(request.user):
        messages.error(request, _("This connection is no longer available."))
        return redirect("crush_lu:my_connections")

    # A swipe match between two Crush Connect members chats in Connect
    # (decision 2026-11-20). While that chat is open it is the only one, so
    # the pair never splits a conversation across two pages.
    connect_invite = False
    if connection.flow == EventConnection.FLOW_SWIPE:
        from .services.event_swipes import live_connect_chat

        connect_chat = live_connect_chat(connection)
        if connect_chat is not None and request.method != "POST":
            return redirect(
                "crush_lu:connect_week_chat_detail", chat_id=connect_chat.pk
            )
        membership = getattr(request.user, "crush_connect_membership", None)
        connect_invite = not (membership and membership.is_onboarded)

    if request.method == "POST":
        # Crush leads: member-facing consent and chat stay closed in every
        # pre-`shared` status — consent is given verbally to the coach and
        # recorded coach-side (Phase D); there is no chat before the
        # introduction (§7 messaging lock, enforced server-side).
        if crush_lead_neutral:
            return redirect("crush_lu:connection_detail", connection_id=connection_id)

        # Contact choices on a shared connection (#1146, Decision D): the
        # coach share and the same-gender auto-share never pass the consent
        # form, so each member opts in to email and phone here, with the
        # same fields the consent form writes.
        if "contact_choices" in request.POST:
            _save_contact_choices(request, connection, is_requester)
            return redirect("crush_lu:connection_detail", connection_id=connection_id)

        # Handle consent
        if "consent" in request.POST:
            # WP10 review (finding 5-10/10-blocker): the consent step, and
            # its "Not now" exit, only ever make sense at `coach_approved` —
            # the same status the template's own `user_needs_consent` gate
            # requires before it shows this form at all. Without this guard
            # a stale page (another tab, a double-submit, or a page reopened
            # after the introduction already completed) could POST "not_now"
            # or a yes/no choice against a `shared` connection and force it
            # straight back to `declined`, undoing an introduction whose
            # contact info was already exchanged. No-op with a stale-page
            # notice instead.
            if connection.status != "coach_approved":
                messages.info(
                    request,
                    _(
                        "This connection has already moved on — refresh the page to see its current status."
                    ),
                )
                return redirect(
                    "crush_lu:connection_detail", connection_id=connection_id
                )

            consent_choice = request.POST.get("consent")

            # "Not now" (finding 5-10): a graceful exit from the consent step.
            # It quietly closes the lead — the other side is never notified
            # (mirrors the plain decline path, which also sends no
            # notification). There is currently no coach-facing view of a
            # quiet decline: coach_connections' "all" filter excludes
            # status="declined" outright, so this state is invisible to
            # coaches under every filter option today. Whether coaches
            # should see it — and how to tell it apart from a mutual
            # decline — is an open product question (see
            # findings_deferred), not something this fix invents.
            if consent_choice == "not_now":
                # Conditional update, not a blind save: a concurrent request
                # (another tab, or the coach/other party moving the
                # connection to `shared` between the status check above and
                # this write) must never be clobbered by a stale-tab
                # "not_now". The WHERE clause re-reads status atomically at
                # write time — if it no longer matches coach_approved, this
                # updates zero rows and the introduction stands.
                updated_rows = EventConnection.objects.filter(
                    pk=connection.pk, status="coach_approved"
                ).update(status="declined")
                if not updated_rows:
                    messages.info(
                        request,
                        _(
                            "This connection has already moved on — refresh the page to see its current status."
                        ),
                    )
                    return redirect(
                        "crush_lu:connection_detail", connection_id=connection_id
                    )
                messages.info(
                    request,
                    _(
                        "No problem — nothing was shared, and this connection is now closed."
                    ),
                )
                return redirect(
                    "crush_lu:connection_detail", connection_id=connection_id
                )

            consent_value = consent_choice == "yes"
            share_email = "share_email" in request.POST
            share_phone = "share_phone" in request.POST

            # Locked re-read, like "not_now" above: a stale save must never
            # restore coach_approved over a concurrent decline, and the switch
            # to `shared` re-checks both consents at write time. The choice
            # is logged so the shared page can say "chose not to" (#1146).
            side = "requester" if is_requester else "recipient"
            with transaction.atomic():
                locked = (
                    EventConnection.objects.filter(
                        pk=connection.pk, status="coach_approved"
                    )
                    .select_for_update()
                    .first()
                )
                if locked is not None:
                    setattr(locked, f"{side}_consents_to_share", consent_value)
                    setattr(locked, f"{side}_shares_email", share_email)
                    setattr(locked, f"{side}_shares_phone", share_phone)
                    locked.log_system_action(
                        "contact_choices", actor="member", side=side
                    )
                    fields = [
                        f"{side}_consents_to_share",
                        f"{side}_shares_email",
                        f"{side}_shares_phone",
                        "system_actions",
                    ]
                    shared = (
                        locked.requester_consents_to_share
                        and locked.recipient_consents_to_share
                    )
                    if shared:
                        locked.status = "shared"
                        locked.shared_at = timezone.now()
                        fields += ["status", "shared_at"]
                    locked.save(update_fields=fields)
            if locked is None:
                messages.info(
                    request,
                    _(
                        "This connection has already moved on — refresh the page to see its current status."
                    ),
                )
                return redirect(
                    "crush_lu:connection_detail", connection_id=connection_id
                )
            if shared:
                messages.success(
                    request, _("The details you each chose to share are now visible!")
                )
            else:
                messages.success(request, _("Your consent has been recorded."))

            return redirect("crush_lu:connection_detail", connection_id=connection_id)

        # Handle message sending
        elif "message" in request.POST:
            is_hx = bool(request.headers.get("HX-Request"))

            def _hx_inline_error(error_text):
                """finding 5-15: a failed send used to fall through to the
                plain redirect below even for HTMX, so htmx's `beforeend`
                swap onto #messages-container spliced the *entire* redirected
                page into the chat thread. Retargeting into
                #chat-compose-error keeps the failure inline, next to the
                textarea, without losing what the member typed."""
                response = render(
                    request,
                    "crush_lu/_connection_message_error.html",
                    {"error": error_text},
                )
                response["HX-Retarget"] = "#chat-compose-error"
                response["HX-Reswap"] = "innerHTML"
                return response

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

                    # First message in the thread? The response then also
                    # removes the "No messages yet" placeholder (finding 5-15).
                    is_first_message = not _approved_messages(connection).exists()

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
                    if is_hx:
                        response = render(
                            request,
                            "crush_lu/_connection_message.html",
                            {
                                "msg": new_message,
                                "is_own_message": True,
                                "remove_empty_placeholder": is_first_message,
                            },
                        )
                        # connection-chat.js resets the compose form on this
                        # event (never inline via hx-on::after-request, which
                        # would violate the site CSP once enforced) — and
                        # only on this event, so a retargeted inline error
                        # (also a 200) never clears what the member typed.
                        response["HX-Trigger"] = "connection-message-sent"
                        return response

                    messages.success(request, _("Message sent!"))
                else:
                    error_text = _("You can only message accepted connections.")
                    if is_hx:
                        return _hx_inline_error(error_text)
                    messages.error(request, error_text)
            else:
                # Literal 500 keeps the existing translation catalog entry intact;
                # CONNECTION_MESSAGE_MAX_LENGTH governs the actual cap above.
                error_text = _("Please enter a valid message (max 500 characters).")
                if is_hx:
                    return _hx_inline_error(error_text)
                messages.error(request, error_text)

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

    # Did the OTHER side opt in to sharing their phone number? Opt-in, default
    # off (UX Wave 5 WP4): phone, tel: link and WhatsApp all hang off this.
    other_shares_phone = bool(
        (
            connection.recipient_shares_phone
            if is_requester
            else connection.requester_shares_phone
        )
        and other_profile
        and other_profile.phone_number
    )

    # WhatsApp number (clean phone for wa.me link, only when shared AND opted in)
    whatsapp_number = ""
    if connection.status == "shared" and other_shares_phone:
        whatsapp_number = re.sub(r"[^\d+]", "", other_profile.phone_number)

    # Did the OTHER side choose to share their email? (finding 5-10: email is
    # opt-in at consent time, so it must not be assumed shared.)
    other_shares_email = (
        connection.recipient_shares_email
        if is_requester
        else connection.requester_shares_email
    )
    own_side = "requester" if is_requester else "recipient"
    other_side = "recipient" if is_requester else "requester"
    # "Chose not to share" only once the other side made a choice; until
    # then the page says the detail is not shared yet (#1146, decision D).
    is_shared = connection.status == "shared"
    other_has_chosen = is_shared and _made_contact_choice(connection, other_side)

    context = {
        "connection": connection,
        "is_requester": is_requester,
        "connect_invite": connect_invite,
        "other_user": other_user,
        "other_profile": other_profile,
        "messages": thread_messages,
        "can_message": can_message,
        "message_max_length": CONNECTION_MESSAGE_MAX_LENGTH,
        "user_needs_consent": user_needs_consent,
        "user_already_consented": user_already_consented,
        "whatsapp_number": whatsapp_number,
        "other_shares_email": other_shares_email,
        "other_shares_phone": other_shares_phone,
        "own_shares_email": getattr(connection, f"{own_side}_shares_email"),
        "own_shares_phone": getattr(connection, f"{own_side}_shares_phone"),
        "other_has_chosen": other_has_chosen,
        "other_email_predates_choice": (
            is_shared
            and not other_has_chosen
            and not other_shares_email
            and _predates_choice(connection, EMAIL_OPT_IN_MIGRATION)
        ),
        "other_phone_predates_choice": (
            is_shared
            and not other_has_chosen
            and not other_shares_phone
            and _predates_choice(connection, PHONE_OPT_IN_MIGRATION)
        ),
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
        connection.flow == EventConnection.FLOW_CRUSH and connection.status != "shared"
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
