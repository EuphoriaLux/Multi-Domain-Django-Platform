from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.models import User
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
import logging

logger = logging.getLogger(__name__)

from .models import (
    CrushCoach,
    MeetupEvent,
    EventRegistration,
    EventActivityOption,
    EventActivityVote,
    EventVotingSession,
    PresentationQueue,
    PresentationRating,
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

    # Only confirmed or attended registrations can vote
    if user_registration.status not in ["confirmed", "attended"]:
        messages.error(request, _("Only confirmed attendees can access event voting."))
        return redirect("crush_lu:event_detail", event_id=event_id)

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

    context = {
        "event": event,
        "voting_session": voting_session,
        "presentation_options": presentation_options,
        "twist_options": twist_options,
        "presentation_vote": presentation_vote,
        "twist_vote": twist_vote,
        "has_voted_both": has_voted_both,
        "total_attendees": total_attendees,
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

                # Handle presentation style vote
                presentation_vote = EventActivityVote.objects.filter(
                    event=event,
                    user=request.user,
                    selected_option__activity_type="presentation_style",
                ).first()

                if presentation_vote:
                    # Update existing vote
                    presentation_vote.selected_option = presentation_option
                    presentation_vote.save()
                else:
                    # Create new vote
                    EventActivityVote.objects.create(
                        event=event,
                        user=request.user,
                        selected_option=presentation_option,
                    )

                # Handle speed dating twist vote
                twist_vote = EventActivityVote.objects.filter(
                    event=event,
                    user=request.user,
                    selected_option__activity_type="speed_dating_twist",
                ).first()

                if twist_vote:
                    # Update existing vote
                    twist_vote.selected_option = twist_option
                    twist_vote.save()
                else:
                    # Create new vote
                    EventActivityVote.objects.create(
                        event=event, user=request.user, selected_option=twist_option
                    )

                # Update total votes only if this is first complete vote
                if not (presentation_vote and twist_vote):
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

    presentation_style_options = GlobalActivityOption.objects.filter(
        activity_type="presentation_style", is_active=True
    ).order_by("sort_order")

    speed_dating_twist_options = GlobalActivityOption.objects.filter(
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
        "presentation_style_options": presentation_style_options,
        "speed_dating_twist_options": speed_dating_twist_options,
        "presentation_vote": presentation_vote,
        "twist_vote": twist_vote,
        "has_voted_both": presentation_vote and twist_vote,
    }
    return render(request, "crush_lu/event_activity_vote.html", context)


@crush_login_required
def event_voting_results(request, event_id):
    """Display voting results and transition to presentations when ready"""
    event = get_object_or_404(MeetupEvent, id=event_id)

    # Verify user is registered for this event
    user_registration = get_object_or_404(
        EventRegistration, event=event, user=request.user
    )

    if user_registration.status not in ["confirmed", "attended"]:
        messages.error(request, _("Only confirmed attendees can view results."))
        return redirect("crush_lu:event_detail", event_id=event_id)

    # Get voting session
    voting_session = get_object_or_404(EventVotingSession, event=event)

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

        if has_presentations:
            # Automatically redirect to presentations after 5 seconds
            messages.success(
                request, _("Voting complete! Redirecting to presentations...")
            )
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
                "redirect_to_presentations": True,
            }
            return render(request, "crush_lu/event_voting_results.html", context)

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
        "redirect_to_presentations": False,
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

    # Cannot rate yourself
    if presenter == request.user:
        return JsonResponse(
            {"success": False, "error": "You cannot rate yourself."}, status=400
        )

    # Get rating from request
    rating_value = request.POST.get("rating")

    try:
        rating_value = int(rating_value)
        if rating_value < 1 or rating_value > 5:
            raise ValueError
    except (TypeError, ValueError):
        return JsonResponse(
            {"success": False, "error": "Rating must be between 1 and 5."}, status=400
        )

    # Create or update rating
    rating, created = PresentationRating.objects.update_or_create(
        event=event,
        presenter=presenter,
        rater=request.user,
        defaults={"rating": rating_value},
    )

    return JsonResponse(
        {
            "success": True,
            "message": (
                "Rating submitted anonymously!" if created else "Rating updated!"
            ),
            "rating": rating_value,
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
    """Show user their personal presentation scores (private view)"""
    event = get_object_or_404(MeetupEvent, id=event_id)

    # Verify user is registered for this event
    user_registration = get_object_or_404(
        EventRegistration, event=event, user=request.user
    )

    if user_registration.status not in ["confirmed", "attended"]:
        messages.error(request, _("Only confirmed attendees can view scores."))
        return redirect("crush_lu:event_detail", event_id=event_id)

    # Check if all presentations are completed
    total_presentations = PresentationQueue.objects.filter(event=event).count()
    completed_presentations = PresentationQueue.objects.filter(
        event=event, status="completed"
    ).count()

    all_completed = (
        total_presentations > 0 and completed_presentations == total_presentations
    )

    if not all_completed:
        messages.warning(
            request,
            _("Scores will be available after all presentations are completed."),
        )
        return redirect("crush_lu:event_presentations", event_id=event_id)

    # Get ratings received by this user
    ratings_received = PresentationRating.objects.filter(
        event=event, presenter=request.user
    ).select_related("rater__crushprofile")

    # Calculate average score
    if ratings_received.exists():
        total_score = sum(r.rating for r in ratings_received)
        average_score = total_score / ratings_received.count()
        rating_count = ratings_received.count()
    else:
        average_score = 0
        rating_count = 0

    # Get individual ratings (without showing who rated)
    individual_ratings = [r.rating for r in ratings_received]
    individual_ratings.sort(reverse=True)  # Sort highest to lowest

    # Calculate rating distribution
    rating_distribution = {
        5: ratings_received.filter(rating=5).count(),
        4: ratings_received.filter(rating=4).count(),
        3: ratings_received.filter(rating=3).count(),
        2: ratings_received.filter(rating=2).count(),
        1: ratings_received.filter(rating=1).count(),
    }

    # Get user's rank among all participants
    from django.db.models import Avg, Count

    all_participants = PresentationQueue.objects.filter(event=event).values_list(
        "user_id", flat=True
    )

    participant_scores = []
    for participant_id in all_participants:
        participant_ratings = PresentationRating.objects.filter(
            event=event, presenter_id=participant_id
        )
        if participant_ratings.exists():
            avg = participant_ratings.aggregate(Avg("rating"))["rating__avg"]
            participant_scores.append((participant_id, avg))

    # Sort by average score (highest first)
    participant_scores.sort(key=lambda x: x[1], reverse=True)

    # Find user's rank
    user_rank = None
    for idx, (participant_id, score) in enumerate(participant_scores, start=1):
        if participant_id == request.user.id:
            user_rank = idx
            break

    context = {
        "event": event,
        "average_score": average_score,
        "rating_count": rating_count,
        "individual_ratings": individual_ratings,
        "rating_distribution": rating_distribution,
        "user_rank": user_rank,
        "total_participants": len(participant_scores),
    }
    return render(request, "crush_lu/my_presentation_scores.html", context)


@crush_login_required
def get_current_presenter_api(request, event_id):
    """API endpoint to get current presenter status (for polling)"""
    event = get_object_or_404(MeetupEvent, id=event_id)

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
