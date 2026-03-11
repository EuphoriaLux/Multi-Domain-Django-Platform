"""
Event Poll analytics view for Crush.lu Coach Panel.

Provides detailed results breakdown for each poll with vote counts,
percentages, and voter demographics.
"""

from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Count, Q
from django.shortcuts import render, get_object_or_404
from django.utils import timezone

from crush_lu.models.event_polls import EventPoll, EventPollOption, EventPollVote


@staff_member_required
def poll_analytics_dashboard(request):
    """Overview of all polls with summary stats."""
    polls = (
        EventPoll.objects.all()
        .annotate(
            total_votes=Count('votes'),
            unique_voters=Count('votes__user', distinct=True),
            option_count=Count('options', distinct=True),
        )
        .prefetch_related('options')
    )

    now = timezone.now()
    active_polls = [p for p in polls if p.is_active]
    closed_polls = [p for p in polls if p.is_closed]
    draft_polls = [p for p in polls if not p.is_published]

    total_votes = sum(p.total_votes for p in polls)
    total_voters = EventPollVote.objects.values('user').distinct().count()

    context = {
        'polls': polls,
        'active_polls': active_polls,
        'closed_polls': closed_polls,
        'draft_polls': draft_polls,
        'total_votes': total_votes,
        'total_voters': total_voters,
        'title': 'Poll Analytics',
        'site_header': 'Crush.lu Administration',
    }
    return render(request, 'admin/crush_lu/poll_analytics.html', context)


@staff_member_required
def poll_analytics_detail(request, poll_id):
    """Detailed analytics for a single poll."""
    poll = get_object_or_404(EventPoll, pk=poll_id)

    options = (
        poll.options
        .annotate(vote_count=Count('votes'))
        .order_by('-vote_count')
    )
    total_votes = sum(o.vote_count for o in options)

    # Results with percentages
    results = []
    for option in options:
        pct = round(option.vote_count / total_votes * 100, 1) if total_votes else 0
        results.append({
            'option': option,
            'vote_count': option.vote_count,
            'percentage': pct,
        })

    # Unique voters
    unique_voters = (
        EventPollVote.objects.filter(poll=poll)
        .values('user').distinct().count()
    )

    # Recent votes (last 20)
    recent_votes = (
        EventPollVote.objects.filter(poll=poll)
        .select_related('user', 'option')
        .order_by('-voted_at')[:20]
    )

    # Votes over time (daily)
    from django.db.models.functions import TruncDate
    votes_by_day = (
        EventPollVote.objects.filter(poll=poll)
        .annotate(day=TruncDate('voted_at'))
        .values('day')
        .annotate(count=Count('id'))
        .order_by('day')
    )

    context = {
        'poll': poll,
        'results': results,
        'total_votes': total_votes,
        'unique_voters': unique_voters,
        'recent_votes': recent_votes,
        'votes_by_day': list(votes_by_day),
        'title': f'Poll Analytics: {poll.title}',
        'site_header': 'Crush.lu Administration',
    }
    return render(request, 'admin/crush_lu/poll_analytics_detail.html', context)
