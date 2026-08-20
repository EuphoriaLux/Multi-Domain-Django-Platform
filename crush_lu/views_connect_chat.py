"""
Crush Connect — Temporary Chat, Venue Picker, Post-Meeting Confirmation, and
1-Click Block views (Epic 13 follow-up to Task 13.2 / PR #886).

Kept in its own module (mirrors ``views_connect_cycle``'s own rationale) —
this is the surface a mutual weekly-request accept opens onto, distinct
from the Cycle's daily-card/review mechanics. See
``crush_lu.services.connect_chat`` for the mechanics and its module
docstring for the scope notes (rolling inactivity timer, block choke point,
``ConnectReport`` reuse).
"""

from django.contrib import messages
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from crush_lu.decorators import crush_login_required
from crush_lu.models.crush_connect_cycle import ConnectTemporaryChat
from crush_lu.services.connect_chat import (
    CHAT_MESSAGE_MAX_LENGTH,
    accept_venue,
    block_chat_partner,
    cancel_venue,
    chat_is_open,
    confirm_meeting,
    get_partner_venues,
    list_messages,
    propose_venue,
    send_message,
    sync_chat_state,
)


def _get_participant_chat(user, chat_id):
    """A chat the user is actually a participant of, or 404 — a stranger
    guessing another pair's chat id must never see or act on it."""
    return get_object_or_404(
        ConnectTemporaryChat.objects.select_related(
            "participant_1__crushprofile",
            "participant_2__crushprofile",
            "coffee_date__venue_location",
        ),
        Q(participant_1=user) | Q(participant_2=user),
        pk=chat_id,
    )


@crush_login_required
def connect_week_chats(request):
    """The member's Connect Cycle temp chats, most recent first.

    ``get_other_participant`` takes an argument the template layer can't
    supply (Django templates only call zero-arg callables), so the partner
    is resolved here into ``chat.partner`` for the template to read as a
    plain attribute.
    """
    user = request.user
    chats = []
    for chat in (
        ConnectTemporaryChat.objects.filter(
            Q(participant_1=user) | Q(participant_2=user)
        )
        .select_related("participant_1__crushprofile", "participant_2__crushprofile")
        .order_by("-created_at")
    ):
        chat = sync_chat_state(chat)
        chat.partner = chat.get_other_participant(user)
        chats.append(chat)
    return render(request, "crush_lu/crush_connect/week_chats.html", {"chats": chats})


@crush_login_required
def connect_week_chat_detail(request, chat_id: int):
    """One chat: message thread, venue picker / coffee-date plan, post-meeting
    confirmation loop, and the 1-click block form."""
    user = request.user
    chat = sync_chat_state(_get_participant_chat(user, chat_id))
    partner = chat.get_other_participant(user)
    coffee_date = getattr(chat, "coffee_date", None)
    is_p1 = chat.participant_1_id == user.id

    my_confirmed_at = None
    partner_confirmed_at = None
    is_proposer = False
    if coffee_date is not None:
        my_confirmed_at = (
            coffee_date.participant_1_confirmed_at
            if is_p1
            else coffee_date.participant_2_confirmed_at
        )
        partner_confirmed_at = (
            coffee_date.participant_2_confirmed_at
            if is_p1
            else coffee_date.participant_1_confirmed_at
        )
        is_proposer = coffee_date.proposer_id == user.id

    context = {
        "chat": chat,
        "partner": partner,
        "chat_messages": list_messages(chat),
        "coffee_date": coffee_date,
        "is_proposer": is_proposer,
        "my_confirmed_at": my_confirmed_at,
        "partner_confirmed_at": partner_confirmed_at,
        "venues": get_partner_venues(),
        "is_open": chat_is_open(chat),
        "message_max_length": CHAT_MESSAGE_MAX_LENGTH,
        "today": timezone.localdate(),
    }
    return render(request, "crush_lu/crush_connect/chat_detail.html", context)


@crush_login_required
def connect_week_chat_send(request, chat_id: int):
    """Post a message (POST only)."""
    if request.method != "POST":
        return redirect("crush_lu:connect_week_chat_detail", chat_id=chat_id)

    user = request.user
    chat = _get_participant_chat(user, chat_id)
    try:
        send_message(chat, user, request.POST.get("message", ""))
    except ValueError as exc:
        reasons = {
            "chat_closed": _("This conversation has ended."),
            "empty_message": _("Write something first."),
        }
        messages.info(request, reasons.get(str(exc), _("Couldn't send that message.")))
    return redirect("crush_lu:connect_week_chat_detail", chat_id=chat_id)


@crush_login_required
def connect_week_chat_venue(request, chat_id: int):
    """Propose or update the coffee-date plan (POST only)."""
    if request.method != "POST":
        return redirect("crush_lu:connect_week_chat_detail", chat_id=chat_id)

    user = request.user
    chat = _get_participant_chat(user, chat_id)

    try:
        propose_venue(
            chat,
            user,
            venue_location_id=request.POST.get("venue_location_id") or None,
            custom_venue_name=request.POST.get("custom_venue_name", ""),
            custom_venue_address=request.POST.get("custom_venue_address", ""),
            proposed_date=request.POST.get("proposed_date", ""),
            proposed_time_slot=request.POST.get("proposed_time_slot", ""),
        )
    except ValueError as exc:
        reasons = {
            "chat_closed": _("This conversation has ended."),
            "already_confirmed": _(
                "This meeting is already confirmed — the plan can't change now."
            ),
            "missing_venue": _("Choose a partner venue or enter your own."),
            "invalid_date": _("Please pick a valid date."),
        }
        messages.info(request, reasons.get(str(exc), _("Couldn't save that plan.")))
    else:
        messages.success(request, _("Coffee date plan saved."))
    return redirect("crush_lu:connect_week_chat_detail", chat_id=chat_id)


@crush_login_required
def connect_week_chat_venue_respond(request, chat_id: int):
    """Accept or cancel the pending coffee-date plan (POST only)."""
    if request.method != "POST":
        return redirect("crush_lu:connect_week_chat_detail", chat_id=chat_id)

    user = request.user
    chat = _get_participant_chat(user, chat_id)
    coffee_date = getattr(chat, "coffee_date", None)
    if coffee_date is None:
        return redirect("crush_lu:connect_week_chat_detail", chat_id=chat_id)

    action = request.POST.get("action")
    try:
        if action == "accept":
            accept_venue(coffee_date, user)
            messages.success(request, _("Plan confirmed — see you there!"))
        elif action == "cancel":
            cancel_venue(coffee_date, user)
            messages.info(request, _("Plan cancelled."))
    except ValueError:
        messages.info(request, _("That plan can't be changed right now."))
    return redirect("crush_lu:connect_week_chat_detail", chat_id=chat_id)


@crush_login_required
def connect_week_chat_confirm(request, chat_id: int):
    """Post-meeting confirmation — "we met" (POST only)."""
    if request.method != "POST":
        return redirect("crush_lu:connect_week_chat_detail", chat_id=chat_id)

    user = request.user
    chat = _get_participant_chat(user, chat_id)
    coffee_date = getattr(chat, "coffee_date", None)
    if coffee_date is None:
        return redirect("crush_lu:connect_week_chat_detail", chat_id=chat_id)

    try:
        confirm_meeting(chat, coffee_date, user)
    except ValueError as exc:
        reasons = {
            "already_confirmed": _("Already confirmed — thanks!"),
            "not_accepted": _(
                "Confirm the plan with your match before marking the meeting as done."
            ),
        }
        messages.info(request, reasons.get(str(exc), _("Couldn't record that.")))
    else:
        messages.success(request, _("Thanks — recorded. Hope it was a great coffee!"))
    return redirect("crush_lu:connect_week_chat_detail", chat_id=chat_id)


@crush_login_required
def connect_week_chat_block(request, chat_id: int):
    """1-click block: exits the chat, permanently excludes the pair from
    future Connect Cycle re-matching, and blocks platform-wide (POST only).
    """
    if request.method != "POST":
        return redirect("crush_lu:connect_week_chats")

    user = request.user
    chat = _get_participant_chat(user, chat_id)
    escalate = request.POST.get("escalate_to_coach") == "1"
    details = request.POST.get("details", "")

    try:
        block_chat_partner(chat, user, escalate=escalate, details=details)
    except ValueError:
        messages.error(request, _("Couldn't block this member."))
    else:
        messages.success(
            request,
            _(
                "Blocked. They won't be able to reach you, and you won't see each other again."
            ),
        )
    return redirect("crush_lu:connect_week_chats")
