"""
Event management views for Crush.lu

Handles event listing, registration, voting, speed-dating presentations, and ratings.
"""
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.models import User
from django.contrib import messages
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods
from django.http import JsonResponse
from django.db.models import Q, Avg, Count
import logging

from ..models import (
    MeetupEvent, EventRegistration, EventInvitation, CrushProfile, CrushCoach,
    EventActivityVote, EventVotingSession, PresentationQueue, PresentationRating
)
from ..forms import EventRegistrationForm
from ..decorators import crush_login_required
from ..email_helpers import (
    send_event_registration_confirmation,
    send_event_waitlist_notification,
    send_event_cancellation_confirmation
)

logger = logging.getLogger(__name__)


def event_list(request):
    """List of upcoming events - filters private invitation events"""
    # Base query: published, non-cancelled, future events
    events = MeetupEvent.objects.filter(
        is_published=True,
        is_cancelled=False,
        date_time__gte=timezone.now()
    ).order_by('date_time')

    # FILTER OUT PRIVATE EVENTS for non-invited users
    if request.user.is_authenticated:
        # Check if user has approved EventInvitation (external guests)
        user_invitations = EventInvitation.objects.filter(
            created_user=request.user,
            approval_status='approved'
        ).values_list('event_id', flat=True)

        # Show: public events + private events they're invited to (either as existing user OR external guest)
        events = events.filter(
            Q(is_private_invitation=False) |  # Public events
            Q(id__in=user_invitations) |  # Private events with approved external invitation
            Q(invited_users=request.user)  # Private events where they're invited as existing user
        )
    else:
        # Public visitors: only see public events
        events = events.filter(is_private_invitation=False)

    # Filter by event type if provided
    event_type = request.GET.get('type')
    if event_type:
        events = events.filter(event_type=event_type)

    # For coaches: show unpublished events count
    unpublished_count = 0
    if request.user.is_authenticated:
        try:
            coach = CrushCoach.objects.get(user=request.user, is_active=True)
            unpublished_count = MeetupEvent.objects.filter(
                is_published=False,
                is_cancelled=False,
                date_time__gte=timezone.now()
            ).count()
        except CrushCoach.DoesNotExist:
            pass

    context = {
        'events': events,
        'event_types': MeetupEvent.EVENT_TYPE_CHOICES,
        'unpublished_count': unpublished_count,
    }
    return render(request, 'crush_lu/events/event_list.html', context)


def event_detail(request, event_id):
    """Event detail page with access control for private events"""
    event = get_object_or_404(MeetupEvent, id=event_id, is_published=True)

    # ACCESS CONTROL for private invitation events
    if event.is_private_invitation:
        if not request.user.is_authenticated:
            messages.error(request, _('This is a private invitation-only event. Please log in.'))
            return redirect('crush_lu:crush_login')

        # Check if user has approved external guest invitation OR is invited as existing user
        has_external_invitation = EventInvitation.objects.filter(
            event=event,
            created_user=request.user,
            approval_status='approved'
        ).exists()

        is_invited_existing_user = event.invited_users.filter(id=request.user.id).exists()

        if not has_external_invitation and not is_invited_existing_user:
            messages.error(request, _('You do not have access to this private event.'))
            return redirect('crush_lu:event_list')

    # Check if user is registered
    user_registration = None
    if request.user.is_authenticated:
        user_registration = EventRegistration.objects.filter(
            event=event,
            user=request.user
        ).first()

    context = {
        'event': event,
        'user_registration': user_registration,
    }
    return render(request, 'crush_lu/events/event_detail.html', context)


@crush_login_required
def event_register(request, event_id):
    """Register for an event - bypasses approval for invited guests"""
    event = get_object_or_404(MeetupEvent, id=event_id)

    # FOR PRIVATE INVITATION EVENTS: Bypass normal profile approval flow
    if event.is_private_invitation:
        # Check if user is invited as existing user OR has approved external invitation
        is_invited_existing_user = event.invited_users.filter(id=request.user.id).exists()

        external_invitation = EventInvitation.objects.filter(
            event=event,
            created_user=request.user,
            approval_status='approved'
        ).first()

        if not is_invited_existing_user and not external_invitation:
            messages.error(request, _('You do not have an approved invitation for this event.'))
            return redirect('crush_lu:event_detail', event_id=event_id)

        # EXISTING USERS: No profile creation needed - use their existing profile
        if is_invited_existing_user:
            try:
                profile = CrushProfile.objects.get(user=request.user)
                # Existing users keep their own profile approval status
            except CrushProfile.DoesNotExist:
                # SECURITY FIX: Redirect to profile creation instead of auto-creating
                # This ensures proper age verification and data collection
                messages.warning(request, _(
                    'Please complete your profile before registering for events. '
                    'This is required for all users, even with invitations.'
                ))
                return redirect('crush_lu:create_profile')

        # EXTERNAL GUESTS: Must have profile from invitation acceptance
        else:
            try:
                profile = CrushProfile.objects.get(user=request.user)
                # External guests already have profile created during invitation acceptance
                # with proper age verification and date of birth
            except CrushProfile.DoesNotExist:
                # SECURITY: This should never happen - external guests must accept invitation first
                # which creates their profile with age verification
                logger.error(
                    f"Security issue: External guest {request.user.email} trying to register "
                    f"without profile. Invitation ID: {external_invitation.id if external_invitation else 'None'}"
                )
                messages.error(request, _(
                    'Your profile is missing. Please contact support for assistance.'
                ))
                return redirect('crush_lu:event_detail', event_id=event_id)
    else:
        # NORMAL EVENT: Require approved profile
        try:
            profile = CrushProfile.objects.get(user=request.user)
            if not profile.is_approved:
                messages.error(request, _('Your profile must be approved before registering for events.'))
                return redirect('crush_lu:event_detail', event_id=event_id)
        except CrushProfile.DoesNotExist:
            messages.error(request, _('Please create a profile first.'))
            return redirect('crush_lu:create_profile')

    # Check if already registered
    if EventRegistration.objects.filter(event=event, user=request.user).exists():
        messages.warning(request, _('You are already registered for this event.'))
        return redirect('crush_lu:event_detail', event_id=event_id)

    # Check if registration is open
    if not event.is_registration_open:
        messages.error(request, _('Registration is not available for this event.'))
        return redirect('crush_lu:event_detail', event_id=event_id)

    if request.method == 'POST':
        form = EventRegistrationForm(request.POST)
        if form.is_valid():
            registration = form.save(commit=False)
            registration.event = event
            registration.user = request.user

            # Set status based on availability
            if event.is_full:
                registration.status = 'waitlist'
                messages.info(request, _('Event is full. You have been added to the waitlist.'))
            else:
                registration.status = 'confirmed'
                messages.success(request, _('Successfully registered for the event!'))

            registration.save()

            # Send confirmation or waitlist email
            try:
                if registration.status == 'confirmed':
                    send_event_registration_confirmation(registration, request)
                elif registration.status == 'waitlist':
                    send_event_waitlist_notification(registration, request)
            except Exception as e:
                logger.error(f"Failed to send event registration email: {e}")

            # Return HTMX partial or redirect
            if request.headers.get('HX-Request'):
                return render(request, 'crush_lu/_event_registration_success.html', {
                    'event': event,
                    'registration': registration,
                })
            return redirect('crush_lu:dashboard')
        else:
            # Form invalid - for HTMX, re-render the form with errors
            if request.headers.get('HX-Request'):
                return render(request, 'crush_lu/_event_registration_form.html', {
                    'event': event,
                    'form': form,
                })
    else:
        form = EventRegistrationForm()

    context = {
        'event': event,
        'form': form,
    }
    return render(request, 'crush_lu/events/event_register.html', context)


@crush_login_required
def event_cancel(request, event_id):
    """Cancel event registration"""
    event = get_object_or_404(MeetupEvent, id=event_id)
    registration = get_object_or_404(
        EventRegistration,
        event=event,
        user=request.user
    )

    if request.method == 'POST':
        registration.status = 'cancelled'
        registration.save()
        messages.success(request, _('Your registration has been cancelled.'))

        # Send cancellation confirmation email
        try:
            send_event_cancellation_confirmation(request.user, event, request)
        except Exception as e:
            logger.error(f"Failed to send event cancellation confirmation: {e}")

        return redirect('crush_lu:dashboard')

    context = {
        'event': event,
        'registration': registration,
    }
    return render(request, 'crush_lu/events/event_cancel.html', context)


@crush_login_required
def event_voting_lobby(request, event_id):
    """Pre-voting lobby with countdown and activity previews"""
    event = get_object_or_404(MeetupEvent, id=event_id)

    # Verify user is registered for this event
    user_registration = get_object_or_404(
        EventRegistration,
        event=event,
        user=request.user
    )

    # Only confirmed or attended registrations can vote
    if user_registration.status not in ['confirmed', 'attended']:
        messages.error(request, _('Only confirmed attendees can access event voting.'))
        return redirect('crush_lu:event_detail', event_id=event_id)

    # Get or create voting session
    voting_session, created = EventVotingSession.objects.get_or_create(event=event)

    # Get all activity options
    activity_options = EventActivityOption.objects.filter(event=event).order_by('activity_type', 'activity_variant')

    # Check if user has already voted
    user_vote = EventActivityVote.objects.filter(event=event, user=request.user).first()

    # Get total confirmed attendees count
    total_attendees = EventRegistration.objects.filter(
        event=event,
        status__in=['confirmed', 'attended']
    ).count()

    context = {
        'event': event,
        'voting_session': voting_session,
        'activity_options': activity_options,
        'user_vote': user_vote,
        'total_attendees': total_attendees,
    }
    return render(request, 'crush_lu/events/event_voting_lobby.html', context)


@crush_login_required
def event_activity_vote(request, event_id):
    """Active voting interface"""
    event = get_object_or_404(MeetupEvent, id=event_id)

    # Verify user is registered for this event
    user_registration = get_object_or_404(
        EventRegistration,
        event=event,
        user=request.user
    )

    if user_registration.status not in ['confirmed', 'attended']:
        messages.error(request, _('Only confirmed attendees can vote.'))
        return redirect('crush_lu:event_detail', event_id=event_id)

    # Get voting session
    voting_session = get_object_or_404(EventVotingSession, event=event)

    # Check if voting is open
    if not voting_session.is_voting_open:
        messages.warning(request, _('Voting is not currently open for this event.'))
        return redirect('crush_lu:event_voting_lobby', event_id=event_id)

    if request.method == 'POST':
        presentation_option_id = request.POST.get('presentation_option_id')
        twist_option_id = request.POST.get('twist_option_id')

        if not presentation_option_id or not twist_option_id:
            messages.error(request, _('Please vote on BOTH categories: Presentation Style AND Speed Dating Twist.'))
        else:
            try:
                from ..models import GlobalActivityOption

                presentation_option = GlobalActivityOption.objects.get(
                    id=presentation_option_id,
                    activity_type='presentation_style',
                    is_active=True
                )
                twist_option = GlobalActivityOption.objects.get(
                    id=twist_option_id,
                    activity_type='speed_dating_twist',
                    is_active=True
                )

                # Handle presentation style vote
                presentation_vote = EventActivityVote.objects.filter(
                    event=event,
                    user=request.user,
                    selected_option__activity_type='presentation_style'
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
                        selected_option=presentation_option
                    )

                # Handle speed dating twist vote
                twist_vote = EventActivityVote.objects.filter(
                    event=event,
                    user=request.user,
                    selected_option__activity_type='speed_dating_twist'
                ).first()

                if twist_vote:
                    # Update existing vote
                    twist_vote.selected_option = twist_option
                    twist_vote.save()
                else:
                    # Create new vote
                    EventActivityVote.objects.create(
                        event=event,
                        user=request.user,
                        selected_option=twist_option
                    )

                # Update total votes only if this is first complete vote
                if not (presentation_vote and twist_vote):
                    voting_session.total_votes += 1
                    voting_session.save()

                messages.success(request, _('Your votes have been recorded for both categories!'))
                return redirect('crush_lu:event_voting_results', event_id=event_id)

            except GlobalActivityOption.DoesNotExist:
                messages.error(request, _('Invalid activity option selected.'))

    # Get all GLOBAL activity options (not per-event anymore!)
    from ..models import GlobalActivityOption
    presentation_style_options = GlobalActivityOption.objects.filter(
        activity_type='presentation_style',
        is_active=True
    ).order_by('sort_order')

    speed_dating_twist_options = GlobalActivityOption.objects.filter(
        activity_type='speed_dating_twist',
        is_active=True
    ).order_by('sort_order')

    # Check if user has voted on BOTH categories
    presentation_vote = EventActivityVote.objects.filter(
        event=event,
        user=request.user,
        selected_option__activity_type='presentation_style'
    ).first()

    twist_vote = EventActivityVote.objects.filter(
        event=event,
        user=request.user,
        selected_option__activity_type='speed_dating_twist'
    ).first()

    context = {
        'event': event,
        'voting_session': voting_session,
        'presentation_style_options': presentation_style_options,
        'speed_dating_twist_options': speed_dating_twist_options,
        'presentation_vote': presentation_vote,
        'twist_vote': twist_vote,
        'has_voted_both': presentation_vote and twist_vote,
    }
    return render(request, 'crush_lu/events/event_activity_vote.html', context)


@crush_login_required
def event_voting_results(request, event_id):
    """Display voting results and transition to presentations when ready"""
    event = get_object_or_404(MeetupEvent, id=event_id)

    # Verify user is registered for this event
    user_registration = get_object_or_404(
        EventRegistration,
        event=event,
        user=request.user
    )

    if user_registration.status not in ['confirmed', 'attended']:
        messages.error(request, _('Only confirmed attendees can view results.'))
        return redirect('crush_lu:event_detail', event_id=event_id)

    # Get voting session
    voting_session = get_object_or_404(EventVotingSession, event=event)

    # Check if user has voted on BOTH categories
    presentation_vote = EventActivityVote.objects.filter(
        event=event,
        user=request.user,
        selected_option__activity_type='presentation_style'
    ).first()

    twist_vote = EventActivityVote.objects.filter(
        event=event,
        user=request.user,
        selected_option__activity_type='speed_dating_twist'
    ).first()

    user_has_voted_both = presentation_vote and twist_vote

    # If voting ended and user hasn't voted, redirect back to voting with message
    if not voting_session.is_voting_open and not user_has_voted_both:
        messages.warning(request, _('Voting has ended. You did not vote, but you can still participate in presentations!'))
        # Allow them to continue to presentations anyway
        return redirect('crush_lu:event_presentations', event_id=event_id)

    # Get vote counts for each GlobalActivityOption
    from ..models import GlobalActivityOption

    # Get all active global options with their vote counts for THIS event
    activity_options_with_votes = []

    for option in GlobalActivityOption.objects.filter(is_active=True).order_by('activity_type', 'sort_order'):
        vote_count = EventActivityVote.objects.filter(
            event=event,
            selected_option=option
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
            if option == voting_session.winning_presentation_style or option == voting_session.winning_speed_dating_twist:
                option.is_winner = True

        # Check if presentations have started
        has_presentations = PresentationQueue.objects.filter(event=event).exists()

        if has_presentations:
            # Automatically redirect to presentations after 5 seconds
            messages.success(request, _('Voting complete! Redirecting to presentations...'))
            context = {
                'event': event,
                'voting_session': voting_session,
                'activity_options': activity_options_with_votes,
                'user_has_voted_both': user_has_voted_both,
                'total_votes': total_votes,
                'redirect_to_presentations': True,  # Signal to template
            }
            return render(request, 'crush_lu/events/event_voting_results.html', context)

    context = {
        'event': event,
        'voting_session': voting_session,
        'activity_options': activity_options_with_votes,
        'user_has_voted_both': user_has_voted_both,
        'total_votes': total_votes,
        'redirect_to_presentations': False,
    }
    return render(request, 'crush_lu/events/event_voting_results.html', context)


@crush_login_required
def event_presentations(request, event_id):
    """Phase 2: Live presentation view - shows current presenter and allows rating"""
    event = get_object_or_404(MeetupEvent, id=event_id)

    # Verify user is registered for this event
    user_registration = get_object_or_404(
        EventRegistration,
        event=event,
        user=request.user
    )

    if user_registration.status not in ['confirmed', 'attended']:
        messages.error(request, _('Only confirmed attendees can view presentations.'))
        return redirect('crush_lu:event_detail', event_id=event_id)

    # Get the current presenter (status='presenting')
    current_presentation = PresentationQueue.objects.filter(
        event=event,
        status='presenting'
    ).select_related('user__crushprofile').first()

    # Get next presenter (for preview)
    next_presentation = PresentationQueue.objects.filter(
        event=event,
        status='waiting'
    ).order_by('presentation_order').first()

    # Get total presentation stats
    total_presentations = PresentationQueue.objects.filter(event=event).count()
    completed_presentations = PresentationQueue.objects.filter(event=event, status='completed').count()

    # Check if user has rated current presenter
    user_has_rated = False
    if current_presentation:
        user_has_rated = PresentationRating.objects.filter(
            event=event,
            presenter=current_presentation.user,
            rater=request.user
        ).exists()

    # Get voting session and winning presentation style
    voting_session = get_object_or_404(EventVotingSession, event=event)
    winning_style = voting_session.winning_presentation_style

    context = {
        'event': event,
        'current_presentation': current_presentation,
        'next_presentation': next_presentation,
        'total_presentations': total_presentations,
        'completed_presentations': completed_presentations,
        'user_has_rated': user_has_rated,
        'winning_style': winning_style,
        'is_presenting': current_presentation and current_presentation.user == request.user,
    }
    return render(request, 'crush_lu/events/event_presentations.html', context)


@crush_login_required
@require_http_methods(["POST"])
def submit_presentation_rating(request, event_id, presenter_id):
    """Submit anonymous 1-5 star rating for a presenter"""
    event = get_object_or_404(MeetupEvent, id=event_id)
    presenter = get_object_or_404(User, id=presenter_id)

    # Verify user is registered for this event
    user_registration = get_object_or_404(
        EventRegistration,
        event=event,
        user=request.user
    )

    if user_registration.status not in ['confirmed', 'attended']:
        return JsonResponse({'success': False, 'error': 'Only confirmed attendees can rate.'}, status=403)

    # Cannot rate yourself
    if presenter == request.user:
        return JsonResponse({'success': False, 'error': 'You cannot rate yourself.'}, status=400)

    # Get rating from request
    rating_value = request.POST.get('rating')

    try:
        rating_value = int(rating_value)
        if rating_value < 1 or rating_value > 5:
            raise ValueError
    except (TypeError, ValueError):
        return JsonResponse({'success': False, 'error': 'Rating must be between 1 and 5.'}, status=400)

    # Create or update rating
    rating, created = PresentationRating.objects.update_or_create(
        event=event,
        presenter=presenter,
        rater=request.user,
        defaults={'rating': rating_value}
    )

    return JsonResponse({
        'success': True,
        'message': 'Rating submitted anonymously!' if created else 'Rating updated!',
        'rating': rating_value
    })


@crush_login_required
def my_presentation_scores(request, event_id):
    """Show user their personal presentation scores (private view)"""
    event = get_object_or_404(MeetupEvent, id=event_id)

    # Verify user is registered for this event
    user_registration = get_object_or_404(
        EventRegistration,
        event=event,
        user=request.user
    )

    if user_registration.status not in ['confirmed', 'attended']:
        messages.error(request, _('Only confirmed attendees can view scores.'))
        return redirect('crush_lu:event_detail', event_id=event_id)

    # Check if all presentations are completed
    total_presentations = PresentationQueue.objects.filter(event=event).count()
    completed_presentations = PresentationQueue.objects.filter(event=event, status='completed').count()

    all_completed = total_presentations > 0 and completed_presentations == total_presentations

    if not all_completed:
        messages.warning(request, _('Scores will be available after all presentations are completed.'))
        return redirect('crush_lu:event_presentations', event_id=event_id)

    # Get ratings received by this user
    ratings_received = PresentationRating.objects.filter(
        event=event,
        presenter=request.user
    ).select_related('rater__crushprofile')

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
    all_participants = PresentationQueue.objects.filter(event=event).values_list('user_id', flat=True)

    participant_scores = []
    for participant_id in all_participants:
        participant_ratings = PresentationRating.objects.filter(
            event=event,
            presenter_id=participant_id
        )
        if participant_ratings.exists():
            avg = participant_ratings.aggregate(Avg('rating'))['rating__avg']
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
        'event': event,
        'average_score': average_score,
        'rating_count': rating_count,
        'individual_ratings': individual_ratings,
        'rating_distribution': rating_distribution,
        'user_rank': user_rank,
        'total_participants': len(participant_scores),
    }
    return render(request, 'crush_lu/events/my_presentation_scores.html', context)


@crush_login_required
def get_current_presenter_api(request, event_id):
    """API endpoint to get current presenter status (for polling)"""
    event = get_object_or_404(MeetupEvent, id=event_id)

    # Verify user is registered for this event
    try:
        user_registration = EventRegistration.objects.get(
            event=event,
            user=request.user
        )
        if user_registration.status not in ['confirmed', 'attended']:
            return JsonResponse({'error': 'Not authorized'}, status=403)
    except EventRegistration.DoesNotExist:
        return JsonResponse({'error': 'Not registered for this event'}, status=403)

    # Get current presenter
    current_presentation = PresentationQueue.objects.filter(
        event=event,
        status='presenting'
    ).select_related('user__crushprofile').first()

    if current_presentation:
        # Check if user has rated this presenter
        user_has_rated = PresentationRating.objects.filter(
            event=event,
            presenter=current_presentation.user,
            rater=request.user
        ).exists()

        # Calculate time remaining
        time_remaining = 90
        if current_presentation.started_at:
            elapsed = (timezone.now() - current_presentation.started_at).total_seconds()
            time_remaining = max(0, int(90 - elapsed))

        return JsonResponse({
            'has_presenter': True,
            'presenter_id': current_presentation.user.id,
            'presenter_name': current_presentation.user.crushprofile.display_name,
            'presentation_order': current_presentation.presentation_order,
            'started_at': current_presentation.started_at.isoformat() if current_presentation.started_at else None,
            'time_remaining': time_remaining,
            'user_has_rated': user_has_rated,
            'is_presenting': current_presentation.user == request.user
        })
    else:
        # Check if all presentations are completed
        total_presentations = PresentationQueue.objects.filter(event=event).count()
        completed_presentations = PresentationQueue.objects.filter(event=event, status='completed').count()

        return JsonResponse({
            'has_presenter': False,
            'all_completed': total_presentations > 0 and completed_presentations == total_presentations,
            'completed_count': completed_presentations,
            'total_count': total_presentations
        })


def voting_demo(request):
    """Interactive demo of the voting system for new users"""
    from ..models import GlobalActivityOption

    # Get actual activity options from database
    presentation_options = GlobalActivityOption.objects.filter(
        activity_type='presentation_style',
        is_active=True
    ).order_by('sort_order')

    twist_options = GlobalActivityOption.objects.filter(
        activity_type='speed_dating_twist',
        is_active=True
    ).order_by('sort_order')

    context = {
        'presentation_options': presentation_options,
        'twist_options': twist_options,
    }
    return render(request, 'crush_lu/events/voting_demo.html', context)
