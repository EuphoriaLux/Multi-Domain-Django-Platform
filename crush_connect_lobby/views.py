import json
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponse, Http404
from django.shortcuts import render, get_object_or_404, redirect
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.views.decorators.http import require_POST
from django.views.decorators.cache import never_cache

from crush_lu.decorators import crush_login_required
from crush_lu.models import MeetupEvent, EventRegistration, CrushConnectMembership
from crush_connect_lobby.models import EventLobbyParticipation, ConfirmedEncounter, EventMeetSignal, EventMeetingConfirmation
from crush_connect_lobby import services


@crush_login_required
def event_lobby_home(request, event_id):
    """
    Renders the main event lobby page or handles consent acknowledgment.
    """
    # Check if global rollout flag is active (staff bypasses)
    is_launched = getattr(settings, "CRUSH_CONNECT_LAUNCHED", False)
    if not is_launched and not request.user.is_staff:
        # Show teaser / waitlist redirect
        return redirect("crush_lu:crush_connect_teaser")

    event = get_object_or_404(MeetupEvent, pk=event_id)
    user = request.user

    # Check if user is checked in (attended)
    reg = EventRegistration.objects.filter(event=event, user=user).first()
    if not reg or reg.status != "attended":
        return render(
            request,
            "crush_connect_lobby/not_eligible.html",
            {"reason": "You must be checked in at this event to join the lobby."},
            status=403
        )

    # Check if Connect membership exists. If not, redirect to Connect teaser or wizard.
    membership = getattr(user, "crush_connect_membership", None)
    if not membership or not membership.onboarded_at:
        return render(
            request,
            "crush_connect_lobby/onboarding_cta.html",
            {"event": event}
        )

    # Handle Coach exclusion
    if membership.excluded_by_coach:
        return render(
            request,
            "crush_connect_lobby/not_eligible.html",
            {"reason": "Your account is not eligible to join the Event Lobby."},
            status=403
        )

    # Check if updated lobby consent is accepted
    if not membership.lobby_consent_given:
        if request.method == "POST" and request.POST.get("accept_consent") == "true":
            membership.lobby_consent_given = True
            membership.save(update_fields=["lobby_consent_given"])
        else:
            return render(
                request,
                "crush_connect_lobby/consent_required.html",
                {"event": event}
            )

    # Derives phase. If closed, raise 404 or show closed message.
    phase = services.get_lobby_phase(event)
    if phase == "closed":
        return render(
            request,
            "crush_connect_lobby/lobby_closed.html",
            {"event": event}
        )

    # Idempotently join the lobby now that consent is given and registration is attended
    services.evaluate_and_join_lobby(user, event, source="checkin")

    # Serve the lobby dashboard template
    return render(
        request,
        "crush_connect_lobby/lobby.html",
        {
            "event": event,
            "phase": phase,
            "is_live": phase == "live",
            "is_recap": phase == "recap",
        }
    )


@crush_login_required
@never_cache
def api_lobby_state(request, event_id):
    """
    JSON state API for current event lobby.
    """
    event = get_object_or_404(MeetupEvent, pk=event_id)
    user = request.user

    if not services.check_eligibility(user, event):
        return JsonResponse({"error": "Unauthorized / Ineligible"}, status=403)

    phase = services.get_lobby_phase(event)
    event_end = event.date_time + timezone.timedelta(minutes=event.duration_minutes)
    recap_end = event_end + timezone.timedelta(hours=48)

    # Calculate remaining countdown seconds
    now = timezone.now()
    if phase == "live":
        countdown_secs = max(0, int((event_end - now).total_seconds()))
    elif phase == "recap":
        countdown_secs = max(0, int((recap_end - now).total_seconds()))
    else:
        countdown_secs = 0

    # Incoming signals/confirmations count (aggregate, anonymous)
    if phase == "live":
        incoming_count = EventMeetSignal.objects.filter(event=event, recipient=user).count()
        sent_signals = EventMeetSignal.objects.filter(event=event, sender=user).count()
        signals_left = max(0, 3 - sent_signals)
        recap_incoming_count = 0
    else:
        incoming_count = 0
        signals_left = 0
        recap_incoming_count = EventMeetingConfirmation.objects.filter(event=event, other_user=user).count()

    # Find if there are any new live mutuals to display a toast
    live_mutuals = []
    if phase == "live" or phase == "recap":
        mutual_signals = EventMeetSignal.objects.filter(
            event=event, sender=user, mutual_revealed_at__isnull=False
        ).select_related("recipient")
        for ms in mutual_signals:
            handle = services.generate_opaque_handle(ms.recipient, event)
            photo_url = reverse(
                "crush_connect_lobby:serve_participant_photo",
                kwargs={"event_id": event.id, "handle": handle}
            )
            live_mutuals.append({
                "first_name": ms.recipient.first_name,
                "handle": handle,
                "photo_url": photo_url,
            })

    return JsonResponse({
        "phase": phase,
        "countdown_seconds": countdown_secs,
        "incoming_signals_count": incoming_count,
        "incoming_confirmations_count": recap_incoming_count,
        "signals_left": signals_left,
        "live_mutuals": live_mutuals,
    })


@crush_login_required
@never_cache
def api_list_participants(request, event_id):
    """
    JSON API listing all participants formatted securely.
    """
    event = get_object_or_404(MeetupEvent, pk=event_id)
    user = request.user

    roster = services.list_participants(user, event)
    return JsonResponse({"participants": roster})


@crush_login_required
@require_POST
def api_send_signal(request, event_id):
    """
    API endpoint to send a live meet signal using opaque handle.
    """
    event = get_object_or_404(MeetupEvent, pk=event_id)
    user = request.user

    try:
        data = json.loads(request.body)
        recipient_handle = data.get("handle")
    except Exception:
        return JsonResponse({"error": "Invalid payload"}, status=400)

    if not recipient_handle:
        return JsonResponse({"error": "Handle is required"}, status=400)

    try:
        res = services.send_meet_signal(user, recipient_handle, event)
        return JsonResponse(res)
    except ValidationError as e:
        return JsonResponse({"error": str(e.message if hasattr(e, 'message') else e)}, status=400)


@crush_login_required
@require_POST
def api_confirm_meeting(request, event_id):
    """
    API endpoint to confirm a meeting in recap phase using opaque handle.
    """
    event = get_object_or_404(MeetupEvent, pk=event_id)
    user = request.user

    try:
        data = json.loads(request.body)
        other_handle = data.get("handle")
    except Exception:
        return JsonResponse({"error": "Invalid payload"}, status=400)

    if not other_handle:
        return JsonResponse({"error": "Handle is required"}, status=400)

    try:
        res = services.confirm_meeting(user, other_handle, event)
        return JsonResponse(res)
    except ValidationError as e:
        return JsonResponse({"error": str(e.message if hasattr(e, 'message') else e)}, status=400)


@crush_login_required
@never_cache
def serve_participant_photo(request, event_id, handle):
    """
    Serves a participant's photo file ONLY after validating that the caller is
    checked in and participating in the event lobby.
    """
    event = get_object_or_404(MeetupEvent, pk=event_id)
    user = request.user

    # Validate caller eligibility
    if not services.check_eligibility(user, event):
        return HttpResponse("Unauthorized", status=403)

    # Find the target user matching the handle
    target_user = None
    for p in EventLobbyParticipation.objects.filter(event=event).select_related("user", "user__crushprofile"):
        if services.generate_opaque_handle(p.user, event) == handle:
            target_user = p.user
            break

    if not target_user:
        raise Http404("Handle not found")

    # Symmetric block check
    if UserBlock.objects.between(user, target_user).exists():
        return HttpResponse("Unauthorized due to block", status=403)

    profile = getattr(target_user, "crushprofile", None)
    if not profile or not profile.photo_1:
        raise Http404("No photo found")

    try:
        photo_file = profile.photo_1.open()
        response = HttpResponse(photo_file.read(), content_type="image/jpeg")
        response["Cache-Control"] = "private, no-store, must-revalidate"
        return response
    except Exception:
        raise Http404("Error reading photo")


@crush_login_required
def people_ive_met_view(request):
    """
    Renders the permanent "People I've Met" collection page.
    """
    encounters = services.get_people_ive_met(request.user)
    return render(
        request,
        "crush_connect_lobby/people_ive_met.html",
        {"encounters": encounters}
    )


@crush_login_required
def view_member_profile(request, member_id):
    """
    PROTOTYPE-STUB: Simulates the full member profile view reached from People I've Met.
    """
    from django.contrib.auth import get_user_model
    User = get_user_model()
    member = get_object_or_404(User, pk=member_id)
    profile = getattr(member, "crushprofile", None)
    membership = getattr(member, "crush_connect_membership", None)

    # Check if they have an active encounter with the viewer
    low_id, high_id = min(request.user.pk, member_id), max(request.user.pk, member_id)
    encounter = ConfirmedEncounter.objects.filter(
        user_low_id=low_id, user_high_id=high_id, status="active"
    ).first()

    if not encounter:
        return HttpResponse("Access Denied", status=403)

    return render(
        request,
        "crush_connect_lobby/member_profile.html",
        {
            "member": member,
            "profile": profile,
            "membership": membership,
            "encounter": encounter,
        }
    )


@crush_login_required
@require_POST
def api_request_removal(request, encounter_id):
    """
    API endpoint to request removal of a confirmed encounter.
    """
    user = request.user
    try:
        data = json.loads(request.body)
        reason = data.get("reason")
        details = data.get("details", "")
    except Exception:
        return JsonResponse({"error": "Invalid payload"}, status=400)

    if not reason:
        return JsonResponse({"error": "Reason is required"}, status=400)

    try:
        req = services.request_encounter_removal(user, encounter_id, reason, details)
        return JsonResponse({"status": "removal_pending", "request_id": req.id})
    except ValidationError as e:
        return JsonResponse({"error": str(e.message if hasattr(e, 'message') else e)}, status=400)
