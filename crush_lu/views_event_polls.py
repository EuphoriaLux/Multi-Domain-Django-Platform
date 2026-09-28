"""
Event Poll views for Crush.lu.

Approved members vote on preferences for future events. A poll marked
``is_public`` (the theme-night ballot at /<lang>/themes/) can be viewed by
anyone and voted on by any logged-in account, profile or not.
"""

import json
import logging

from django.contrib.auth.views import redirect_to_login
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.conf import settings
from django.utils import translation
from django.utils.translation import get_language, gettext_lazy as _
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST
from django.contrib import messages
from datetime import timedelta

from .decorators import crush_login_required, ratelimit
from .forms_event_polls import EventPollSuggestionForm
from .models import CrushProfile
from .models.event_polls import EventPoll, EventPollSuggestion, EventPollVote

logger = logging.getLogger(__name__)


# A theme's women/men split is public only once at least this many women AND
# this many men voted for it, so no single vote can be read off the page.
GENDER_SPLIT_MIN_VOTES = 5

# CrushProfile.gender -> EventPollVote.voter_gender
_PROFILE_TO_VOTER_GENDER = {'F': 'F', 'M': 'M', 'NB': 'O', 'O': 'O', 'P': 'P'}
_VOTER_GENDERS = {code for code, _label in EventPollVote.VOTER_GENDER_CHOICES}


def _profile_voter_gender(user):
    gender = (
        CrushProfile.objects.filter(user=user).values_list('gender', flat=True).first()
    )
    return _PROFILE_TO_VOTER_GENDER.get(gender or '', '')


def _annotate_gender_split(poll, options):
    """Set women_pct / men_pct on options that clear GENDER_SPLIT_MIN_VOTES.

    The percentage is the share of all women (men) voting in this poll who
    picked the option, so the uneven women/men turnout doesn't skew it.
    """
    totals = dict(
        EventPollVote.objects.filter(poll=poll, voter_gender__in=['F', 'M'])
        .values('voter_gender')
        .annotate(n=Count('user', distinct=True))
        .values_list('voter_gender', 'n')
    )
    women_total, men_total = totals.get('F', 0), totals.get('M', 0)
    any_split = False
    for option in options:
        option.show_gender_split = (
            option.women_votes >= GENDER_SPLIT_MIN_VOTES
            and option.men_votes >= GENDER_SPLIT_MIN_VOTES
        )
        if option.show_gender_split:
            option.women_pct = round(option.women_votes / women_total * 100)
            option.men_pct = round(option.men_votes / men_total * 100)
            any_split = True
    return any_split


def _is_approved_member(user):
    return (
        user.is_authenticated
        and CrushProfile.objects.filter(user=user, is_approved=True).exists()
    )


def _member_gate(request):
    """Return a redirect for anyone who may not use members-only polls."""
    if not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path(), reverse('crush_lu:login'))
    try:
        profile = CrushProfile.objects.get(user=request.user)
    except CrushProfile.DoesNotExist:
        messages.warning(request, _("You need a profile to view polls."))
        return redirect('crush_lu:create_profile')
    if not profile.is_approved:
        messages.info(request, _("Your profile must be approved to view polls."))
        return redirect('crush_lu:dashboard')
    return None


@never_cache
@require_GET
def poll_list(request):
    """List active and recently closed polls.

    Visitors who are not approved members see only public polls; when there
    are none, they get the members-only redirect as before.
    """
    polls = EventPoll.objects.filter(is_published=True)
    if not _is_approved_member(request.user):
        polls = polls.filter(is_public=True)
        if not polls.exists():
            return _member_gate(request)

    now = timezone.now()
    thirty_days_ago = now - timedelta(days=30)

    active_polls = polls.filter(
        start_date__lte=now,
        end_date__gte=now,
    ).annotate(total_votes=Count('votes'))

    closed_polls = polls.filter(
        end_date__lt=now,
        end_date__gte=thirty_days_ago,
    ).annotate(total_votes=Count('votes'))

    user_voted_poll_ids = set()
    if request.user.is_authenticated:
        user_voted_poll_ids = set(
            EventPollVote.objects.filter(user=request.user).values_list(
                'poll_id', flat=True
            )
        )

    return render(request, 'crush_lu/event_polls/poll_list.html', {
        'active_polls': active_polls,
        'closed_polls': closed_polls,
        'user_voted_poll_ids': user_voted_poll_ids,
    })


def _results_context(request, poll):
    """What _poll_results_partial.html needs: options with counts, the viewer's votes."""
    options = list(
        poll.options.annotate(
            vote_count=Count('votes'),
            women_votes=Count('votes', filter=Q(votes__voter_gender='F')),
            men_votes=Count('votes', filter=Q(votes__voter_gender='M')),
        )
    )
    total_votes = sum(o.vote_count for o in options)
    # Members-only polls never publish a gender breakdown.
    any_gender_split = poll.is_public and _annotate_gender_split(poll, options)
    authenticated = request.user.is_authenticated

    user_votes = set()
    if authenticated:
        user_votes = set(
            EventPollVote.objects.filter(
                poll=poll, user=request.user
            ).values_list('option_id', flat=True)
        )
    return {
        'poll': poll,
        'options': options,
        'total_votes': total_votes,
        'user_votes': user_votes,
        'has_voted': bool(user_votes),
        'any_gender_split': any_gender_split,
        'gender_split_min': GENDER_SPLIT_MIN_VOTES,
    }


def _render_poll(request, poll):
    context = _results_context(request, poll)
    has_voted = context['has_voted']
    authenticated = request.user.is_authenticated

    # Show voting form if poll is active and user hasn't voted
    can_vote = authenticated and poll.is_active and not has_voted
    # Show results if user voted, poll closed, or show_results_before_close is on
    show_results = has_voted or poll.is_closed or (poll.show_results_before_close and not can_vote)

    return render(request, 'crush_lu/event_polls/poll_detail.html', {
        **context,
        'can_vote': can_vote,
        'show_results': show_results,
        # The non-JS vote form posts back to the page that rendered it.
        'return_to_themes': request.resolver_match.url_name == 'theme_board',
        # Anonymous visitors of a public poll: show the ballot, ask to log in.
        'login_to_vote': not authenticated and poll.is_active,
        'login_url': redirect_to_login(
            request.get_full_path(), reverse('crush_lu:login')
        ).url,
        'can_suggest': authenticated and poll.is_public and poll.is_active,
        # Voters without a profile gender get an optional "I am..." choice.
        'ask_gender': can_vote and not _profile_voter_gender(request.user),
        'voter_gender_choices': EventPollVote.VOTER_GENDER_CHOICES,
        'suggestion_form': EventPollSuggestionForm(),
    })


@never_cache
@require_GET
def poll_detail(request, poll_id):
    """View poll details, vote, or see results."""
    poll = get_object_or_404(EventPoll, pk=poll_id, is_published=True)
    if not poll.is_public:
        denied = _member_gate(request)
        if denied:
            return denied
    return _render_poll(request, poll)


@never_cache
@require_GET
def theme_board(request):
    """Short public URL for the newest active public poll (the theme ballot)."""
    now = timezone.now()
    poll = (
        EventPoll.objects.filter(
            is_published=True,
            is_public=True,
            start_date__lte=now,
            end_date__gte=now,
        )
        .order_by('-start_date', '-pk')
        .first()
    )
    if poll is None:
        return render(request, 'crush_lu/event_polls/theme_board_empty.html')
    return _render_poll(request, poll)


@require_POST
@crush_login_required
@ratelimit(key='user', rate='5/h')
def poll_suggest(request, poll_id):
    """Store a voter's idea for a new option; a coach reviews it in the admin."""
    poll = get_object_or_404(
        EventPoll, pk=poll_id, is_published=True, is_public=True
    )
    # Return to whichever of our two pages rendered the form; the URL is
    # always built here, never taken from the request.
    if request.POST.get('return_to') == 'themes':
        next_url = reverse('crush_lu:theme_board')
    else:
        next_url = reverse('crush_lu:poll_detail', args=[poll.pk])

    if not poll.is_active:
        messages.error(request, _("This poll is closed."))
        return redirect(next_url)

    form = EventPollSuggestionForm(request.POST)
    if form.is_valid():
        EventPollSuggestion.objects.create(
            poll=poll,
            user=request.user,
            text=form.cleaned_data['text'],
            language=(get_language() or '')[:10],
        )
        messages.success(
            request, _("Thanks for your idea! A coach will review it soon.")
        )
    elif 'website' not in form.errors:
        # A filled honeypot is dropped silently; real input errors get a message.
        messages.error(
            request, _("Please describe your idea in 200 characters or fewer.")
        )
    return redirect(next_url)


def _back_to_poll(request, poll):
    """Redirect a no-JS vote to the page that rendered the ballot."""
    if request.POST.get('return_to') == 'themes':
        return redirect('crush_lu:theme_board')
    return redirect('crush_lu:poll_detail', poll.pk)


def _ballot_language(data):
    """The language of the page the ballot sits on, posted as ``lang``.

    The vote URL is language-neutral, so without it LocaleMiddleware would
    answer in the browser's Accept-Language instead of the page's language.
    """
    lang = data.get('lang') if isinstance(data, dict) else None
    # A non-string lang (a list, an object) must not reach the set lookup.
    if isinstance(lang, str) and lang in {code for code, _n in settings.LANGUAGES}:
        return lang
    return get_language()


@require_POST
@crush_login_required
def poll_vote(request, poll_id):
    """Submit a vote on a poll.

    The ballot's fetch posts JSON and gets JSON back, with the rendered results
    partial to swap in. A plain form post (no JS) is redirected back to the
    poll page with a flash message. Either way the reply is in the ballot
    page's language.
    """
    is_json = request.content_type == 'application/json'
    if is_json:
        try:
            data = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            data = None
    else:
        data = request.POST
    # The language applies before the rate limit, so its 429 page (or JSON
    # error) is in the ballot's language too.
    with translation.override(_ballot_language(data)):
        return _rate_limited_poll_vote(request, poll_id, is_json, data)


@ratelimit(key='user', rate='10/m', rate_limited_template='crush_lu/rate_limited.html')
def _rate_limited_poll_vote(request, poll_id, is_json, data):
    poll = get_object_or_404(EventPoll, pk=poll_id, is_published=True)
    return _poll_vote(request, poll, is_json, data)


def _poll_vote(request, poll, is_json, data):
    def reply(error, status=400):
        if is_json:
            return JsonResponse({'error': error}, status=status)
        # Messages render after this language override ends: translate now.
        messages.error(request, str(error))
        return _back_to_poll(request, poll)

    if not poll.is_public:
        try:
            profile = CrushProfile.objects.get(user=request.user)
        except CrushProfile.DoesNotExist:
            return reply(_('Profile required'), 403)

        if not profile.is_approved:
            return reply(_('Profile not approved'), 403)

    if not poll.is_active:
        return reply(_('Poll is not active'))

    if is_json:
        if not isinstance(data, dict):
            return reply(_('Invalid JSON'))
    else:
        data = {
            'option_ids': request.POST.getlist('option_ids'),
            'gender': request.POST.get('voter_gender'),
        }
        try:
            data['option_ids'] = [int(oid) for oid in data['option_ids']]
        except ValueError:
            return reply(_('Invalid option'))

    option_ids = data.get('option_ids', [])
    if not isinstance(option_ids, list) or not all(
        type(oid) is int for oid in option_ids
    ):
        return reply(_('Invalid option'))
    if not option_ids:
        return reply(_('No options selected'))

    if not poll.allow_multiple_choices and len(option_ids) > 1:
        return reply(_('Only one choice allowed'))

    # Validate all option IDs belong to this poll
    valid_options = set(poll.options.values_list('id', flat=True))
    for oid in option_ids:
        if oid not in valid_options:
            return reply(_('Invalid option'))

    # Single-choice: delete existing votes first
    if not poll.allow_multiple_choices:
        EventPollVote.objects.filter(poll=poll, user=request.user).delete()

    # One gender per voter per poll, or a voter could land in both the women
    # and the men denominators: an earlier vote's gender wins, then the
    # profile's, then the ballot answer.
    earlier_votes = EventPollVote.objects.filter(poll=poll, user=request.user)
    voter_gender = earlier_votes.values_list('voter_gender', flat=True).first()
    if voter_gender is None:
        voter_gender = _profile_voter_gender(request.user)
        gender = data.get('gender')
        if not voter_gender and isinstance(gender, str) and gender in _VOTER_GENDERS:
            voter_gender = gender

    # Create votes (skip duplicates via unique_together)
    created = 0
    for oid in option_ids:
        _vote, was_created = EventPollVote.objects.get_or_create(
            poll=poll,
            option_id=oid,
            user=request.user,
            defaults={'voter_gender': voter_gender},
        )
        if was_created:
            created += 1
    # Two first submissions racing (two tabs) could still disagree; settle
    # every row of this voter on one value.
    earlier_votes.exclude(voter_gender=voter_gender).update(
        voter_gender=voter_gender
    )

    if not is_json:
        messages.success(request, str(_("Your vote has been recorded. Thank you!")))
        return _back_to_poll(request, poll)

    # Return updated results. One evaluated snapshot feeds the counts, the
    # header total and the rendered partial, so a concurrent vote cannot
    # make them disagree within one response.
    context = _results_context(request, poll)
    total_votes = context['total_votes']
    results = [
        {
            'id': o.id,
            'name': str(o.name),
            'vote_count': o.vote_count,
            'percentage': round(o.vote_count / total_votes * 100) if total_votes else 0,
        }
        for o in context['options']
    ]

    return JsonResponse({
        'success': True,
        'created': created,
        'total_votes': total_votes,
        'results': results,
        'results_html': render_to_string(
            'crush_lu/event_polls/_poll_results_partial.html',
            context,
            request=request,
        ),
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
