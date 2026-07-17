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

from .forms import EncounterRemovalRequestForm, EncounterRemovalReviewForm
from .services import (
    LobbyAccessError,
    acknowledge_consent,
    confirm_event_meeting,
    event_phase,
    get_authorized_photo,
    get_confirmation_target,
    get_people_met_photo,
    get_recap_confirmation_target,
    list_live_participants,
    list_people_met,
    list_recap_participants,
    lobby_state,
    people_met_profile,
    recap_state,
    review_encounter_removal_request,
    reviewable_removal_requests,
    send_meet_signal,
    submit_encounter_removal_request,
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
    if event_phase(event) == "recap":
        return redirect("crush_lu:event_lobby:recap", event_id=event.pk)
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


@login_required
@never_cache
@require_GET
def recap(request, event_id):
    event = get_object_or_404(MeetupEvent, pk=event_id)
    try:
        state = recap_state(event, request.user)
        participants = list_recap_participants(event, request.user)
    except LobbyAccessError as error:
        return _handle_page_error(request, error, event_id)
    return _private(
        render(
            request,
            "crush_event_lobby/recap.html",
            {"event": event, "state": state, "participants": participants},
        )
    )


@login_required
@never_cache
@require_GET
def recap_participants_api(request, event_id):
    event = get_object_or_404(MeetupEvent, pk=event_id)
    try:
        participants = list_recap_participants(event, request.user)
    except LobbyAccessError:
        return _unavailable_json()
    return _private(JsonResponse({"participants": participants}))


@login_required
@never_cache
@require_GET
def confirm_meeting(request, event_id, handle):
    event = get_object_or_404(MeetupEvent, pk=event_id)
    try:
        target = get_recap_confirmation_target(event, request.user, handle)
    except LobbyAccessError as error:
        return _handle_page_error(request, error, event_id)
    return _private(
        render(
            request,
            "crush_event_lobby/confirm_meeting.html",
            {"event": event, "target": target},
        )
    )


@login_required
@never_cache
@require_POST
def meeting_confirmation(request, event_id, handle):
    event = get_object_or_404(MeetupEvent, pk=event_id)
    try:
        result = confirm_event_meeting(event, request.user, handle)
    except LobbyAccessError as error:
        if error.code == "already_met":
            messages.info(request, "You've already met this person.")
            return redirect("crush_lu:event_lobby:recap", event_id=event.pk)
        return _handle_page_error(request, error, event_id)

    if result.mutual:
        messages.success(
            request,
            f"{result.first_name} was added to People I've Met.",
        )
    elif result.created:
        messages.success(
            request,
            "Meeting confirmed anonymously. This cannot be undone.",
        )
    else:
        messages.info(request, "You already confirmed meeting this person.")
    return redirect("crush_lu:event_lobby:recap", event_id=event.pk)


@login_required
@never_cache
@require_GET
def people_met(request):
    try:
        encounters = list_people_met(request.user)
    except LobbyAccessError as error:
        return _handle_page_error(request, error)
    return _private(
        render(
            request,
            "crush_event_lobby/people_met.html",
            {"encounters": encounters},
        )
    )


@login_required
@never_cache
@require_GET
def people_met_member(request, handle):
    try:
        context = people_met_profile(request.user, handle)
    except LobbyAccessError as error:
        return _handle_page_error(request, error)
    return _private(
        render(request, "crush_event_lobby/people_met_profile.html", context)
    )


@login_required
@never_cache
@require_http_methods(["GET", "POST"])
def request_people_met_removal(request, handle):
    try:
        context = people_met_profile(request.user, handle)
    except LobbyAccessError as error:
        return _handle_page_error(request, error)
    form = EncounterRemovalRequestForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            submit_encounter_removal_request(
                request.user,
                handle,
                form.cleaned_data["reason"],
                form.cleaned_data["details"],
            )
        except LobbyAccessError as error:
            return _handle_page_error(request, error)
        messages.success(
            request,
            "The encounter is now hidden for both people and your private request was sent for review.",
        )
        return redirect("crush_lu:event_lobby:people_met")
    context["form"] = form
    return _private(render(request, "crush_event_lobby/request_removal.html", context))


def _is_removal_reviewer(user):
    coach = getattr(user, "crushcoach", None)
    if coach is not None:
        return user.is_active and coach.is_active
    return user.is_active and user.is_staff


@login_required
@never_cache
@require_GET
def removal_reviews(request):
    if not _is_removal_reviewer(request.user):
        raise Http404("Review queue is not available")
    pending = reviewable_removal_requests(request.user).filter(status="pending")
    review_items = [
        {"request": removal_request, "form": EncounterRemovalReviewForm()}
        for removal_request in pending
    ]
    return _private(
        render(
            request,
            "crush_event_lobby/removal_reviews.html",
            {"review_items": review_items},
        )
    )


@login_required
@never_cache
@require_POST
def review_removal(request, request_id):
    if not _is_removal_reviewer(request.user):
        raise Http404("Review queue is not available")
    form = EncounterRemovalReviewForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Choose an outcome and add private resolution notes.")
        return redirect("crush_lu:event_lobby:removal_reviews")
    try:
        review_encounter_removal_request(
            request.user,
            request_id,
            form.cleaned_data["decision"],
            form.cleaned_data["resolution_notes"],
        )
    except LobbyAccessError as error:
        return _handle_page_error(request, error)
    messages.success(request, "The private removal decision was recorded.")
    return redirect("crush_lu:event_lobby:removal_reviews")


@login_required
@never_cache
@require_GET
def people_met_photo(request, handle, slot):
    try:
        photo = get_people_met_photo(request.user, handle, slot)
        photo.open("rb")
    except (LobbyAccessError, FileNotFoundError, OSError):
        raise Http404("Photo not available") from None
    content_type = mimetypes.guess_type(photo.name)[0] or "application/octet-stream"
    return _private(FileResponse(photo, content_type=content_type))
