"""
Interactive Journey System Views - "The Wonderland of You"

This module handles all views related to the personalized journey experience.
"""

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.utils import timezone
from django.utils.translation import get_language, gettext as _
from django.db.models import Q
from django.http import JsonResponse
from .decorators import crush_login_required
from .models import (
    JourneyConfiguration, JourneyChapter, JourneyChallenge,
    JourneyReward, JourneyProgress, ChapterProgress, ChallengeAttempt
)
import logging

logger = logging.getLogger(__name__)


@crush_login_required
def journey_selector(request):
    """
    Journey selection view - shows all available journeys when user has multiple.
    If user has only one journey, redirects directly to that journey.
    """
    logger.info(f"🎮 journey_selector called for user: {request.user.username}")

    from .models import SpecialUserExperience

    # Find special experience for this user (direct link OR name match)
    special_experience = SpecialUserExperience.objects.filter(
        Q(is_active=True) &
        (
            Q(linked_user=request.user) |  # Direct link (from gifts)
            (Q(first_name__iexact=request.user.first_name) & Q(last_name__iexact=request.user.last_name))  # Name match (legacy)
        )
    ).first()

    if not special_experience:
        messages.warning(request, _('No special journey found for your account.'))
        return redirect('crush_lu:home')

    # Get all active journeys for this user
    journeys = JourneyConfiguration.objects.filter(
        special_experience=special_experience,
        is_active=True
    ).order_by('journey_type')

    if not journeys.exists():
        messages.info(request, _('Welcome! Your journey is being prepared.'))
        return redirect('crush_lu:special_welcome')

    # If only one journey, redirect directly to it
    if journeys.count() == 1:
        journey = journeys.first()
        if journey.journey_type == 'wonderland':
            return redirect('crush_lu:journey_map_wonderland')
        elif journey.journey_type == 'advent_calendar':
            return redirect('crush_lu:advent_calendar')
        else:
            # Custom journey - use wonderland map for now
            return redirect('crush_lu:journey_map_wonderland')

    # Multiple journeys - show selection page
    journey_data = []
    for journey in journeys:
        # Get progress if exists
        try:
            progress = JourneyProgress.objects.get(user=request.user, journey=journey)
            completion_pct = progress.completion_percentage
            is_completed = progress.is_completed
        except JourneyProgress.DoesNotExist:
            completion_pct = 0
            is_completed = False

        # Determine the URL and icon for each journey type
        if journey.journey_type == 'wonderland':
            url = 'crush_lu:journey_map_wonderland'
            icon = '🗺️'
            description = _('A magical 6-chapter adventure through the Wonderland of You')
        elif journey.journey_type == 'advent_calendar':
            url = 'crush_lu:advent_calendar'
            icon = '🎄'
            description = _('24 doors of surprises waiting to be discovered')
        else:
            url = 'crush_lu:journey_map_wonderland'
            icon = '✨'
            description = journey.journey_name

        journey_data.append({
            'journey': journey,
            'url': url,
            'icon': icon,
            'description': description,
            'completion_percentage': completion_pct,
            'is_completed': is_completed,
        })

    context = {
        'journeys': journey_data,
        'special_experience': special_experience,
    }

    return render(request, 'crush_lu/journey/journey_selector.html', context)


@crush_login_required
def journey_map(request):
    """
    Main journey entry point - redirects to journey selector.
    This maintains backwards compatibility with existing links.
    """
    return redirect('crush_lu:journey_selector')


@crush_login_required
def journey_map_wonderland(request):
    """
    Wonderland journey map view - shows visual progress through all chapters.
    """
    logger.info(f"🎮 journey_map_wonderland called for user: {request.user.username} (first: '{request.user.first_name}', last: '{request.user.last_name}')")

    # Check if user has an active journey
    try:
        # Get the user's special experience journey
        from .models import SpecialUserExperience

        # Try to find special experience for this user (direct link OR name match)
        special_experience = SpecialUserExperience.objects.filter(
            Q(is_active=True) &
            (
                Q(linked_user=request.user) |  # Direct link (from gifts)
                (Q(first_name__iexact=request.user.first_name) & Q(last_name__iexact=request.user.last_name))  # Name match (legacy)
            )
        ).first()

        logger.info(f"Special experience found: {special_experience is not None}")

        if not special_experience:
            messages.warning(request, _('No special journey found for your account.'))
            return redirect('crush_lu:home')

        # Get the Wonderland journey specifically
        try:
            journey = JourneyConfiguration.objects.get(
                special_experience=special_experience,
                journey_type='wonderland',
                is_active=True
            )
            logger.info(f"Wonderland journey found: {journey.journey_name}, active: {journey.is_active}")
        except JourneyConfiguration.DoesNotExist:
            # No Wonderland journey - redirect to selector to show what's available
            logger.info(f"No Wonderland journey found - redirecting to selector")
            return redirect('crush_lu:journey_selector')

        # Get or create journey progress
        journey_progress, created = JourneyProgress.objects.get_or_create(
            user=request.user,
            journey=journey
        )

        if created:
            logger.info(f"🎮 Started new journey for {request.user.username}: {journey.journey_name}")

        # Get all chapters with completion status
        chapters = journey.chapters.all().order_by('chapter_number')

        # Build chapter data with progress
        chapter_data = []
        for chapter in chapters:
            # Get chapter progress if exists
            try:
                chapter_progress = ChapterProgress.objects.get(
                    journey_progress=journey_progress,
                    chapter=chapter
                )
                is_completed = chapter_progress.is_completed
                points_earned = chapter_progress.points_earned
            except ChapterProgress.DoesNotExist:
                is_completed = False
                points_earned = 0

            # Determine if chapter is unlocked
            if chapter.chapter_number == 1:
                is_unlocked = True
            elif chapter.requires_previous_completion:
                # Check if previous chapter is completed
                try:
                    previous_chapter = chapters.get(chapter_number=chapter.chapter_number - 1)
                    previous_progress = ChapterProgress.objects.get(
                        journey_progress=journey_progress,
                        chapter=previous_chapter
                    )
                    is_unlocked = previous_progress.is_completed
                except (JourneyChapter.DoesNotExist, ChapterProgress.DoesNotExist):
                    is_unlocked = False
            else:
                is_unlocked = True

            chapter_data.append({
                'chapter': chapter,
                'is_completed': is_completed,
                'is_unlocked': is_unlocked,
                'is_current': chapter.chapter_number == journey_progress.current_chapter,
                'points_earned': points_earned,
            })

        context = {
            'journey': journey,
            'journey_progress': journey_progress,
            'chapters': chapter_data,
            'completion_percentage': journey_progress.completion_percentage,
        }

        return render(request, 'crush_lu/journey/journey_map.html', context)

    except Exception as e:
        logger.error(f"❌ Error loading journey map: {e}", exc_info=True)
        messages.error(request, _('An error occurred loading your journey. Please contact support if this persists.'))
        # For special journey users, redirect to home, not dashboard
        # They may not have a regular Crush profile
        return redirect('crush_lu:home')


@crush_login_required
def chapter_view(request, chapter_number):
    """
    Display a specific chapter with its story and challenges.
    """
    try:
        # Get user's journey
        journey_progress = JourneyProgress.objects.filter(
            user=request.user
        ).select_related('journey').first()

        if not journey_progress:
            messages.warning(request, _('No active journey found.'))
            return redirect('crush_lu:dashboard')

        # Get the chapter
        chapter = get_object_or_404(
            JourneyChapter,
            journey=journey_progress.journey,
            chapter_number=chapter_number
        )

        # Check if chapter is unlocked
        if chapter.requires_previous_completion and chapter.chapter_number > 1:
            previous_chapter = JourneyChapter.objects.get(
                journey=journey_progress.journey,
                chapter_number=chapter_number - 1
            )
            try:
                previous_progress = ChapterProgress.objects.get(
                    journey_progress=journey_progress,
                    chapter=previous_chapter
                )
                if not previous_progress.is_completed:
                    messages.warning(request, _('Please complete Chapter %(chapter)s first.') % {'chapter': chapter_number - 1})
                    return redirect('crush_lu:journey_map')
            except ChapterProgress.DoesNotExist:
                messages.warning(request, _('Please complete Chapter %(chapter)s first.') % {'chapter': chapter_number - 1})
                return redirect('crush_lu:journey_map')

        # Get or create chapter progress
        chapter_progress, created = ChapterProgress.objects.get_or_create(
            journey_progress=journey_progress,
            chapter=chapter
        )

        # Get all challenges for this chapter
        challenges = chapter.challenges.all().order_by('challenge_order')

        # Get challenge attempts
        challenge_attempts = {}
        for attempt in chapter_progress.attempts.filter(is_correct=True):
            challenge_attempts[attempt.challenge_id] = attempt

        # Get rewards
        rewards = chapter.rewards.all()

        # Check if chapter is completed
        all_challenges_correct = all(
            challenge.id in challenge_attempts for challenge in challenges
        )

        if all_challenges_correct and challenges.exists() and not chapter_progress.is_completed:
            # Mark chapter as completed
            chapter_progress.is_completed = True
            chapter_progress.completed_at = timezone.now()
            chapter_progress.save()

            # Update journey progress
            journey_progress.current_chapter = max(
                journey_progress.current_chapter,
                chapter_number + 1
            )
            journey_progress.save()

            logger.info(f"✅ {request.user.username} completed Chapter {chapter_number}")

        context = {
            'chapter': chapter,
            'chapter_progress': chapter_progress,
            'journey_progress': journey_progress,
            'challenges': challenges,
            'challenge_attempts': challenge_attempts,
            'rewards': rewards,
            'is_completed': chapter_progress.is_completed,
        }

        return render(request, 'crush_lu/journey/chapter_view.html', context)

    except Exception as e:
        logger.error(f"❌ Error loading chapter {chapter_number}: {e}", exc_info=True)
        messages.error(request, _('An error occurred loading Chapter %(chapter)s. Please try again or contact support.') % {'chapter': chapter_number})
        # Always redirect back to journey map on chapter errors
        return redirect('crush_lu:journey_map')


def _get_timeline_events_with_fallback(challenge, requested_lang='en'):
    """
    Extract timeline events with multi-language fallback.

    Handles:
    1. Clean structure: {'events': [...]} in options_{lang}
    2. Legacy nested: {'events_de': [...], 'events_en': [...]} inside single options
    3. Empty options: fallback to other languages

    Args:
        challenge: JourneyChallenge instance
        requested_lang: Language code ('en', 'de', 'fr')

    Returns:
        List of event dictionaries for timeline display
    """
    from .utils.i18n import get_supported_language_codes
    supported_languages = get_supported_language_codes()
    fallback_order = [requested_lang] + [lang for lang in supported_languages if lang != requested_lang]

    for lang in fallback_order:
        # Access language-specific field directly (bypass modeltranslation)
        options = getattr(challenge, f'options_{lang}', None)

        if not options or not isinstance(options, dict):
            continue

        # Clean structure: {'events': [...]}
        if 'events' in options and options['events']:
            if lang != requested_lang:
                logger.debug(
                    f"Timeline challenge {challenge.id}: using {lang} fallback "
                    f"(requested: {requested_lang})"
                )
            return options['events']

        # Legacy nested structure: try requested lang key first
        events_key = f'events_{requested_lang}'
        if events_key in options and options[events_key]:
            return options[events_key]

        # Try any available nested key
        for fb_lang in fallback_order:
            nested_key = f'events_{fb_lang}'
            if nested_key in options and options[nested_key]:
                logger.debug(
                    f"Timeline challenge {challenge.id}: using nested {fb_lang} events"
                )
                return options[nested_key]

    logger.warning(f"Timeline challenge {challenge.id}: no events found in any language")
    return []


@crush_login_required
def challenge_view(request, chapter_number, challenge_id):
    """
    Display and handle a specific challenge.
    """
    try:
        # Get user's journey
        journey_progress = JourneyProgress.objects.filter(
            user=request.user
        ).select_related('journey').first()

        if not journey_progress:
            messages.warning(request, _('No active journey found.'))
            return redirect('crush_lu:dashboard')

        # Get the chapter
        chapter = get_object_or_404(
            JourneyChapter,
            journey=journey_progress.journey,
            chapter_number=chapter_number
        )

        # Get chapter progress
        chapter_progress = get_object_or_404(
            ChapterProgress,
            journey_progress=journey_progress,
            chapter=chapter
        )

        # Get the challenge
        challenge = get_object_or_404(
            JourneyChallenge,
            id=challenge_id,
            chapter=chapter
        )

        # Check if already completed
        existing_attempt = ChallengeAttempt.objects.filter(
            chapter_progress=chapter_progress,
            challenge=challenge,
            is_correct=True
        ).first()

        context = {
            'chapter': chapter,
            'chapter_progress': chapter_progress,
            'challenge': challenge,
            'existing_attempt': existing_attempt,
            'journey_progress': journey_progress,
        }

        # Get current language for localized content
        current_lang = get_language() or 'en'
        lang_code = current_lang[:2] if '-' in current_lang else current_lang

        # For timeline_sort challenges, get language-specific events with fallback
        if challenge.challenge_type == 'timeline_sort':
            context['localized_events'] = _get_timeline_events_with_fallback(
                challenge, lang_code
            )

        # For multiple_choice challenges, use django-modeltranslation
        # challenge.options automatically returns the translated version (options_de, options_fr)
        # based on the current language activation
        if challenge.challenge_type == 'multiple_choice' and challenge.options:
            context['localized_options'] = challenge.options

        # Render challenge template based on type
        template_name = f'crush_lu/journey/challenges/{challenge.challenge_type}.html'

        return render(request, template_name, context)

    except Exception as e:
        logger.error(f"❌ Error loading challenge {challenge_id} in chapter {chapter_number}: {e}", exc_info=True)
        messages.error(request, _('An error occurred loading this challenge. Please try again.'))
        # Redirect back to chapter view so user can try another challenge
        return redirect('crush_lu:chapter_view', chapter_number=chapter_number)


@crush_login_required
def reward_view(request, reward_id):
    """
    Display a reward after completing challenges.

    Security: Verifies the reward belongs to a journey the user has access to
    before displaying any content (prevents IDOR attacks).
    """
    try:
        # Get the user's journey progress first
        journey_progress = JourneyProgress.objects.filter(
            user=request.user
        ).first()

        if not journey_progress:
            messages.warning(request, _('No active journey found.'))
            return redirect('crush_lu:dashboard')

        # SECURITY: Fetch reward AND verify it belongs to user's journey in ONE query
        # This prevents IDOR attacks where users guess reward IDs from other journeys
        reward = get_object_or_404(
            JourneyReward,
            id=reward_id,
            chapter__journey=journey_progress.journey  # Must be from user's journey
        )

        # Check if chapter is completed
        try:
            chapter_progress = ChapterProgress.objects.get(
                journey_progress=journey_progress,
                chapter=reward.chapter
            )

            if not chapter_progress.is_completed:
                messages.warning(request, _('Complete all challenges to unlock this reward.'))
                return redirect('crush_lu:chapter_view', chapter_number=reward.chapter.chapter_number)
        except ChapterProgress.DoesNotExist:
            messages.warning(request, _('You must complete the chapter first.'))
            return redirect('crush_lu:chapter_view', chapter_number=reward.chapter.chapter_number)

        context = {
            'reward': reward,
            'chapter': reward.chapter,
            'journey_progress': journey_progress,
        }

        # For photo slideshows, add the list of all images as JSON for Alpine.js
        if reward.reward_type == 'photo_slideshow':
            import json
            slideshow_images = reward.all_slideshow_images
            if slideshow_images:
                context['slideshow_images'] = slideshow_images
                # Escape for safe embedding in <script> tags (prevent XSS via </script>)
                json_str = json.dumps(slideshow_images)
                # Replace sequences that could break out of script tag
                json_str = json_str.replace('</', '<\\/')
                context['slideshow_images_json'] = json_str

        # Render reward template based on type
        template_name = f'crush_lu/journey/rewards/{reward.reward_type}.html'

        return render(request, template_name, context)

    except Exception as e:
        logger.error(f"❌ Error loading reward {reward_id}: {e}", exc_info=True)
        messages.error(request, _('An error occurred loading this reward. Please try again.'))
        # Redirect to journey map so user can see all available rewards
        return redirect('crush_lu:journey_map')


@crush_login_required
def certificate_view(request):
    """
    Generate and display completion certificate.
    """
    try:
        # Get user's journey progress
        journey_progress = JourneyProgress.objects.filter(
            user=request.user,
            is_completed=True
        ).select_related('journey').first()

        if not journey_progress:
            messages.warning(request, _('Complete the journey to unlock your certificate.'))
            return redirect('crush_lu:journey_map')

        # Calculate statistics
        total_chapters = journey_progress.journey.total_chapters
        total_points = journey_progress.total_points

        # Calculate time spent in human-readable format
        total_seconds = journey_progress.total_time_seconds
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60

        context = {
            'journey_progress': journey_progress,
            'journey': journey_progress.journey,
            'total_chapters': total_chapters,
            'total_points': total_points,
            'hours_spent': hours,
            'minutes_spent': minutes,
            'completion_date': journey_progress.completed_at,
        }

        return render(request, 'crush_lu/journey/certificate.html', context)

    except Exception as e:
        logger.error(f"❌ Error generating certificate for {request.user.username}: {e}", exc_info=True)
        messages.error(request, _('An error occurred generating your certificate. Please contact support.'))
        # Redirect to journey map so user can still access the journey
        return redirect('crush_lu:journey_map')
