import mimetypes

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from crush_lu.models import MeetupEvent

from .services import (
    LobbyAccessError,
    acknowledge_consent,
    get_authorized_photo,
    get_confirmation_target,
    list_live_participants,
    lobby_state,
    send_meet_signal,
)


def _private(response):
    response["Cache-Control"] = "private, no-store, max-age=0"
    response["Pragma"] = "no-cache"
    response["X-Content-Type-Options"] = "nosniff"
    return response


def _unavailable_json(status=404):
    return _private(JsonResponse({"detail": "not_available"}, status=status))


def _handle_page_error(request, error, event_id=None):
    if error.code == "consent_required":
        url = reverse("crush_lu:event_lobby:consent")
        if event_id is not None:
            url = f"{url}?event={event_id}"
        return redirect(url)
    raise Http404("Event Lobby is not available")


def _signal_rate_limited(user_id, event_id):
    key = f"event-lobby:signal-rate:{event_id}:{user_id}"
    try:
        if cache.add(key, 1, timeout=60):
            return False
        return cache.incr(key) > 20
    except Exception:
        # Database constraints and row locks remain authoritative if cache is down.
        return False


@login_required
@never_cache
@require_http_methods(["GET", "POST"])
def consent(request):
    event_id = request.GET.get("event") or request.POST.get("event")
    if request.method == "POST":
        try:
            acknowledge_consent(request.user)
        except LobbyAccessError as error:
            return _handle_page_error(request, error)
        if event_id and event_id.isdigit():
            return redirect("crush_lu:event_lobby:lobby", event_id=int(event_id))
        return redirect("crush_lu:crush_connect_hub")
    return _private(
        render(request, "crush_event_lobby/consent.html", {"event_id": event_id})
    )


@login_required
@never_cache
@require_GET
def lobby(request, event_id):
    event = get_object_or_404(MeetupEvent, pk=event_id)
    try:
        state = lobby_state(event, request.user)
        participants = list_live_participants(event, request.user)
    except LobbyAccessError as error:
        return _handle_page_error(request, error, event_id)
    return _private(
        render(
            request,
            "crush_event_lobby/lobby.html",
            {"event": event, "state": state, "participants": participants},
        )
    )


@login_required
@never_cache
@require_GET
def state_api(request, event_id):
    event = get_object_or_404(MeetupEvent, pk=event_id)
    try:
        payload = lobby_state(event, request.user)
    except LobbyAccessError:
        return _unavailable_json()
    return _private(JsonResponse(payload))


@login_required
@never_cache
@require_GET
def participants_api(request, event_id):
    event = get_object_or_404(MeetupEvent, pk=event_id)
    try:
        participants = list_live_participants(event, request.user)
    except LobbyAccessError:
        return _unavailable_json()
    return _private(JsonResponse({"participants": participants}))


@login_required
@never_cache
@require_GET
def participant_photo(request, event_id, handle):
    event = get_object_or_404(MeetupEvent, pk=event_id)
    try:
        photo = get_authorized_photo(event, request.user, handle)
        photo.open("rb")
    except (LobbyAccessError, FileNotFoundError, OSError):
        raise Http404("Photo not available") from None
    content_type = mimetypes.guess_type(photo.name)[0] or "application/octet-stream"
    return _private(FileResponse(photo, content_type=content_type))


@login_required
@never_cache
@require_GET
def confirm_signal(request, event_id, handle):
    event = get_object_or_404(MeetupEvent, pk=event_id)
    try:
        target = get_confirmation_target(event, request.user, handle)
        state = lobby_state(event, request.user)
    except LobbyAccessError as error:
        return _handle_page_error(request, error, event_id)
    return _private(
        render(
            request,
            "crush_event_lobby/confirm_signal.html",
            {"event": event, "target": target, "state": state},
        )
    )


@login_required
@never_cache
@require_POST
def signal(request, event_id, handle):
    event = get_object_or_404(MeetupEvent, pk=event_id)
    if _signal_rate_limited(request.user.pk, event.pk):
        return _unavailable_json(status=429)
    try:
        result = send_meet_signal(event, request.user, handle)
    except LobbyAccessError as error:
        if error.code == "signal_limit_reached":
            messages.warning(request, "You have used all three signals for this event.")
            return redirect("crush_lu:event_lobby:lobby", event_id=event.pk)
        return _handle_page_error(request, error, event_id)

    if result.mutual:
        messages.success(
            request,
            f"You and {result.first_name} would like to meet. Say hello now.",
        )
    elif result.created:
        messages.success(request, "Signal sent anonymously. It cannot be undone.")
    else:
        messages.info(request, "Your anonymous signal was already sent.")
    return redirect("crush_lu:event_lobby:lobby", event_id=event.pk)
