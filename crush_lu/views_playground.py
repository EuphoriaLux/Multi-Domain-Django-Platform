"""Coach playground pages on test.crush.lu.

Spec: ai-memory-hub/specs/2026-11-13-crush-coach-playground.md

Every view here returns 404 off staging before any login redirect, so the
production slot looks like it has no such pages.
"""

import logging
import time
from functools import wraps
from urllib.parse import quote

from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django.views.decorators.http import require_GET, require_POST

from crush_lu.decorators import coach_required
from crush_lu.services import playground_practice as practice
from crush_lu.services import playground_sso as sso
from crush_lu.services.staging_playground import playground_enabled

logger = logging.getLogger(__name__)

MODEL_BACKEND = "django.contrib.auth.backends.ModelBackend"

ERROR_MESSAGES = {
    "video_admission_closed": gettext_lazy(
        "Video verification requests are switched off on this server."
    ),
    "video_unavailable": gettext_lazy("Video calls are not configured on this server."),
    "capacity": gettext_lazy(
        "You already hold 10 open claims. Release one in the channel, then try "
        "again."
    ),
    "appointment_overlap": gettext_lazy(
        "You already have an appointment at that time. Cancel it, then try again."
    ),
    "excluded": gettext_lazy(
        "That test member already met your practice member. Reset the Connect "
        "history first."
    ),
    "recipient_unavailable": gettext_lazy(
        "Your practice member is not ready for Connect yet. Start from "
        '"Connect Week ready".'
    ),
    "requester_unavailable": gettext_lazy(
        "Your practice member is not ready for Connect yet. Start from "
        '"Connect Week ready".'
    ),
    "no_pending_request": gettext_lazy("There is no pending request between them."),
    "no_chat": gettext_lazy("They have no chat yet: accept a request first."),
    "no_connect_week": gettext_lazy(
        "Your practice member has no Connect Week running. Open Connect Week on "
        "the phone first."
    ),
    "no_cast": gettext_lazy("The test members are not set up on this server yet."),
}


def playground_view(view):
    """404 off staging, then coach only, then a recent crush.lu sign-in."""

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not playground_enabled():
            raise Http404
        return _coach_and_fresh(request, *args, **kwargs)

    @coach_required
    def _coach_and_fresh(request, *args, **kwargs):
        if not sso.signed_in_recently(request.session):
            messages.info(
                request,
                _("Please sign in with crush.lu again to use the playground."),
            )
            start = reverse("coach_playground_sso_start")
            return redirect(f"{start}?next={quote(request.path, safe='')}")
        return view(request, *args, **kwargs)

    return wrapper


def _error_text(code):
    return ERROR_MESSAGES.get(code) or _(
        "A check refused this scenario (%(code)s). Nothing was changed."
    ) % {"code": code}


@playground_view
@require_GET
def coach_playground(request):
    from crush_lu.services.playground_mail import recent_mail
    from crush_lu.services.quiz_rehearsal import rehearsal_enabled

    from crush_lu.services import playground_cast as cast
    from crush_lu.services.playground_events import ensure_practice_mixer

    link = practice.live_practice_link(request.user)
    practice_user = link.user if link else None
    cast.refresh_cast_activity()
    context = {
        "cast": list(cast.cast_queryset().select_related("playground_account")),
        "practice_mixer": ensure_practice_mixer(request.user),
        "coach": request.coach,
        "practice_link": link,
        "practice_user": practice_user,
        "practice_status": (
            practice.practice_status(practice_user) if practice_user else {}
        ),
        "presets": list(practice.PRESETS.values()),
        "current_preset": practice.PRESETS.get(link.preset) if link else None,
        "mails": recent_mail(practice_user) if practice_user else [],
        "quiz_rehearsal_enabled": rehearsal_enabled(),
        "sso_enabled": sso.consumer_enabled(),
    }
    return render(request, "crush_lu/coach_playground.html", context)


@playground_view
@require_POST
def coach_playground_scenario(request):
    key = request.POST.get("preset", practice.DEFAULT_PRESET)
    gender = request.POST.get("gender", "")
    try:
        practice.apply_preset(request.coach, key, gender=gender)
    except practice.PlaygroundError as exc:
        messages.error(request, _error_text(exc.code))
    else:
        messages.success(
            request,
            _("Your practice member is ready. Switch to it on your phone."),
        )
    return redirect("crush_lu:coach_playground")


@playground_view
@require_POST
def coach_playground_switch(request):
    link = practice.live_practice_link(request.user)
    if link is None or not link.user.is_active:
        messages.error(request, _("Create your practice member first."))
        return redirect("crush_lu:coach_playground")
    coach_user = request.user
    sso_at = request.session.get(sso.SIGNED_IN_AT_SESSION_KEY)
    login(request, link.user, backend=MODEL_BACKEND)
    # After login(): it flushed the coach's session.
    request.session[practice.SWITCH_SESSION_KEY] = {
        "coach": coach_user.pk,
        "practice": link.user_id,
        "sso_at": sso_at,
        "at": int(time.time()),
    }
    logger.info(
        "Coach playground: coach %s switched to practice member %s",
        coach_user.pk,
        link.user_id,
    )
    return redirect("crush_lu:dashboard")


def playground_back_to_coach(request):
    """From the practice member back to the coach who switched into it."""
    from django.http import HttpResponseNotAllowed

    from crush_lu.models import CrushCoach, PlaygroundAccount

    # The gate comes before the method check: production answers 404 to any
    # method, as if the page did not exist.
    if not playground_enabled():
        raise Http404
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    marker = practice.switch_marker(request)
    owner_id = marker.get("coach") if marker else None
    valid = (
        marker is not None
        and isinstance(owner_id, int)
        and PlaygroundAccount.objects.filter(
            user=request.user,
            kind=PlaygroundAccount.KIND_PRACTICE,
            owner_id=owner_id,
            retired_at__isnull=True,
        ).exists()
        and CrushCoach.objects.filter(
            user_id=owner_id, is_active=True, user__is_active=True
        ).exists()
    )
    if not valid:
        messages.error(
            request,
            _(
                "This session cannot switch back. Sign in again with your coach account."
            ),
        )
        return redirect("crush_lu:login")
    owner = get_user_model().objects.get(pk=owner_id)
    sso_at = marker.get("sso_at")
    login(request, owner, backend=MODEL_BACKEND)
    if isinstance(sso_at, (int, float)):
        request.session[sso.SIGNED_IN_AT_SESSION_KEY] = sso_at
    return redirect("crush_lu:coach_playground")


#: Buttons that make a test member act, and buttons that move the member's week.
CONNECT_ACTIONS = ("send", "accept", "decline", "reply", "expire")
SESSION_ACTIONS = ("next_day", "review", "reset")


@playground_view
@require_POST
def coach_playground_connect(request):
    """A test member does what the coach asks, through the real Connect code."""
    from crush_lu.services import playground_cast as cast

    action = request.POST.get("action", "")
    link = practice.live_practice_link(request.user)
    if link is None:
        messages.error(request, _("Create your practice member first."))
        return redirect("crush_lu:coach_playground")
    member = link.user
    try:
        if action in CONNECT_ACTIONS:
            other = cast.cast_member(request.POST.get("cast", ""))
            name = other.first_name
            if action == "send":
                cast.cast_sends_request(member, other)
                text = _(
                    "%(name)s sent your practice member a request. Open Connect "
                    "Week on the phone."
                )
            elif action == "accept":
                cast.cast_answers_request(member, other, accept=True)
                text = _("%(name)s accepted. A chat is open on the phone.")
            elif action == "decline":
                cast.cast_answers_request(member, other, accept=False)
                text = _(
                    "%(name)s declined. The member is never told; the request "
                    "just ends."
                )
            elif action == "reply":
                cast.cast_replies(member, other)
                text = _("%(name)s replied in the chat.")
            else:
                cast.let_request_expire(member, other)
                text = _("The request expired.")
            messages.success(request, text % {"name": name})
        elif action == "next_day":
            cast.skip_days(member, 1)
            messages.success(
                request, _("Your practice member's Connect Week moved one day on.")
            )
        elif action == "review":
            cast.jump_to_review(member)
            messages.success(
                request, _("Your practice member's Connect Week is at review day.")
            )
        elif action == "reset":
            cast.reset_connect_history(member)
            messages.success(
                request, _("Your practice member's Connect history is cleared.")
            )
        else:
            raise practice.PlaygroundError("unknown_action")
    except practice.PlaygroundError as exc:
        messages.error(request, _error_text(exc.code))
    return redirect("crush_lu:coach_playground")


@playground_view
@require_POST
def coach_playground_mixer(request):
    """Fill the room, end the mixer, or start a fresh one."""
    from crush_lu.services import playground_events as events

    action = request.POST.get("action", "")
    try:
        if action == "fill":
            count = events.fill_the_room(request.user)
            messages.success(
                request, _("%(count)s test members are in the room.") % {"count": count}
            )
        elif action == "end":
            events.end_practice_mixer(request.user)
            messages.success(
                request, _("The mixer has ended: the recap and My Crush are open.")
            )
        elif action == "fresh":
            events.fresh_practice_mixer(request.user)
            messages.success(request, _("A fresh mixer is ready."))
        else:
            raise practice.PlaygroundError("unknown_action")
    except practice.PlaygroundError as exc:
        messages.error(request, _error_text(exc.code))
    return redirect("crush_lu:coach_playground")
