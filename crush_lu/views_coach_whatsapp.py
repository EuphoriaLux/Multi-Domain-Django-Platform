"""
Coach WhatsApp inbox views for Crush.lu.

Members' WhatsApp messages to a coach's own number, read and answered on
crush.lu (see ``crush_lu/services/coach_whatsapp.py``). A coach sees only
their own number's chats; a superuser can open any coach's inbox read-only.
"""

import logging

from django.contrib import messages
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from crush_lu.decorators import coach_required, ratelimit
from crush_lu.models import CallAttempt, CrushCoach, ProfileSubmission
from crush_lu.services import coach_whatsapp
from hub.models import WhatsAppMessage
from hub.whatsapp_service import ERROR_OUTSIDE_SERVICE_WINDOW, TEXT_MAX_LENGTH

logger = logging.getLogger(__name__)


def _inbox_coach(request):
    """The coach whose inbox is shown: a superuser may pick another one."""
    if request.user.is_superuser and request.GET.get("coach"):
        coach = (
            CrushCoach.objects.filter(pk=request.GET["coach"])
            .exclude(whatsapp_phone_number_id="")
            .select_related("user")
            .first()
        )
        if coach is not None:
            return coach
    return request.coach


def _thread_coach(request, conversation_id):
    """``(coach, conversation)`` for a thread the viewer may open, else 404.

    A coach only ever resolves conversations on their own number. A superuser
    also resolves another coach's, which is how they open it read-only.
    """
    conv = coach_whatsapp.conversation(request.coach, conversation_id)
    if conv is not None:
        return request.coach, conv
    if request.user.is_superuser:
        for coach in CrushCoach.objects.exclude(whatsapp_phone_number_id=""):
            conv = coach_whatsapp.conversation(coach, conversation_id)
            if conv is not None:
                return coach, conv
    raise Http404


def _failure_text(message):
    """What to tell the coach when Meta refused a reply."""
    last = (message.status_history or [{}])[-1]
    if last.get("error_code") == ERROR_OUTSIDE_SERVICE_WINDOW:
        return _(
            "WhatsApp refused the reply: more than 24 hours have passed since "
            "this member's last message."
        )
    return _("WhatsApp didn't accept the message. Please try again.")


@coach_required
@require_GET
def coach_whatsapp_inbox(request):
    coach = _inbox_coach(request)
    coaches_with_numbers = []
    if request.user.is_superuser:
        coaches_with_numbers = (
            CrushCoach.objects.exclude(whatsapp_phone_number_id="")
            .select_related("user")
            .order_by("user__first_name")
        )
    return render(
        request,
        "crush_lu/coach_whatsapp_inbox.html",
        {
            "inbox_coach": coach,
            "has_inbox": coach_whatsapp.has_inbox(coach),
            "conversations": coach_whatsapp.conversations_for(coach),
            "coaches_with_numbers": coaches_with_numbers,
            "read_only": coach != request.coach,
        },
    )


@coach_required
# Generous: a busy evening of replies stays far below it; it only caps a
# runaway client (each reply is a billed Meta message).
@ratelimit(key="user", rate="200/h", method="POST")
@require_http_methods(["GET", "POST"])
def coach_whatsapp_thread(request, conversation_id):
    coach, conv = _thread_coach(request, conversation_id)
    read_only = coach != request.coach
    is_hx = bool(request.headers.get("HX-Request"))

    if request.method == "POST":
        if read_only:
            raise Http404

        def _inline_error(error_text):
            response = render(
                request,
                "crush_lu/_connection_message_error.html",
                {"error": error_text},
            )
            response["HX-Retarget"] = "#chat-compose-error"
            response["HX-Reswap"] = "innerHTML"
            return response

        try:
            message = coach_whatsapp.send_reply(
                coach, conv, request.POST.get("message", ""), request.user
            )
        except coach_whatsapp.CoachWhatsAppError as exc:
            if is_hx:
                return _inline_error(str(exc))
            messages.error(request, str(exc))
            return redirect("crush_lu:coach_whatsapp_thread", conversation_id=conv.id)

        if message.status == WhatsAppMessage.Status.FAILED:
            if is_hx:
                return _inline_error(_failure_text(message))
            messages.error(request, _failure_text(message))
            return redirect("crush_lu:coach_whatsapp_thread", conversation_id=conv.id)

        if is_hx:
            response = render(
                request,
                "crush_lu/_coach_whatsapp_message.html",
                {
                    "item": coach_whatsapp.ThreadItem(
                        outbound=True,
                        at=message.created_at,
                        text=message.text,
                        status=message.status,
                    )
                },
            )
            # connection-chat.js resets the compose form on this event only.
            response["HX-Trigger"] = "connection-message-sent"
            return response
        return redirect("crush_lu:coach_whatsapp_thread", conversation_id=conv.id)

    if not read_only:
        coach_whatsapp.mark_read(coach, conv.from_number)
    return render(
        request,
        "crush_lu/coach_whatsapp_thread.html",
        {
            "conv": conv,
            "items": coach_whatsapp.thread(coach, conv.from_number),
            "read_only": read_only,
            "inbox_coach": coach,
            "message_max_length": TEXT_MAX_LENGTH,
        },
    )


@coach_required
# Each send is a billed template; the 24-hour per-member cooldown is the real
# limit, this only caps a runaway client.
@ratelimit(key="user", rate="60/h", method="POST")
@require_POST
def coach_whatsapp_followup(request, submission_id):
    """Screening tab: send the follow-up template from the coach's Crush number.

    The Crush-number counterpart of ``coach_log_whatsapp_sent``, for a coach
    whose ``wa.me`` link would open a personal WhatsApp. On success it logs the
    same ``whatsapp_sent`` call attempt, then re-renders the screening tab.
    """
    from crush_lu.views_coach import _screening_outreach_context

    coach = request.coach
    submission = get_object_or_404(
        ProfileSubmission.objects.select_related("profile__user"),
        id=submission_id,
        coach=coach,
    )
    profile = submission.profile
    try:
        message = coach_whatsapp.send_followup(coach, profile, request.user)
    except coach_whatsapp.CoachWhatsAppError as exc:
        feedback = {"ok": False, "text": str(exc)}
    else:
        if message.status == WhatsAppMessage.Status.FAILED:
            feedback = {"ok": False, "text": _failure_text(message)}
        else:
            CallAttempt.objects.create(
                submission=submission,
                result="whatsapp_sent",
                coach=coach,
                notes=_("WhatsApp template sent from the coach's Crush number"),
            )
            feedback = {
                "ok": True,
                "text": _(
                    "Sent from your Crush number. Their reply will show in your "
                    "WhatsApp inbox."
                ),
            }

    if request.headers.get("HX-Request"):
        context = {
            "submission": submission,
            "profile": profile,
            "followup_feedback": feedback,
        }
        context.update(_screening_outreach_context(coach, profile))
        return render(request, "crush_lu/_screening_tab.html", context)
    if feedback["ok"]:
        messages.success(request, feedback["text"])
    else:
        messages.error(request, feedback["text"])
    return redirect("crush_lu:coach_review_profile", submission_id=submission.id)


@coach_required
@require_GET
def coach_whatsapp_messages(request, conversation_id):
    """HTMX polling endpoint: the rendered message list of one conversation."""
    coach, conv = _thread_coach(request, conversation_id)
    if coach == request.coach:
        coach_whatsapp.mark_read(coach, conv.from_number)
    return render(
        request,
        "crush_lu/_coach_whatsapp_messages_list.html",
        {"items": coach_whatsapp.thread(coach, conv.from_number)},
    )


@coach_required
@require_GET
def coach_whatsapp_media(request, inbound_id):
    """Stream one inbound photo / voice note / file from Meta (nothing stored)."""
    coach, _conv = _thread_coach(request, inbound_id)
    row = coach_whatsapp.inbound_for(coach).filter(pk=inbound_id).first()
    info = coach_whatsapp.media_info(row) if row is not None else None
    if info is None:
        raise Http404
    try:
        content, mime_type = coach_whatsapp.fetch_media(info["id"])
    except coach_whatsapp.MediaUnavailable as exc:
        logger.warning("Coach WhatsApp media %s unavailable: %s", inbound_id, exc)
        return HttpResponse(
            _("This file is no longer available on WhatsApp."),
            status=404,
            content_type="text/plain; charset=utf-8",
        )
    response = HttpResponse(content, content_type=mime_type)
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "private, max-age=3600"
    # Only known-safe types render in the browser; anything else (a document,
    # an SVG) is a download, so a member can't get markup run on crush.lu.
    response["Content-Disposition"] = (
        "inline"
        if mime_type.startswith(coach_whatsapp.INLINE_MEDIA_PREFIXES)
        else "attachment"
    )
    return response
