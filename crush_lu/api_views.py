from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_http_methods
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import F, Count, Q
from django.utils import timezone

from .models import (
    MeetupEvent, EventRegistration, EventVotingSession,
    GlobalActivityOption, EventActivityVote
)
from .decorators import ratelimit


@login_required
@require_http_methods(["GET"])
def voting_status_api(request, event_id):
    """
    Get voting session status for an event
    Returns: voting state, time remaining, user vote status
    """
    event = get_object_or_404(MeetupEvent, id=event_id)

    # Allow event coaches and superusers
    is_coach = event.coaches.filter(user=request.user).exists()
    if not request.user.is_superuser and not is_coach:
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

    # Check if user has voted (a user may vote once per activity_type/category)
    user_votes_qs = EventActivityVote.objects.filter(
        event=event,
        user=request.user,
    ).select_related('selected_option')

    # Map activity_type -> selected_option_id for clients that need per-category state.
    # EventActivityVote.Meta.ordering = ["-voted_at"], so iteration yields newest first.
    # setdefault keeps the FIRST value seen per key, i.e. the newest vote, so legacy
    # duplicate rows from earlier races don't cause stale state to shadow the latest.
    user_votes = {}
    for v in user_votes_qs:
        user_votes.setdefault(v.selected_option.activity_type, v.selected_option.id)

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

    # Preserve backward-compatible single-option field (first vote encountered)
    first_vote = user_votes_qs.first()

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
            'has_voted': bool(user_votes),
            'has_voted_presentation_style': 'presentation_style' in user_votes,
            'has_voted_speed_dating_twist': 'speed_dating_twist' in user_votes,
            'user_votes': user_votes,
            'user_vote_option_id': first_vote.selected_option.id if first_vote else None,
        }
    })


@login_required
@ratelimit(key='user', rate='30/m', method='POST')
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
        if user_registration.status != 'attended':
            return JsonResponse({
                'success': False,
                'error': 'You must check in at the event before you can vote'
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

    # Get the selected option (GlobalActivityOption - shared across all events)
    try:
        selected_option = GlobalActivityOption.objects.get(id=option_id, is_active=True)
    except GlobalActivityOption.DoesNotExist:
        return JsonResponse({
            'success': False,
            'error': 'Invalid activity option'
        }, status=400)

    # Atomic vote creation/update to prevent race conditions
    with transaction.atomic():
        # Lock the registration row (unique per event+user) to serialize
        # concurrent submissions from the same user. Locking EventActivityVote
        # itself is insufficient: on the first vote no row exists, so nothing
        # is locked and two concurrent creates can both succeed with different
        # selected_options (unique_together includes selected_option).
        EventRegistration.objects.select_for_update().get(pk=user_registration.pk)

        existing_vote = EventActivityVote.objects.filter(
            event=event,
            user=request.user,
            selected_option__activity_type=selected_option.activity_type,
        ).first()

        if existing_vote:
            existing_vote.selected_option = selected_option
            existing_vote.save()
            action = 'updated'
        else:
            # total_votes counts unique voters (the percentage denominator in
            # voting_results_api). Only bump it on the user's first vote across
            # ANY category for this event -- otherwise a single attendee voting
            # in both categories would double-count.
            is_first_vote_for_event = not EventActivityVote.objects.filter(
                event=event, user=request.user,
            ).exists()

            EventActivityVote.objects.create(
                event=event,
                user=request.user,
                selected_option=selected_option
            )
            if is_first_vote_for_event:
                EventVotingSession.objects.filter(
                    event=event
                ).update(total_votes=F('total_votes') + 1)
                voting_session.refresh_from_db()
            action = 'created'

    vote_count = EventActivityVote.objects.filter(
        event=event, selected_option=selected_option
    ).count()

    return JsonResponse({
        'success': True,
        'message': f'Vote {action} successfully',
        'data': {
            'action': action,
            'selected_option_id': selected_option.id,
            'selected_option_name': selected_option.display_name,
            'vote_count': vote_count,
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

    # Allow event coaches and superusers
    is_coach = event.coaches.filter(user=request.user).exists()
    if not request.user.is_superuser and not is_coach:
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

    # Get all options with vote counts in a single query (avoids N+1)
    options = GlobalActivityOption.objects.filter(
        is_active=True
    ).annotate(
        vote_count=Count(
            'eventactivityvote',
            filter=Q(eventactivityvote__event=event)
        )
    ).order_by('activity_type', 'sort_order')

    winner_ids = {
        voting_session.winning_presentation_style_id,
        voting_session.winning_speed_dating_twist_id,
    }

    results = []
    for option in options:
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
            'is_winner': option.id in winner_ids,
        })

    return JsonResponse({
        'success': True,
        'data': {
            'total_votes': voting_session.total_votes,
            'is_voting_open': voting_session.is_voting_open,
            'winning_presentation_style_id': voting_session.winning_presentation_style_id,
            'winning_speed_dating_twist_id': voting_session.winning_speed_dating_twist_id,
            'options': results,
        }
    })
