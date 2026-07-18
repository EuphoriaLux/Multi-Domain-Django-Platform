"""
Crush Connect Event Lobby views — the protected live photo grid.

Spec: docs/superpowers/specs/2026-07-17-crush-connect-event-lobby-design.md
(§7 UX, §11 HTTP/realtime contract, §13 privacy invariants).

All business rules live in ``services.event_lobby``; these views only
authenticate, authorize, shape JSON, and broadcast refetch hints. Identity
contract: every client-visible payload addresses participants by their opaque
event-scoped handle; first names appear only for the viewer's own authorized
mutual reveals. WebSocket broadcasts carry no identity at all — they are
refetch hints, never the source of truth (§11.1).
"""

import json
import logging
import mimetypes

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.contrib import messages
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET, require_POST

from .decorators import crush_login_required, ratelimit
from .models import (
    ConfirmedEncounterRemovalRequest,
    EventLobbyParticipation,
    EventRegistration,
    MeetupEvent,
)
from .services.event_lobby import (
    LobbyAccessError,
    PHASE_LIVE,
    PHASE_RECAP,
    confirm_meeting,
    eligible_participations,
    encounter_counterpart,
    event_lobby_phase,
    get_mutuals,
    get_people_ive_met,
    get_recap_roster,
    get_roster,
    handle_checkin,
    hidden_encounter_user_ids,
    incoming_confirmation_count,
    incoming_signal_count,
    lobby_feature_enabled,
    lobby_state,
    participant_gate,
    recap_state,
    send_meet_signal,
    submit_encounter_removal_request,
    viewer_participation,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Broadcast helpers — sanitized refetch hints only (§11.1)
# ---------------------------------------------------------------------------


def _group_send(group, msg_type, data):
    channel_layer = get_channel_layer()
    if not channel_layer:
        return
    try:
        async_to_sync(channel_layer.group_send)(group, {"type": msg_type, "data": data})
    except Exception:
        logger.exception("Failed to broadcast %s to %s", msg_type, group)


def broadcast_participant_joined(event_id, onboarded=False):
    """Event-wide neutral join hint (§7.5): no identity, clients refetch. The
    ``onboarded`` flag only selects which neutral copy the client shows."""
    _group_send(
        f"event_lobby_{event_id}",
        "lobby.joined",
        {"onboarded": bool(onboarded)},
    )


def _notify_lobby_user(event_id, user_id, msg_type, data):
    """Private per-user hint (counter changes, mutual reveal) — §11.1."""
    _group_send(f"event_lobby_{event_id}_user_{user_id}", msg_type, data)


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


def _get_lobby_event(event_id):
    """Fetch the event without leaking lobby existence for bad ids (§5.3)."""
    if not lobby_feature_enabled():
        raise Http404("Event lobby not available")
    return get_object_or_404(
        MeetupEvent, pk=event_id, is_published=True, is_cancelled=False
    )


@crush_login_required
def event_lobby(request, event_id):
    """The lobby page (§7.2). Attendance is the hard wall: guests without an
    ``attended`` registration get a plain 404 and can never infer the lobby
    exists (§5.3). Checked-in members who fail the Connect gate see only the
    onboarding CTA — never the roster or any participant count."""
    event = _get_lobby_event(event_id)
    now = timezone.now()
    phase = event_lobby_phase(event, now)

    registration = EventRegistration.objects.filter(
        event=event, user=request.user, status="attended"
    ).first()
    if registration is None:
        raise Http404("Event lobby not available")

    ok, gate_reason = participant_gate(request.user)
    if not ok:
        return render(
            request,
            "crush_lu/event_lobby/lobby_locked.html",
            {"event": event, "gate_reason": gate_reason, "phase": phase},
        )

    # Idempotent self-heal (§10: evaluate/create participation is idempotent):
    # covers members whose check-in predates the feature rollout. Creates only
    # while the live phase lasts — never retroactively (§5.3). During recap it
    # returns None, so read the member's frozen participation directly.
    participation, created = handle_checkin(registration)
    if created:
        broadcast_participant_joined(event.pk)
    if participation is None:
        participation = EventLobbyParticipation.objects.filter(
            event=event, user=request.user
        ).first()

    # Recap phase (§7.7): the live lobby is closed, but the member joined
    # before the exact end so their frozen participation grants recap access.
    if phase == PHASE_RECAP and participation is not None:
        context = {
            "event": event,
            "participation": participation,
            "state": recap_state(request.user, event, now),
            "roster": get_recap_roster(request.user, event),
            "ws_path": f"/ws/event-lobby/{event.pk}/",
        }
        response = render(request, "crush_lu/event_lobby/recap.html", context)
        response["Cache-Control"] = "private, no-store"
        return response

    if phase != PHASE_LIVE or participation is None:
        # Closed, or a member who never joined before the end (§5.3).
        return render(
            request,
            "crush_lu/event_lobby/lobby_closed.html",
            {"event": event, "phase": phase},
        )

    state = lobby_state(request.user, event, now)
    context = {
        "event": event,
        "participation": participation,
        "state": state,
        "roster": get_roster(request.user, event),
        "mutuals": get_mutuals(request.user, event),
        "ws_path": f"/ws/event-lobby/{event.pk}/",
    }
    response = render(request, "crush_lu/event_lobby/lobby.html", context)
    # §13: the roster must never be cacheable.
    response["Cache-Control"] = "private, no-store"
    return response


@crush_login_required
def people_ive_met(request):
    """The permanent "People I've Met" collection (§7.8). One flat
    chronological list; current photo + first name only. Non-participants of
    the feature (or deactivated members) simply see an empty collection."""
    if not lobby_feature_enabled():
        raise Http404("Not found")
    entries = get_people_ive_met(request.user)
    response = render(
        request,
        "crush_lu/event_lobby/people_ive_met.html",
        {"people": entries},
    )
    response["Cache-Control"] = "private, no-store"
    return response


@crush_login_required
def event_lobby_person(request, user_id):
    """The full current Crush Connect profile reached from a People I've Met
    entry (§7.8). Authorized by an active permanent encounter — never an
    unguessable id (§13). Opens no chat, contact request, or Spark."""
    if not lobby_feature_enabled():
        raise Http404("Not found")
    other = encounter_counterpart(request.user, user_id)
    if other is None:
        raise Http404("Not found")
    membership = getattr(other, "crush_connect_membership", None)
    response = render(
        request,
        "crush_lu/event_lobby/person_profile.html",
        {
            "person": other,
            "profile": other.crushprofile,
            "membership": membership,
            "removal_reasons": ConfirmedEncounterRemovalRequest.REASON_CHOICES,
        },
    )
    response["Cache-Control"] = "private, no-store"
    return response


@crush_login_required
@ratelimit(key="user", rate="200/m", method="GET")
@require_GET
def event_lobby_person_photo(request, user_id):
    """Serve a People I've Met photo through current pair authorization.

    Unlike the generic profile-photo route, this endpoint rechecks the active
    encounter, blocks/removals, and both members' current Connect eligibility
    on every request. The image is proxied instead of redirected to a durable
    storage URL and is never cacheable after a safety state change.
    """
    if not lobby_feature_enabled():
        raise Http404("Photo not found")
    other = encounter_counterpart(request.user, user_id)
    if other is None:
        raise Http404("Photo not found")

    photo = other.crushprofile.photo_1
    if not photo:
        raise Http404("Photo not found")
    try:
        photo.open("rb")
        content = photo.read()
    except Exception:
        logger.exception("Error serving encounter-authorized profile photo")
        raise Http404("Photo not found") from None
    finally:
        photo.close()

    content_type = mimetypes.guess_type(photo.name)[0] or "application/octet-stream"
    response = HttpResponse(content, content_type=content_type)
    response["Content-Disposition"] = "inline"
    response["Cache-Control"] = "private, no-store"
    return response


@crush_login_required
@ratelimit(key="user", rate="10/h", method="POST")
@require_POST
def event_lobby_remove_person(request, user_id):
    """Immediately hide an encounter and open its private Support review."""
    if not lobby_feature_enabled():
        raise Http404("Not found")

    try:
        submit_encounter_removal_request(
            request.user,
            str(user_id),
            request.POST.get("reason", ""),
            request.POST.get("details", ""),
        )
    except LobbyAccessError as exc:
        if exc.code == "invalid_reason":
            messages.error(request, _("Please select a valid reason."))
            return redirect("crush_lu:event_lobby_person", user_id=user_id)
        raise Http404("Not found") from None

    messages.success(
        request,
        _("This person is now hidden. Support will review your private request."),
    )
    return redirect("crush_lu:event_lobby_people")


# ---------------------------------------------------------------------------
# JSON APIs (called from event-lobby.js via {% url %} data attributes)
# ---------------------------------------------------------------------------


@crush_login_required
@ratelimit(key="user", rate="120/m", method="GET")
@require_GET
def lobby_state_api(request, event_id):
    """Authoritative state + roster + mutuals for the polling fallback and
    post-broadcast refetches (§11). Never contains pre-mutual identity."""
    event = _get_lobby_event(event_id)
    participation = viewer_participation(request.user, event)
    if participation is None:
        return JsonResponse({"ok": False, "error": "forbidden"}, status=403)

    now = timezone.now()
    phase = event_lobby_phase(event, now)
    if phase == PHASE_LIVE:
        payload = {
            "ok": True,
            "state": lobby_state(request.user, event, now),
            "roster": get_roster(request.user, event),
            "mutuals": get_mutuals(request.user, event),
        }
    elif phase == PHASE_RECAP:
        # §7.6: at the exact end the live roster is replaced by the recap grid.
        payload = {
            "ok": True,
            "state": recap_state(request.user, event, now),
            "roster": get_recap_roster(request.user, event),
            "mutuals": [],
        }
    else:
        # §13/§18: once the recap closes (or the event is cancelled) the
        # feature stops answering — expired quotas and anonymous counters
        # must not stay visible to ex-participants.
        payload = {
            "ok": True,
            "state": {"phase": phase},
            "roster": [],
            "mutuals": [],
        }
    response = JsonResponse(payload)
    response["Cache-Control"] = "private, no-store"
    return response


@crush_login_required
@ratelimit(key="user", rate="60/m", method="POST")
@require_POST
def lobby_confirm_api(request, event_id):
    """Confirm a real-world meeting during the recap (§7.7). CSRF-protected
    POST; the server re-derives phase inside the service transaction."""
    event = _get_lobby_event(event_id)

    try:
        data = json.loads(request.body)
        handle = str(data["handle"])
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return JsonResponse({"ok": False, "error": "bad_payload"}, status=400)

    result = confirm_meeting(request.user, event, handle)
    outcome = result.get("result")

    if outcome in ("feature_disabled", "not_participant"):
        return JsonResponse({"ok": False, "error": outcome}, status=403)
    if outcome == "unknown_participant":
        return JsonResponse({"ok": False, "error": outcome}, status=404)

    recipient_user_id = result.pop("recipient_user_id", None)
    if recipient_user_id is not None and outcome == "confirmed":
        _push_confirmation_counter(event, recipient_user_id)
    elif recipient_user_id is not None and outcome == "encounter":
        # Private hint; the counterpart's client refetches authoritative state.
        _notify_lobby_user(event.pk, recipient_user_id, "lobby.encounter", {})

    result["ok"] = True
    response = JsonResponse(result)
    response["Cache-Control"] = "private, no-store"
    return response


def _push_confirmation_counter(event, recipient_user_id):
    """Push the recipient's fresh exact anonymous confirmation count (§7.7)."""
    from django.contrib.auth.models import User

    try:
        recipient = User.objects.get(pk=recipient_user_id)
    except User.DoesNotExist:
        return
    _notify_lobby_user(
        event.pk,
        recipient_user_id,
        "lobby.counter",
        {"incoming_confirmations": incoming_confirmation_count(recipient, event)},
    )


@crush_login_required
@ratelimit(key="user", rate="30/m", method="POST")
@require_POST
def lobby_signal_api(request, event_id):
    """Send one irrevocable meet signal (§7.3). CSRF-protected POST; the
    server re-derives phase/quota inside the service transaction (§6)."""
    event = _get_lobby_event(event_id)

    try:
        data = json.loads(request.body)
        handle = str(data["handle"])
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return JsonResponse({"ok": False, "error": "bad_payload"}, status=400)

    result = send_meet_signal(request.user, event, handle)
    outcome = result.get("result")

    if outcome in ("feature_disabled", "not_participant"):
        return JsonResponse({"ok": False, "error": outcome}, status=403)
    if outcome == "unknown_participant":
        return JsonResponse({"ok": False, "error": outcome}, status=404)

    # recipient_user_id is server-side routing data only — it must never reach
    # the client (§13).
    recipient_user_id = result.pop("recipient_user_id", None)

    if recipient_user_id is not None and outcome == "sent":
        _push_counter_update(event, recipient_user_id)
    elif recipient_user_id is not None and outcome == "mutual":
        # Private hint; the recipient's client refetches and renders the
        # reveal from the authoritative state API (§11.1 — no identity in
        # broadcast payloads).
        _notify_lobby_user(event.pk, recipient_user_id, "lobby.mutual", {})

    result["ok"] = True
    response = JsonResponse(result)
    response["Cache-Control"] = "private, no-store"
    return response


def _push_counter_update(event, recipient_user_id):
    """Push the recipient's fresh exact anonymous count privately (§7.4)."""
    from django.contrib.auth.models import User

    try:
        recipient = User.objects.get(pk=recipient_user_id)
    except User.DoesNotExist:
        return
    _notify_lobby_user(
        event.pk,
        recipient_user_id,
        "lobby.counter",
        {"incoming_count": incoming_signal_count(recipient, event)},
    )


# ---------------------------------------------------------------------------
# Roster-authorized photo serving (§7.2 / §13)
# ---------------------------------------------------------------------------


@crush_login_required
@ratelimit(key="user", rate="200/m", method="GET")
@require_GET
def lobby_photo(request, event_id, handle):
    """Serve a participant's current primary photo, authorizing the REQUESTING
    VIEWER against the event roster (§7.2) — never an unguessable URL alone.
    Addressed by opaque handle so no durable user id appears in the URL (§13,
    unlike the general ``serve_profile_photo`` route)."""
    event = _get_lobby_event(event_id)

    if viewer_participation(request.user, event) is None:
        raise Http404("Photo not found")
    # Photos stay retrievable through live AND recap (the recap grid is
    # photo-only, §7.7); everything closes with the recap window.
    if event_lobby_phase(event) not in (PHASE_LIVE, "recap"):
        raise Http404("Photo not found")

    target = eligible_participations(event).filter(handle=handle).first()
    if target is None:
        raise Http404("Photo not found")
    from .services.blocking import is_blocked_pair

    if target.user_id != request.user.pk and (
        is_blocked_pair(request.user, target.user)
        or target.user_id in hidden_encounter_user_ids(request.user)
    ):
        # Indistinguishable from an unknown handle — blocks aren't probeable.
        raise Http404("Photo not found")

    photo = target.user.crushprofile.photo_1
    if not photo:
        raise Http404("Photo not found")

    # Proxy the image through this authorized view instead of redirecting to
    # a reusable storage URL: an Azure SAS link would keep working for its
    # whole lifetime after a block/exclusion/removal or an attendance
    # correction, defeating immediate revocation (§13). Mirrors
    # ``event_lobby_person_photo``; works for both local and blob storage.
    try:
        photo.open("rb")
        content = photo.read()
    except Exception:
        logger.exception("Error serving lobby photo for event %s", event.pk)
        raise Http404("Photo not found") from None
    finally:
        photo.close()

    content_type = mimetypes.guess_type(photo.name)[0] or "application/octet-stream"
    response = HttpResponse(content, content_type=content_type)
    response["Content-Disposition"] = "inline"
    # §13: private browser micro-cache only — every new request re-authorizes,
    # and no shareable URL ever leaves the server.
    response["Cache-Control"] = "private, max-age=300"
    return response
