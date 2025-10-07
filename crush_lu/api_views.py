from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_http_methods
from django.contrib.auth.decorators import login_required
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone

from .models import (
    MeetupEvent, EventRegistration, EventVotingSession,
    EventActivityOption, EventActivityVote
)


@login_required
@require_http_methods(["GET"])
def voting_status_api(request, event_id):
    """
    Get voting session status for an event
    Returns: voting state, time remaining, user vote status
    """
    event = get_object_or_404(MeetupEvent, id=event_id)

    # Verify user is registered
    try:
        user_registration = EventRegistration.objects.get(
            event=event,
            user=request.user
        )
        if user_registration.status not in ['confirmed', 'attended']:
            return JsonResponse({
                'success': False,
                'error': 'Only confirmed attendees can access voting'
            }, status=403)
    except EventRegistration.DoesNotExist:
        return JsonResponse({
            'success': False,
            'error': 'You are not registered for this event'
        }, status=403)

    # Get voting session
    try:
        voting_session = EventVotingSession.objects.get(event=event)
    except EventVotingSession.DoesNotExist:
        return JsonResponse({
            'success': False,
            'error': 'No voting session found for this event'
        }, status=404)

    # Check if user has voted
    user_vote = EventActivityVote.objects.filter(
        event=event,
        user=request.user
    ).first()

    # Determine voting phase
    now = timezone.now()
    if now < voting_session.voting_start_time:
        phase = 'waiting'
        time_value = voting_session.time_until_start
    elif now > voting_session.voting_end_time:
        phase = 'ended'
        time_value = 0
    else:
        phase = 'active'
        time_value = voting_session.time_remaining

    return JsonResponse({
        'success': True,
        'data': {
            'is_active': voting_session.is_active,
            'is_voting_open': voting_session.is_voting_open,
            'phase': phase,
            'voting_start_time': voting_session.voting_start_time.isoformat(),
            'voting_end_time': voting_session.voting_end_time.isoformat(),
            'time_until_start': voting_session.time_until_start,
            'time_remaining': time_value,
            'total_votes': voting_session.total_votes,
            'has_voted': user_vote is not None,
            'user_vote_option_id': user_vote.selected_option.id if user_vote else None,
        }
    })


@login_required
@require_http_methods(["POST"])
def submit_vote_api(request, event_id):
    """
    Submit or update a vote for an event activity
    Expects JSON body: {"option_id": 123}
    """
    import json

    event = get_object_or_404(MeetupEvent, id=event_id)

    # Verify user is registered
    try:
        user_registration = EventRegistration.objects.get(
            event=event,
            user=request.user
        )
        if user_registration.status not in ['confirmed', 'attended']:
            return JsonResponse({
                'success': False,
                'error': 'Only confirmed attendees can vote'
            }, status=403)
    except EventRegistration.DoesNotExist:
        return JsonResponse({
            'success': False,
            'error': 'You are not registered for this event'
        }, status=403)

    # Get voting session
    try:
        voting_session = EventVotingSession.objects.get(event=event)
    except EventVotingSession.DoesNotExist:
        return JsonResponse({
            'success': False,
            'error': 'No voting session found'
        }, status=404)

    # Check if voting is open
    if not voting_session.is_voting_open:
        return JsonResponse({
            'success': False,
            'error': 'Voting is not currently open'
        }, status=400)

    # Parse request body
    try:
        data = json.loads(request.body)
        option_id = data.get('option_id')
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'Invalid JSON'
        }, status=400)

    if not option_id:
        return JsonResponse({
            'success': False,
            'error': 'option_id is required'
        }, status=400)

    # Get the selected option
    try:
        selected_option = EventActivityOption.objects.get(id=option_id, event=event)
    except EventActivityOption.DoesNotExist:
        return JsonResponse({
            'success': False,
            'error': 'Invalid activity option'
        }, status=400)

    # Check for existing vote
    existing_vote = EventActivityVote.objects.filter(
        event=event,
        user=request.user
    ).first()

    if existing_vote:
        # Update existing vote
        old_option = existing_vote.selected_option
        old_option.vote_count -= 1
        old_option.save()

        existing_vote.selected_option = selected_option
        existing_vote.save()

        selected_option.vote_count += 1
        selected_option.save()

        action = 'updated'
    else:
        # Create new vote
        EventActivityVote.objects.create(
            event=event,
            user=request.user,
            selected_option=selected_option
        )

        selected_option.vote_count += 1
        selected_option.save()

        voting_session.total_votes += 1
        voting_session.save()

        action = 'created'

    return JsonResponse({
        'success': True,
        'message': f'Vote {action} successfully',
        'data': {
            'action': action,
            'selected_option_id': selected_option.id,
            'selected_option_name': selected_option.display_name,
            'vote_count': selected_option.vote_count,
        }
    })


@login_required
@require_http_methods(["GET"])
def voting_results_api(request, event_id):
    """
    Get current voting results
    Returns vote counts for all options
    """
    event = get_object_or_404(MeetupEvent, id=event_id)

    # Verify user is registered
    try:
        user_registration = EventRegistration.objects.get(
            event=event,
            user=request.user
        )
        if user_registration.status not in ['confirmed', 'attended']:
            return JsonResponse({
                'success': False,
                'error': 'Only confirmed attendees can view results'
            }, status=403)
    except EventRegistration.DoesNotExist:
        return JsonResponse({
            'success': False,
            'error': 'You are not registered for this event'
        }, status=403)

    # Get voting session
    try:
        voting_session = EventVotingSession.objects.get(event=event)
    except EventVotingSession.DoesNotExist:
        return JsonResponse({
            'success': False,
            'error': 'No voting session found'
        }, status=404)

    # Get all activity options with vote counts
    activity_options = EventActivityOption.objects.filter(event=event).order_by('-vote_count')

    # Format results
    results = []
    for option in activity_options:
        percentage = 0
        if voting_session.total_votes > 0:
            percentage = (option.vote_count / voting_session.total_votes) * 100

        results.append({
            'id': option.id,
            'display_name': option.display_name,
            'activity_type': option.activity_type,
            'activity_variant': option.activity_variant,
            'vote_count': option.vote_count,
            'percentage': round(percentage, 1),
            'is_winner': option.is_winner,
        })

    return JsonResponse({
        'success': True,
        'data': {
            'total_votes': voting_session.total_votes,
            'is_voting_open': voting_session.is_voting_open,
            'winner_id': voting_session.winning_option.id if voting_session.winning_option else None,
            'options': results,
        }
    })
