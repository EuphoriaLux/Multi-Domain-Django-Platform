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
import os

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.conf import settings
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from .decorators import crush_login_required, ratelimit
from .models import EventRegistration, MeetupEvent
from .services.event_lobby import (
    PHASE_LIVE,
    eligible_participations,
    event_lobby_phase,
    get_mutuals,
    get_roster,
    handle_checkin,
    incoming_signal_count,
    lobby_feature_enabled,
    lobby_state,
    participant_gate,
    send_meet_signal,
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
        async_to_sync(channel_layer.group_send)(
            group, {"type": msg_type, "data": data}
        )
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
    # while the live phase lasts — never retroactively (§5.3).
    participation, created = handle_checkin(registration)
    if created:
        broadcast_participant_joined(event.pk)

    if phase != PHASE_LIVE or participation is None:
        # PROTOTYPE-STUB: §7.7–§7.8 — the 48-hour recap grid, meeting
        # confirmations, and People I've Met are outside this prototype
        # slice; after the live phase we render a phase notice instead.
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
    payload = {
        "ok": True,
        "state": lobby_state(request.user, event, now),
    }
    if phase == PHASE_LIVE:
        payload["roster"] = get_roster(request.user, event)
        payload["mutuals"] = get_mutuals(request.user, event)
    else:
        # §7.6: at the exact end the live roster is gone; clients flip to the
        # ended state. PROTOTYPE-STUB: the recap payload (§7.7) goes here.
        payload["roster"] = []
        payload["mutuals"] = []
    response = JsonResponse(payload)
    response["Cache-Control"] = "private, no-store"
    return response


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

    if target.user_id != request.user.pk and is_blocked_pair(
        request.user, target.user
    ):
        # Indistinguishable from an unknown handle — blocks aren't probeable.
        raise Http404("Photo not found")

    photo = target.user.crushprofile.photo_1
    if not photo:
        raise Http404("Photo not found")

    # Azure blob storage: redirect to a time-limited SAS URL (mirrors
    # views_media.serve_profile_photo).
    if getattr(settings, "AZURE_ACCOUNT_NAME", None):
        from .storage import CrushProfilePhotoStorage

        storage = CrushProfilePhotoStorage()
        secure_url = storage.url(photo.name, expire=1800)
        return redirect(secure_url)

    # Local filesystem (dev): stream the file.
    photo_path = photo.path
    if not os.path.exists(photo_path):
        raise Http404("Photo file not found")
    try:
        with open(photo_path, "rb") as f:
            content_type = "image/jpeg"
            if photo_path.lower().endswith(".png"):
                content_type = "image/png"
            elif photo_path.lower().endswith(".webp"):
                content_type = "image/webp"
            response = HttpResponse(f.read(), content_type=content_type)
            response["Content-Disposition"] = "inline"
            # §13: keep lobby photos out of shared caches; short private
            # cache is the practical trade-off for a photo grid.
            response["Cache-Control"] = "private, max-age=300"
            return response
    except OSError:
        logger.error("Error serving lobby photo %s", photo_path)
        raise Http404("Error loading photo")
