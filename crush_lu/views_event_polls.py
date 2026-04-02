"""
Event Poll views for Crush.lu.

Allows approved users to vote on preferences for future events.
"""

import json
import logging

from django.db.models import Count
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST
from django.contrib import messages
from datetime import timedelta

from .decorators import crush_login_required, ratelimit
from .models import CrushProfile
from .models.event_polls import EventPoll, EventPollVote

logger = logging.getLogger(__name__)


@crush_login_required
def poll_list(request):
    """List active and recently closed polls."""
    try:
        profile = CrushProfile.objects.get(user=request.user)
    except CrushProfile.DoesNotExist:
        messages.warning(request, _("You need a profile to view polls."))
        return redirect('crush_lu:create_profile')

    if not profile.is_approved:
        messages.info(request, _("Your profile must be approved to view polls."))
        return redirect('crush_lu:dashboard')

    now = timezone.now()
    thirty_days_ago = now - timedelta(days=30)

    active_polls = (
        EventPoll.objects.filter(
            is_published=True,
            start_date__lte=now,
            end_date__gte=now,
        )
        .annotate(total_votes=Count('votes'))
    )

    closed_polls = (
        EventPoll.objects.filter(
            is_published=True,
            end_date__lt=now,
            end_date__gte=thirty_days_ago,
        )
        .annotate(total_votes=Count('votes'))
    )

    # Get user's voted poll IDs
    user_voted_poll_ids = set(
        EventPollVote.objects.filter(user=request.user).values_list('poll_id', flat=True)
    )

    return render(request, 'crush_lu/event_polls/poll_list.html', {
        'active_polls': active_polls,
        'closed_polls': closed_polls,
        'user_voted_poll_ids': user_voted_poll_ids,
    })


@crush_login_required
def poll_detail(request, poll_id):
    """View poll details, vote, or see results."""
    try:
        profile = CrushProfile.objects.get(user=request.user)
    except CrushProfile.DoesNotExist:
        messages.warning(request, _("You need a profile to view polls."))
        return redirect('crush_lu:create_profile')

    if not profile.is_approved:
        messages.info(request, _("Your profile must be approved to view polls."))
        return redirect('crush_lu:dashboard')

    poll = get_object_or_404(EventPoll, pk=poll_id, is_published=True)

    options = poll.options.annotate(vote_count=Count('votes'))
    total_votes = sum(o.vote_count for o in options)

    user_votes = set(
        EventPollVote.objects.filter(
            poll=poll, user=request.user
        ).values_list('option_id', flat=True)
    )
    has_voted = len(user_votes) > 0

    # Show voting form if poll is active and user hasn't voted
    can_vote = poll.is_active and not has_voted
    # Show results if user voted, poll closed, or show_results_before_close is on
    show_results = has_voted or poll.is_closed or (poll.show_results_before_close and not can_vote)

    return render(request, 'crush_lu/event_polls/poll_detail.html', {
        'poll': poll,
        'options': options,
        'can_vote': can_vote,
        'total_votes': total_votes,
        'user_votes': user_votes,
        'has_voted': has_voted,
        'show_results': show_results,
    })


@require_POST
@crush_login_required
@ratelimit(key='user', rate='10/m')
def poll_vote(request, poll_id):
    """Submit a vote on a poll. Returns JSON."""
    try:
        profile = CrushProfile.objects.get(user=request.user)
    except CrushProfile.DoesNotExist:
        return JsonResponse({'error': 'Profile required'}, status=403)

    if not profile.is_approved:
        return JsonResponse({'error': 'Profile not approved'}, status=403)

    poll = get_object_or_404(EventPoll, pk=poll_id, is_published=True)

    if not poll.is_active:
        return JsonResponse({'error': 'Poll is not active'}, status=400)

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    option_ids = data.get('option_ids', [])
    if not option_ids:
        return JsonResponse({'error': 'No options selected'}, status=400)

    if not poll.allow_multiple_choices and len(option_ids) > 1:
        return JsonResponse({'error': 'Only one choice allowed'}, status=400)

    # Validate all option IDs belong to this poll
    valid_options = set(poll.options.values_list('id', flat=True))
    for oid in option_ids:
        if oid not in valid_options:
            return JsonResponse({'error': 'Invalid option'}, status=400)

    # Single-choice: delete existing votes first
    if not poll.allow_multiple_choices:
        EventPollVote.objects.filter(poll=poll, user=request.user).delete()

    # Create votes (skip duplicates via unique_together)
    created = 0
    for oid in option_ids:
        _, was_created = EventPollVote.objects.get_or_create(
            poll=poll,
            option_id=oid,
            user=request.user,
        )
        if was_created:
            created += 1

    # Return updated results
    options = poll.options.annotate(vote_count=Count('votes'))
    total_votes = sum(o.vote_count for o in options)
    results = [
        {
            'id': o.id,
            'name': str(o.name),
            'vote_count': o.vote_count,
            'percentage': round(o.vote_count / total_votes * 100) if total_votes else 0,
        }
        for o in options
    ]

    return JsonResponse({
        'success': True,
        'created': created,
        'total_votes': total_votes,
        'results': results,
    })


@crush_login_required
def poll_results_api(request, poll_id):
    """Return poll results as JSON."""
    poll = get_object_or_404(EventPoll, pk=poll_id, is_published=True)

    options = poll.options.annotate(vote_count=Count('votes'))
    total_votes = sum(o.vote_count for o in options)

    results = [
        {
            'id': o.id,
            'name': str(o.name),
            'vote_count': o.vote_count,
            'percentage': round(o.vote_count / total_votes * 100) if total_votes else 0,
        }
        for o in options
    ]

    return JsonResponse({
        'total_votes': total_votes,
        'results': results,
    })
