from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.models import User
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
import logging

logger = logging.getLogger(__name__)

from django.db.models import Count, Max

from .models import (
    CrushCoach,
    MeetupEvent,
    EventRegistration,
    EventActivityOption,
    EventActivityVote,
    EventVotingSession,
    PresentationQueue,
    PresentationRating,
    SpeedDatingPair,
)
from .decorators import crush_login_required, coach_required


# Event Activity Voting Views
@crush_login_required
def event_voting_lobby(request, event_id):
    """Pre-voting lobby with countdown and activity previews"""
    event = get_object_or_404(MeetupEvent, id=event_id)

    # Check if activity voting is enabled for this event
    if not event.enable_activity_voting:
        messages.error(request, _("Activity voting is not enabled for this event."))
        return redirect("crush_lu:event_detail", event_id=event_id)

    # Verify user is registered for this event
    user_registration = get_object_or_404(
        EventRegistration, event=event, user=request.user
    )

    # Only confirmed or attended registrations can access the lobby
    if user_registration.status not in ["confirmed", "attended"]:
        messages.error(request, _("Only confirmed attendees can access event voting."))
        return redirect("crush_lu:event_detail", event_id=event_id)

    # Check if user needs to check in before voting
    needs_checkin = user_registration.status == "confirmed"

    # Get or create voting session
    voting_session, created = EventVotingSession.objects.get_or_create(event=event)

    # Get global activity options by category
    from .models import GlobalActivityOption

    presentation_options = GlobalActivityOption.objects.filter(
        activity_type="presentation_style", is_active=True
    ).order_by("sort_order")

    twist_options = GlobalActivityOption.objects.filter(
        activity_type="speed_dating_twist", is_active=True
    ).order_by("sort_order")

    # Check if user has already voted on each category
    presentation_vote = None
    twist_vote = None
    has_voted_both = False

    if not needs_checkin:
        presentation_vote = EventActivityVote.objects.filter(
            event=event,
            user=request.user,
            selected_option__activity_type="presentation_style",
        ).first()

        twist_vote = EventActivityVote.objects.filter(
            event=event,
            user=request.user,
            selected_option__activity_type="speed_dating_twist",
        ).first()

        has_voted_both = bool(presentation_vote and twist_vote)

    # Get total confirmed attendees count
    total_attendees = EventRegistration.objects.filter(
        event=event, status__in=["confirmed", "attended"]
    ).count()

    # Determine voting phase
    voting_ended = not voting_session.is_voting_open and voting_session.time_until_start <= 0

    # Compute voting window length in minutes for display
    voting_window_minutes = int(
        (voting_session.voting_end_time - voting_session.voting_start_time).total_seconds() // 60
    )

    context = {
        "event": event,
        "voting_session": voting_session,
        "presentation_options": presentation_options,
        "twist_options": twist_options,
        "presentation_vote": presentation_vote,
        "twist_vote": twist_vote,
        "has_voted_both": has_voted_both,
        "total_attendees": total_attendees,
        "needs_checkin": needs_checkin,
        "voting_ended": voting_ended,
        "voting_window_minutes": voting_window_minutes,
    }
    return render(request, "crush_lu/event_voting_lobby.html", context)


@crush_login_required
def event_activity_vote(request, event_id):
    """Active voting interface"""
    event = get_object_or_404(MeetupEvent, id=event_id)

    # Check if activity voting is enabled for this event
    if not event.enable_activity_voting:
        messages.error(request, _("Activity voting is not enabled for this event."))
        return redirect("crush_lu:event_detail", event_id=event_id)

    # Verify user is registered for this event
    user_registration = get_object_or_404(
        EventRegistration, event=event, user=request.user
    )

    if user_registration.status not in ["confirmed", "attended"]:
        messages.error(request, _("Only confirmed attendees can vote."))
        return redirect("crush_lu:event_detail", event_id=event_id)

    # Must be checked in (attended) to vote
    if user_registration.status == "confirmed":
        messages.warning(
            request,
            _("You need to check in at the event before you can vote. Please find your coach to get checked in!"),
        )
        return redirect("crush_lu:event_voting_lobby", event_id=event_id)

    # Get voting session
    voting_session = get_object_or_404(EventVotingSession, event=event)

    # Check if voting is open
    if not voting_session.is_voting_open:
        messages.warning(request, _("Voting is not currently open for this event."))
        return redirect("crush_lu:event_voting_lobby", event_id=event_id)

    if request.method == "POST":
        presentation_option_id = request.POST.get("presentation_option_id")
        twist_option_id = request.POST.get("twist_option_id")

        if not presentation_option_id or not twist_option_id:
            messages.error(
                request,
                _(
                    "Please vote on BOTH categories: Presentation Style AND Speed Dating Twist."
                ),
            )
        else:
            try:
                from .models import GlobalActivityOption

                presentation_option = GlobalActivityOption.objects.get(
                    id=presentation_option_id,
                    activity_type="presentation_style",
                    is_active=True,
                )
                twist_option = GlobalActivityOption.objects.get(
                    id=twist_option_id,
                    activity_type="speed_dating_twist",
                    is_active=True,
                )

                # Check if user has already voted (for vote count tracking)
                had_presentation_vote = EventActivityVote.objects.filter(
                    event=event,
                    user=request.user,
                    selected_option__activity_type="presentation_style",
                ).exists()
                had_twist_vote = EventActivityVote.objects.filter(
                    event=event,
                    user=request.user,
                    selected_option__activity_type="speed_dating_twist",
                ).exists()

                # Handle presentation style vote (atomic update_or_create)
                if had_presentation_vote:
                    EventActivityVote.objects.filter(
                        event=event,
                        user=request.user,
                        selected_option__activity_type="presentation_style",
                    ).update(selected_option=presentation_option)
                else:
                    EventActivityVote.objects.create(
                        event=event,
                        user=request.user,
                        selected_option=presentation_option,
                    )

                # Handle speed dating twist vote (atomic update_or_create)
                if had_twist_vote:
                    EventActivityVote.objects.filter(
                        event=event,
                        user=request.user,
                        selected_option__activity_type="speed_dating_twist",
                    ).update(selected_option=twist_option)
                else:
                    EventActivityVote.objects.create(
                        event=event,
                        user=request.user,
                        selected_option=twist_option,
                    )

                # Update total votes only if this is first complete vote
                if not (had_presentation_vote and had_twist_vote):
                    voting_session.total_votes += 1
                    voting_session.save()

                messages.success(
                    request, _("Your votes have been recorded for both categories!")
                )
                return redirect("crush_lu:event_voting_results", event_id=event_id)

            except GlobalActivityOption.DoesNotExist:
                messages.error(request, _("Invalid activity option selected."))

    # Get all GLOBAL activity options (not per-event anymore!)
    from .models import GlobalActivityOption

    presentation_options = GlobalActivityOption.objects.filter(
        activity_type="presentation_style", is_active=True
    ).order_by("sort_order")

    twist_options = GlobalActivityOption.objects.filter(
        activity_type="speed_dating_twist", is_active=True
    ).order_by("sort_order")

    # Check if user has voted on BOTH categories
    presentation_vote = EventActivityVote.objects.filter(
        event=event,
        user=request.user,
        selected_option__activity_type="presentation_style",
    ).first()

    twist_vote = EventActivityVote.objects.filter(
        event=event,
        user=request.user,
        selected_option__activity_type="speed_dating_twist",
    ).first()

    context = {
        "event": event,
        "voting_session": voting_session,
        "presentation_options": presentation_options,
        "twist_options": twist_options,
        "presentation_vote": presentation_vote,
        "twist_vote": twist_vote,
        "has_voted_both": presentation_vote and twist_vote,
    }
    return render(request, "crush_lu/event_activity_vote.html", context)


@crush_login_required
def event_voting_results(request, event_id):
    """Display voting results and transition to presentations when ready"""
    event = get_object_or_404(MeetupEvent, id=event_id)

    # Allow event coaches and superusers
    is_coach = event.coaches.filter(user=request.user).exists()
    is_coach_view = is_coach or request.user.is_superuser
    if not is_coach_view:
        # Verify user is registered for this event
        user_registration = get_object_or_404(
            EventRegistration, event=event, user=request.user
        )

        if user_registration.status not in ["confirmed", "attended"]:
            messages.error(request, _("Only confirmed attendees can view results."))
            return redirect("crush_lu:event_detail", event_id=event_id)

        if user_registration.status == "confirmed":
            messages.warning(
                request,
                _("You need to check in at the event before you can view results. Please find your coach to get checked in!"),
            )
            return redirect("crush_lu:event_detail", event_id=event_id)

    # Get voting session
    voting_session = get_object_or_404(EventVotingSession, event=event)

    # Check if user has voted on BOTH categories (not relevant for coaches)
    presentation_vote = None
    twist_vote = None
    user_has_voted_both = False

    if not is_coach_view:
        presentation_vote = EventActivityVote.objects.filter(
            event=event,
            user=request.user,
            selected_option__activity_type="presentation_style",
        ).first()

        twist_vote = EventActivityVote.objects.filter(
            event=event,
            user=request.user,
            selected_option__activity_type="speed_dating_twist",
        ).first()

        user_has_voted_both = presentation_vote and twist_vote

        # If voting ended and user hasn't voted, redirect back to voting with message
        if not voting_session.is_voting_open and not user_has_voted_both:
            messages.warning(
                request,
                _(
                    "Voting has ended. You did not vote, but you can still participate in presentations!"
                ),
            )
            # Allow them to continue to presentations anyway
            return redirect("crush_lu:event_presentations", event_id=event_id)

    # Get vote counts for each GlobalActivityOption
    from .models import GlobalActivityOption
    from django.db.models import Count

    # Get all active global options with their vote counts for THIS event
    activity_options_with_votes = []

    for option in GlobalActivityOption.objects.filter(is_active=True).order_by(
        "activity_type", "sort_order"
    ):
        vote_count = EventActivityVote.objects.filter(
            event=event, selected_option=option
        ).count()

        # Add vote_count attribute for template compatibility
        option.vote_count = vote_count
        option.is_winner = False  # Will be set below
        activity_options_with_votes.append(option)

    # Calculate total votes
    total_votes = voting_session.total_votes

    # Calculate percentages for each option
    for option in activity_options_with_votes:
        if total_votes > 0:
            option.vote_percentage = round(option.vote_count / total_votes * 100)
        else:
            option.vote_percentage = 0

    # If voting has ended, calculate winners and initialize presentation queue
    if not voting_session.is_voting_open:
        # Calculate winners if not already done
        if not voting_session.winning_presentation_style:
            voting_session.calculate_winner()
            voting_session.initialize_presentation_queue()
            voting_session.save()

        # Mark winners in the activity_options list
        for option in activity_options_with_votes:
            if (
                option == voting_session.winning_presentation_style
                or option == voting_session.winning_speed_dating_twist
            ):
                option.is_winner = True

        # Check if presentations have started
        has_presentations = PresentationQueue.objects.filter(event=event).exists()
        presentations_skipped = voting_session.presentations_skipped

        if (has_presentations or presentations_skipped) and not is_coach_view:
            # Show results with appropriate CTA (presentations or skip banner)
            context = {
                "event": event,
                "voting_session": voting_session,
                "activity_options": activity_options_with_votes,
                "presentation_options": [
                    o for o in activity_options_with_votes if o.activity_type == "presentation_style"
                ],
                "twist_options": [
                    o for o in activity_options_with_votes if o.activity_type == "speed_dating_twist"
                ],
                "presentation_winner": voting_session.winning_presentation_style,
                "twist_winner": voting_session.winning_speed_dating_twist,
                "presentation_vote": presentation_vote,
                "twist_vote": twist_vote,
                "user_has_voted_both": user_has_voted_both,
                "total_votes": total_votes,
                "presentations_ready": has_presentations,
                "presentations_skipped": presentations_skipped,
                "is_coach_view": is_coach_view,
            }
            return render(request, "crush_lu/event_voting_results.html", context)

    # Coach-specific data: attendance and voting status
    coach_data = {}
    if is_coach_view:
        # Get all registrations for this event
        registrations = (
            EventRegistration.objects.filter(event=event)
            .select_related("user__crushprofile")
            .order_by("user__first_name", "user__last_name")
        )

        attended = registrations.filter(status="attended")
        confirmed_not_checked_in = registrations.filter(status="confirmed")

        # Get user IDs who have voted (at least one vote)
        voted_user_ids = set(
            EventActivityVote.objects.filter(event=event)
            .values_list("user_id", flat=True)
        )

        # Build list of attended users who haven't voted yet
        not_voted = []
        for reg in attended:
            if reg.user_id not in voted_user_ids:
                not_voted.append(reg)

        coach_data = {
            "attended_list": attended,
            "attended_count": attended.count(),
            "confirmed_not_checked_in": confirmed_not_checked_in,
            "confirmed_not_checked_in_count": confirmed_not_checked_in.count(),
            "not_voted_list": not_voted,
            "not_voted_count": len(not_voted),
            "voted_count": len(voted_user_ids),
        }

    context = {
        "event": event,
        "voting_session": voting_session,
        "activity_options": activity_options_with_votes,
        "presentation_options": [
            o for o in activity_options_with_votes if o.activity_type == "presentation_style"
        ],
        "twist_options": [
            o for o in activity_options_with_votes if o.activity_type == "speed_dating_twist"
        ],
        "presentation_winner": voting_session.winning_presentation_style,
        "twist_winner": voting_session.winning_speed_dating_twist,
        "presentation_vote": presentation_vote,
        "twist_vote": twist_vote,
        "user_has_voted_both": user_has_voted_both,
        "total_votes": total_votes,
        "presentations_ready": False,
        "presentations_skipped": voting_session.presentations_skipped,
        "is_coach_view": is_coach_view,
        **coach_data,
    }
    return render(request, "crush_lu/event_voting_results.html", context)


# Presentation Round Views (Phase 2)
@crush_login_required
def event_presentations(request, event_id):
    """Phase 2: Live presentation view - shows current presenter and allows rating"""
    event = get_object_or_404(MeetupEvent, id=event_id)

    # Check if activity voting is enabled for this event
    if not event.enable_activity_voting:
        messages.error(request, _("Activity voting is not enabled for this event."))
        return redirect("crush_lu:event_detail", event_id=event_id)

    # Verify user is registered for this event
    user_registration = get_object_or_404(
        EventRegistration, event=event, user=request.user
    )

    if user_registration.status not in ["confirmed", "attended"]:
        messages.error(request, _("Only confirmed attendees can view presentations."))
        return redirect("crush_lu:event_detail", event_id=event_id)

    if user_registration.status == "confirmed":
        messages.warning(
            request,
            _("You need to check in at the event before you can view presentations. Please find your coach to get checked in!"),
        )
        return redirect("crush_lu:event_detail", event_id=event_id)

    # Get the current presenter (status='presenting')
    current_presentation = (
        PresentationQueue.objects.filter(event=event, status="presenting")
        .select_related("user__crushprofile")
        .first()
    )

    # Get next presenter (for preview)
    next_presentation = (
        PresentationQueue.objects.filter(event=event, status="waiting")
        .order_by("presentation_order")
        .first()
    )

    # Get total presentation stats
    total_presentations = PresentationQueue.objects.filter(event=event).count()
    completed_presentations = PresentationQueue.objects.filter(
        event=event, status="completed"
    ).count()

    # Check if user has rated current presenter
    user_has_rated = False
    if current_presentation:
        user_has_rated = PresentationRating.objects.filter(
            event=event, presenter=current_presentation.user, rater=request.user
        ).exists()

    # Get voting session and winning presentation style
    voting_session = get_object_or_404(EventVotingSession, event=event)
    winning_style = voting_session.winning_presentation_style

    context = {
        "event": event,
        "current_presentation": current_presentation,
        "next_presentation": next_presentation,
        "total_presentations": total_presentations,
        "completed_presentations": completed_presentations,
        "user_has_rated": user_has_rated,
        "winning_style": winning_style,
        "is_presenting": current_presentation
        and current_presentation.user == request.user,
    }
    return render(request, "crush_lu/event_presentations.html", context)


@crush_login_required
@require_http_methods(["POST"])
def submit_presentation_rating(request, event_id, presenter_id):
    """Submit anonymous 1-5 star rating for a presenter"""
    event = get_object_or_404(MeetupEvent, id=event_id)
    presenter = get_object_or_404(User, id=presenter_id)

    # Check if activity voting is enabled for this event
    if not event.enable_activity_voting:
        return JsonResponse(
            {
                "success": False,
                "error": "Activity voting is not enabled for this event.",
            },
            status=403,
        )

    # Verify user is registered for this event
    user_registration = get_object_or_404(
        EventRegistration, event=event, user=request.user
    )

    if user_registration.status not in ["confirmed", "attended"]:
        return JsonResponse(
            {"success": False, "error": "Only confirmed attendees can rate."},
            status=403,
        )

    if user_registration.status == "confirmed":
        return JsonResponse(
            {"success": False, "error": "You must check in at the event before you can rate."},
            status=403,
        )

    # Cannot rate yourself
    if presenter == request.user:
        return JsonResponse(
            {"success": False, "error": "You cannot rate yourself."}, status=400
        )

    # Get impression from request (yes / no)
    impression = request.POST.get("is_positive", "").lower()
    if impression not in ("true", "false"):
        return JsonResponse(
            {"success": False, "error": "is_positive must be true or false."}, status=400
        )
    is_positive = impression == "true"

    # Create or update impression
    _, created = PresentationRating.objects.update_or_create(
        event=event,
        presenter=presenter,
        rater=request.user,
        defaults={"is_positive": is_positive},
    )

    return JsonResponse(
        {
            "success": True,
            "message": (
                _("Response submitted anonymously!") if created else _("Response updated!")
            ),
            "is_positive": is_positive,
        }
    )


# Coach Presentation Control Panel
@coach_required
def coach_presentation_control(request, event_id):
    """Coach control panel for managing presentation queue"""
    event = get_object_or_404(MeetupEvent, id=event_id)

    # Get all presentations
    presentations = (
        PresentationQueue.objects.filter(event=event)
        .select_related("user__crushprofile")
        .order_by("presentation_order")
    )

    # Get current presenter
    current_presentation = presentations.filter(status="presenting").first()

    # Get next presenter
    next_presentation = (
        presentations.filter(status="waiting").order_by("presentation_order").first()
    )

    # Get stats
    total_presentations = presentations.count()
    completed_presentations = presentations.filter(status="completed").count()

    # Get voting session and winning presentation style
    voting_session = get_object_or_404(EventVotingSession, event=event)
    winning_style = voting_session.winning_presentation_style

    context = {
        "event": event,
        "presentations": presentations,
        "current_presentation": current_presentation,
        "next_presentation": next_presentation,
        "total_presentations": total_presentations,
        "completed_presentations": completed_presentations,
        "winning_style": winning_style,
        "voting_session": voting_session,
    }
    return render(request, "crush_lu/coach_presentation_control.html", context)


@coach_required
@require_http_methods(["POST"])
def coach_advance_presentation(request, event_id):
    """Advance to next presenter in the queue"""
    event = get_object_or_404(MeetupEvent, id=event_id)

    # End current presentation if exists
    current_presentation = PresentationQueue.objects.filter(
        event=event, status="presenting"
    ).first()

    if current_presentation:
        current_presentation.status = "completed"
        current_presentation.completed_at = timezone.now()
        current_presentation.save()

    # Start next presentation
    next_presentation = (
        PresentationQueue.objects.filter(event=event, status="waiting")
        .order_by("presentation_order")
        .first()
    )

    if next_presentation:
        next_presentation.status = "presenting"
        next_presentation.started_at = timezone.now()
        next_presentation.save()

        return JsonResponse(
            {
                "success": True,
                "message": f"Now presenting: {next_presentation.user.crushprofile.display_name}",
                "presenter_name": next_presentation.user.crushprofile.display_name,
                "presentation_order": next_presentation.presentation_order,
            }
        )
    else:
        return JsonResponse(
            {
                "success": True,
                "message": "All presentations completed!",
                "all_completed": True,
            }
        )


@crush_login_required
def my_presentation_scores(request, event_id):
    """Phase 2 complete page — first impressions are kept private; matches revealed in Phase 3."""
    event = get_object_or_404(MeetupEvent, id=event_id)
    get_object_or_404(EventRegistration, event=event, user=request.user, status="attended")
    return render(request, "crush_lu/my_presentation_scores.html", {"event": event})


@crush_login_required
def get_current_presenter_api(request, event_id):
    """API endpoint to get current presenter status (for polling)"""
    event = get_object_or_404(MeetupEvent, id=event_id)

    # Allow event coaches and superusers
    is_coach = event.coaches.filter(user=request.user).exists()
    if not request.user.is_superuser and not is_coach:
        # Verify user is registered for this event
        try:
            user_registration = EventRegistration.objects.get(
                event=event, user=request.user
            )
            if user_registration.status not in ["confirmed", "attended"]:
                return JsonResponse({"error": "Not authorized"}, status=403)
        except EventRegistration.DoesNotExist:
            return JsonResponse({"error": "Not registered for this event"}, status=403)

    # Get current presenter
    current_presentation = (
        PresentationQueue.objects.filter(event=event, status="presenting")
        .select_related("user__crushprofile")
        .first()
    )

    if current_presentation:
        # Check if user has rated this presenter
        user_has_rated = PresentationRating.objects.filter(
            event=event, presenter=current_presentation.user, rater=request.user
        ).exists()

        # Calculate time remaining
        time_remaining = 90
        if current_presentation.started_at:
            from django.utils import timezone

            elapsed = (timezone.now() - current_presentation.started_at).total_seconds()
            time_remaining = max(0, int(90 - elapsed))

        return JsonResponse(
            {
                "has_presenter": True,
                "presenter_id": current_presentation.user.id,
                "presenter_name": current_presentation.user.crushprofile.display_name,
                "presentation_order": current_presentation.presentation_order,
                "started_at": (
                    current_presentation.started_at.isoformat()
                    if current_presentation.started_at
                    else None
                ),
                "time_remaining": time_remaining,
                "user_has_rated": user_has_rated,
                "is_presenting": current_presentation.user == request.user,
            }
        )
    else:
        # Check if all presentations are completed
        total_presentations = PresentationQueue.objects.filter(event=event).count()
        completed_presentations = PresentationQueue.objects.filter(
            event=event, status="completed"
        ).count()

        return JsonResponse(
            {
                "has_presenter": False,
                "all_completed": total_presentations > 0
                and completed_presentations == total_presentations,
                "completed_count": completed_presentations,
                "total_count": total_presentations,
            }
        )


# Demo/Guided Tour View
def voting_demo(request):
    """Interactive demo of the voting system for new users"""
    from .models import GlobalActivityOption

    # Get actual activity options from database
    presentation_options = GlobalActivityOption.objects.filter(
        activity_type="presentation_style", is_active=True
    ).order_by("sort_order")

    twist_options = GlobalActivityOption.objects.filter(
        activity_type="speed_dating_twist", is_active=True
    ).order_by("sort_order")

    context = {
        "presentation_options": presentation_options,
        "twist_options": twist_options,
    }
    return render(request, "crush_lu/voting_demo.html", context)


# ── Speed Dating TV Display ──────────────────────────────────────────────────

def speed_dating_tv_display(request, event_id):
    """Full-screen TV/projector display for speed dating events. No auth required."""
    event = get_object_or_404(MeetupEvent, id=event_id, is_published=True)

    attended_count = EventRegistration.objects.filter(
        event=event, status="attended"
    ).count()
    confirmed_count = EventRegistration.objects.filter(
        event=event, status__in=["confirmed", "attended"]
    ).count()

    context = {
        "event": event,
        "attended_count": attended_count,
        "confirmed_count": confirmed_count,
    }
    return render(request, "crush_lu/speed_dating_display.html", context)


def speed_dating_tv_display_data(request, event_id):
    """JSON polling endpoint for the speed dating TV display."""
    try:
        event = MeetupEvent.objects.get(id=event_id, is_published=True)
    except MeetupEvent.DoesNotExist:
        return JsonResponse({"error": "Event not found"}, status=404)

    attended_count = EventRegistration.objects.filter(
        event=event, status="attended"
    ).count()
    confirmed_count = EventRegistration.objects.filter(
        event=event, status__in=["confirmed", "attended"]
    ).count()

    # Gender breakdown (exclude "Prefer not to say")
    gender_counts: dict[str, int] = {}
    for reg in (
        EventRegistration.objects.filter(event=event, status__in=["confirmed", "attended"])
        .select_related("user__crushprofile")
    ):
        profile = getattr(reg.user, "crushprofile", None)
        gender = getattr(profile, "gender", "") or ""
        if gender and gender not in ("P",):
            gender_counts[gender] = gender_counts.get(gender, 0) + 1

    phase = "welcome"
    phase_data: dict = {}

    # Phase 3 — Speed Dating (most advanced phase wins)
    pairs_qs = SpeedDatingPair.objects.filter(event=event)
    if pairs_qs.exists():
        phase = "speed_dating"
        agg = pairs_qs.aggregate(max_round=Max("round_number"))
        current_round = agg["max_round"] or 1
        phase_data = {
            "current_round": current_round,
            "total_pairs": pairs_qs.filter(round_number=current_round).count(),
        }
    else:
        # Phase 2 — Presentations
        current_presenter = (
            PresentationQueue.objects.filter(event=event, status="presenting")
            .select_related("user__crushprofile")
            .first()
        )
        if current_presenter:
            phase = "presentations"
            completed = PresentationQueue.objects.filter(event=event, status="completed").count()
            total = PresentationQueue.objects.filter(event=event).exclude(status="skipped").count()
            profile = getattr(current_presenter.user, "crushprofile", None)
            presenter_name = (
                (profile.display_name if profile else None)
                or current_presenter.user.first_name
                or "Anonymous"
            )
            phase_data = {
                "presenter_name": presenter_name,
                "completed_count": completed,
                "total_count": total,
                "progress_pct": int(completed / total * 100) if total else 0,
            }
        else:
            # Phase 1 — Voting
            try:
                voting_session = EventVotingSession.objects.select_related(
                    "winning_presentation_style", "winning_speed_dating_twist"
                ).get(event=event)

                if voting_session.is_voting_open:
                    phase = "voting"
                    pres_votes = (
                        EventActivityVote.objects.filter(
                            event=event,
                            selected_option__activity_type="presentation_style",
                        )
                        .values("selected_option__display_name", "selected_option__activity_variant")
                        .annotate(count=Count("id"))
                        .order_by("-count")
                    )
                    twist_votes = (
                        EventActivityVote.objects.filter(
                            event=event,
                            selected_option__activity_type="speed_dating_twist",
                        )
                        .values("selected_option__display_name", "selected_option__activity_variant")
                        .annotate(count=Count("id"))
                        .order_by("-count")
                    )
                    phase_data = {
                        "total_votes": voting_session.total_votes,
                        "time_remaining": int(voting_session.time_remaining),
                        "presentation_votes": [
                            {
                                "name": v["selected_option__display_name"],
                                "variant": v["selected_option__activity_variant"],
                                "count": v["count"],
                            }
                            for v in pres_votes
                        ],
                        "twist_votes": [
                            {
                                "name": v["selected_option__display_name"],
                                "variant": v["selected_option__activity_variant"],
                                "count": v["count"],
                            }
                            for v in twist_votes
                        ],
                    }
                elif voting_session.winning_presentation_style_id:
                    phase = "voting_results"
                    phase_data = {
                        "winning_style": voting_session.winning_presentation_style.display_name,
                        "winning_twist": (
                            voting_session.winning_speed_dating_twist.display_name
                            if voting_session.winning_speed_dating_twist
                            else None
                        ),
                        "total_votes": voting_session.total_votes,
                    }
            except EventVotingSession.DoesNotExist:
                pass

    return JsonResponse(
        {
            "phase": phase,
            "attended_count": attended_count,
            "confirmed_count": confirmed_count,
            "max_participants": event.max_participants,
            "gender_counts": gender_counts,
            "phase_data": phase_data,
        }
    )
