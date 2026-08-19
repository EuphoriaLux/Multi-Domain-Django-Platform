"""
Crush Connect — 7-Day Connect Cycle views (Epic 13 / Task 13.2).

Daily 3-card generation, the 24h "Deine Connect-Woche" review grid, the
one-or-none weekly request, and the recipient inbox. Kept in its own module
(rather than growing the 1547-line ``views_crush_connect.py``) since the
Cycle is a distinct surface with its own access gate.

See ``crush_lu.services.connect_cycle`` for the mechanics and its module
docstring for this PR's documented scope simplifications.
"""

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext_lazy as _

from crush_lu.connect_phase import candidate_access_open, cycle_access_open
from crush_lu.decorators import crush_login_required
from crush_lu.models.crush_connect_cycle import (
    ConnectCycleCard,
    ConnectWeeklyRequest,
    ConnectWeekSession,
)
from crush_lu.services.connect_cycle import (
    CYCLE_LENGTH_DAYS,
    can_send_weekly_request,
    get_or_create_active_session,
    get_or_create_todays_cards,
    get_pending_inbox,
    get_review_cards,
    record_card_answer,
    respond_to_weekly_request,
    send_weekly_request,
    sync_request_state,
    sync_session_state,
)

User = get_user_model()


def _connect_week_access_blocker(user):
    """``None`` when the user may access the Connect Week surfaces (home,
    cards, review, sending a request), otherwise the redirect to send them.

    Gated by ``cycle_access_open`` — deliberately NOT ``candidate_access_open``
    or ``receiver_access_open``: the Cycle's beta rule widens to event-verified
    members without requiring Premium (see ``connect_phase.cycle_access_open``
    docstring). Mirrors ``views_crush_connect._connect_access_blocker``'s
    shape otherwise: staff bypass, coach-exclusion and onboarding checks.
    """
    if user.is_staff:
        return None
    if not cycle_access_open(user):
        return redirect("crush_lu:crush_connect_teaser")

    membership = getattr(user, "crush_connect_membership", None)
    if membership is not None and membership.excluded_by_coach:
        return redirect("crush_lu:crush_connect_teaser")

    if membership is None or membership.onboarded_at is None:
        profile = getattr(user, "crushprofile", None)
        if (
            profile is None
            or not profile.is_approved
            or not profile.is_connect_identity_verified
        ):
            return redirect("crush_lu:crush_connect_teaser")
        return redirect("crush_lu:crush_connect_onboarding")

    return None


@crush_login_required
def connect_week_home(request):
    """Connect Week home: today's up-to-3 cards, or a redirect to the review
    once day 7 has fully elapsed. Starts a new session on first visit for a
    cycle-eligible member with no session in progress."""
    user = request.user
    profile = getattr(user, "crushprofile", None)
    if not user.is_staff and profile and not profile.photo_1:
        messages.warning(
            request, _("Please upload a profile photo to use Crush Connect.")
        )
        return redirect("crush_lu:edit_profile")

    blocker = _connect_week_access_blocker(user)
    if blocker is not None:
        return blocker

    session = sync_session_state(get_or_create_active_session(user))

    if session.status == ConnectWeekSession.Status.REVIEW_OPEN:
        return redirect("crush_lu:connect_week_review")

    cards = get_or_create_todays_cards(session)
    answered_ids = {c.target_user_id for c in cards if c.is_completed}

    context = {
        "session": session,
        "cards": cards,
        "answered_ids": answered_ids,
        "day_number": session.current_day_number,
        "cycle_length": CYCLE_LENGTH_DAYS,
    }
    return render(request, "crush_lu/crush_connect/week_home.html", context)


@crush_login_required
def connect_week_card_answer(request, card_id: int):
    """Submit private guesses for one of today's cards (POST only)."""
    if request.method != "POST":
        return redirect("crush_lu:connect_week_home")

    blocker = _connect_week_access_blocker(request.user)
    if blocker is not None:
        return blocker

    card = get_object_or_404(
        ConnectCycleCard.objects.select_related(
            "session", "target_user__crush_connect_membership"
        ),
        pk=card_id,
        session__user=request.user,
    )
    session = sync_session_state(card.session)
    still_current = (
        session.status == ConnectWeekSession.Status.ACTIVE
        and card.day_number == session.current_day_number
    )
    if not still_current or card.is_completed or card.is_expired:
        messages.info(request, _("This card is no longer open."))
        return redirect("crush_lu:connect_week_home")

    membership = getattr(card.target_user, "crush_connect_membership", None)
    gate_questions = list(membership.active_gate_questions) if membership else []
    if not gate_questions:
        # The target cleared/re-picked their gate questions between the card
        # being rendered and this POST landing (or a direct/racy POST with no
        # prior render) — nothing to score, so don't silently complete the
        # card with zero guesses. The template only renders the answer form
        # when gate_questions is non-empty; this is the view-layer guard for
        # everything that bypasses the template (direct POST, stale tab).
        messages.error(request, _("Please answer all three questions."))
        return redirect("crush_lu:connect_week_home")

    guesses = {}
    for gq in gate_questions:
        raw = request.POST.get(f"answer_{gq.question_id}")
        if raw not in ("yes", "no"):
            messages.error(request, _("Please answer all three questions."))
            return redirect("crush_lu:connect_week_home")
        guesses[gq.question_id] = raw == "yes"

    record_card_answer(card, guesses)
    messages.success(request, _("Got it — see you tomorrow for three new faces."))
    return redirect("crush_lu:connect_week_home")


@crush_login_required
def connect_week_review(request):
    """The 24h "Deine Connect-Woche" review grid."""
    from crush_lu.models import ConnectQuestion

    user = request.user
    blocker = _connect_week_access_blocker(user)
    if blocker is not None:
        return blocker

    session = (
        ConnectWeekSession.objects.filter(user=user).order_by("-started_at").first()
    )
    if session is None:
        return redirect("crush_lu:connect_week_home")

    session = sync_session_state(session)
    if session.status == ConnectWeekSession.Status.ACTIVE:
        return redirect("crush_lu:connect_week_home")

    cards = get_review_cards(session)

    # Resolve display text from the STORED guesses (answers_json), never from
    # the target's current CrushConnectMembership.active_gate_questions —
    # that's a live pointer that moves if the target re-picks their 3
    # questions after this member answered, which would otherwise show the
    # wrong questions next to an old guess. Only the guess itself is shown —
    # never gate_align or the target's truth (that would leak their private
    # answer, exactly what the "Read-the-Photo" privacy contract forbids).
    question_ids = set()
    for card in cards:
        guesses = (card.answers_json or {}).get("guesses") or {}
        question_ids.update(int(qid) for qid in guesses)
    questions_by_id = ConnectQuestion.objects.in_bulk(question_ids)

    review_items = []
    for card in cards:
        guesses = (card.answers_json or {}).get("guesses") or {}
        answers = [
            {"text": questions_by_id[int(qid)].text, "guess": guess}
            for qid, guess in guesses.items()
            if int(qid) in questions_by_id
        ]
        review_items.append({"card": card, "target": card.target_user, "answers": answers})

    # The requester's own review is the one page they reliably revisit — sync
    # it here so a stale PENDING request (recipient never opened their inbox)
    # flips to EXPIRED and writes its ConnectPairExclusion instead of showing
    # "waiting for their answer" forever.
    sent_request = session.weekly_requests.select_related("recipient").first()
    if sent_request is not None:
        sent_request = sync_request_state(sent_request)

    context = {
        "session": session,
        "review_items": review_items,
        "highlight_user_id": session.compatibility_highlight_user_id,
        "sent_request": sent_request,
        "review_active": session.is_review_active,
    }
    return render(request, "crush_lu/crush_connect/week_review.html", context)


@crush_login_required
def connect_week_request_send(request, card_id: int):
    """Send the session's single weekly request to one reviewed profile
    (POST only)."""
    if request.method != "POST":
        return redirect("crush_lu:connect_week_review")

    user = request.user
    blocker = _connect_week_access_blocker(user)
    if blocker is not None:
        return blocker

    card = get_object_or_404(
        ConnectCycleCard.objects.select_related("session", "target_user"),
        pk=card_id,
        session__user=user,
    )
    session = sync_session_state(card.session)

    try:
        send_weekly_request(session, user, card.target_user, request=request)
    except ValueError as exc:
        reasons = {
            "not_owner": _("This isn't your Connect Week."),
            "review_closed": _("The review window for this week has closed."),
            "not_in_review": _("This profile isn't part of this week's review."),
            "already_sent": _("You've already sent your one request for this week."),
            "recipient_unavailable": _("This member isn't available right now."),
            "blocked": _("This member isn't available right now."),
            "excluded": _("This member isn't available right now."),
        }
        messages.info(
            request,
            reasons.get(str(exc), _("That request isn't possible right now.")),
        )
    else:
        messages.success(
            request,
            _('Sent — "Ich möchte dich kennenlernen." They have 24 hours to respond.'),
        )
    return redirect("crush_lu:connect_week_review")


@crush_login_required
def connect_week_inbox(request):
    """The recipient's inbox of pending weekly requests.

    Reachable by any catalogue-eligible member — deliberately NOT gated by
    ``cycle_access_open``: the trust table lets a LuxID-only member (who
    can't browse or send from the Cycle) still accept a request and chat.
    Mirrors ``crush_connect_sparks_received``'s gate exactly.
    """
    from crush_lu.services.crush_connect import is_catalogue_eligible

    user = request.user
    if not user.is_staff and not (
        candidate_access_open() and is_catalogue_eligible(user)
    ):
        return redirect("crush_lu:crush_connect_teaser")

    return render(
        request,
        "crush_lu/crush_connect/week_inbox.html",
        {"requests": get_pending_inbox(user)},
    )


@crush_login_required
def connect_week_request_respond(request, request_id: int):
    """Accept or decline a pending weekly request (POST only, recipient
    only). Same gate as ``connect_week_inbox``."""
    from crush_lu.services.crush_connect import is_catalogue_eligible

    if request.method != "POST":
        return redirect("crush_lu:connect_week_inbox")

    user = request.user
    if not user.is_staff and not (
        candidate_access_open() and is_catalogue_eligible(user)
    ):
        return redirect("crush_lu:crush_connect_teaser")

    weekly_request = get_object_or_404(
        ConnectWeeklyRequest.objects.select_related("requester", "recipient"),
        pk=request_id,
        recipient=user,
    )
    accept = request.POST.get("action") == "accept"
    updated = respond_to_weekly_request(weekly_request, accept=accept, request=request)
    # respond_to_weekly_request can no-op either direction (already resolved
    # in another tab, or an accept left PENDING because the requester lost
    # eligibility / a block appeared) — check the ACTUAL resulting status
    # rather than trusting the requested action, so a no-op decline doesn't
    # falsely tell the user "Declined" when nothing was recorded.
    if updated.status == ConnectWeeklyRequest.Status.ACCEPTED:
        messages.success(request, _("It's mutual! Say hello — your chat is open."))
    elif updated.status == ConnectWeeklyRequest.Status.DECLINED:
        messages.info(request, _("Declined — they won't be told."))
    else:
        messages.info(request, _("This request is no longer available."))
    return redirect("crush_lu:connect_week_inbox")
