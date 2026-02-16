"""
Context processors for Crush.lu app
These make variables available to all templates
"""
import time

from django.db.models import Q
from django.utils import timezone
from django.conf import settings
from django.utils.translation import gettext_lazy as _
from .models import (
    EventConnection, CrushProfile, CrushCoach, SpecialUserExperience,
    JourneyProgress, ProfileSubmission, EventRegistration, MeetupEvent,
    CrushSpark,
)

# Simple in-memory cache for site config (avoids DB hit on every request)
_site_config_cache = {"config": None, "expires": 0}

# Profile completion step mapping for navbar progress indicator
PROFILE_STEP_INFO = {
    'not_started': (0, _('Get started')),
    'step1': (1, _('Tell us about you')),
    'step2': (2, _('Add photos')),
    'step3': (3, _('Review & submit')),
    'submitted': (4, _('Under review')),
}


def crush_user_context(request):
    """Add user-specific context for navigation and UI"""
    context = {}

    if request.user.is_authenticated:
        # Connection count for badge
        connection_count = EventConnection.objects.filter(
            Q(requester=request.user) | Q(recipient=request.user),
            status__in=['accepted', 'coach_reviewing', 'coach_approved', 'shared']
        ).count()

        # Pending connection requests (received)
        pending_requests_count = EventConnection.objects.filter(
            recipient=request.user,
            status='pending'
        ).count()

        context['connection_count'] = connection_count
        context['pending_requests_count'] = pending_requests_count

        # Sparks needing action (approved by coach, waiting for journey creation)
        actionable_sparks_count = CrushSpark.objects.filter(
            sender=request.user,
            status__in=['coach_approved', 'coach_assigned'],
        ).count()
        context['actionable_sparks_count'] = actionable_sparks_count

        # Profile submission status for visual indicators
        profile_submission = None
        profile = CrushProfile.objects.filter(user=request.user).first()
        if profile:
            completion_status = profile.completion_status
            context['profile_completion_status'] = completion_status

            # Profile step info for navbar progress indicator
            step_info = PROFILE_STEP_INFO.get(completion_status, (0, _('Get started')))
            context['profile_completion_step'] = step_info[0]
            context['profile_step_label'] = step_info[1]

            profile_submission = ProfileSubmission.objects.filter(
                profile=profile
            ).select_related('coach__user').order_by('-submitted_at').first()

            if profile_submission:
                context['profile_submission'] = profile_submission
                context['profile_status'] = profile_submission.status
                context['profile_is_approved'] = profile.is_approved
                context['profile_needs_action'] = profile_submission.status == 'revision'

                # Coach name for pending review navbar display
                if profile_submission.coach and profile_submission.status == 'pending':
                    context['assigned_coach_name'] = profile_submission.coach.user.first_name
        else:
            # No profile yet - show step 0
            context['profile_completion_step'] = 0
            context['profile_step_label'] = _('Get started')

        # Upcoming events for user
        now = timezone.now()
        upcoming_registrations = EventRegistration.objects.filter(
            user=request.user,
            event__date_time__gte=now,
            status__in=['confirmed', 'waitlist']
        ).select_related('event').order_by('event__date_time')[:5]

        context['upcoming_events'] = upcoming_registrations
        context['upcoming_events_count'] = upcoming_registrations.count()

        # Pending screening calls count for coaches
        if hasattr(request.user, 'crushcoach') and request.user.crushcoach.is_active:
            pending_screening_count = ProfileSubmission.objects.filter(
                coach=request.user.crushcoach,
                status='pending',
                review_call_completed=False
            ).count()
            context['pending_screening_count'] = pending_screening_count

            # Count pending invitations to review (future feature)
            context['pending_invitations_count'] = 0

        # Check for special journey experience
        # Prioritize linked_user (gifts), then fall back to name match (legacy)
        special_experience = SpecialUserExperience.objects.filter(
            Q(is_active=True) &
            (
                Q(linked_user=request.user) |  # Direct link (gifts)
                Q(
                    first_name__iexact=request.user.first_name,
                    last_name__iexact=request.user.last_name,
                    linked_user__isnull=True  # Only name-match if no linked_user
                )
            )
        ).first()

        if special_experience:
            context['has_special_journey'] = True
            context['special_experience'] = special_experience

            # Check if journey is already started
            journey_progress = JourneyProgress.objects.filter(
                user=request.user
            ).first()
            context['journey_started'] = journey_progress is not None
            if journey_progress:
                context['journey_progress'] = journey_progress

    return context


def social_preview_context(request):
    """Expose social preview settings for Open Graph/Twitter tags."""
    return {
        "social_preview_image_url": settings.SOCIAL_PREVIEW_IMAGE_URL,
    }


def firebase_config(request):
    """
    Add Firebase configuration to template context.

    These values come from environment variables and are safe to expose
    to the client (they are client-side API keys, protected by domain restrictions).
    """
    return {
        'firebase_api_key': getattr(settings, 'FIREBASE_API_KEY', ''),
        'firebase_auth_domain': getattr(settings, 'FIREBASE_AUTH_DOMAIN', ''),
        'firebase_project_id': getattr(settings, 'FIREBASE_PROJECT_ID', ''),
    }


def site_config_context(request):
    """Expose CrushSiteConfig values (cached for 5 minutes)."""
    now = time.time()
    if _site_config_cache["config"] is None or now > _site_config_cache["expires"]:
        from .models import CrushSiteConfig

        try:
            config = CrushSiteConfig.get_config()

            # Build social_links list from non-empty URLs
            social_platforms = [
                ("Instagram", config.social_instagram_url, "instagram"),
                ("Facebook", config.social_facebook_url, "facebook"),
                ("LinkedIn", config.social_linkedin_url, "linkedin"),
                ("Google Business", config.social_google_business_url, "google"),
                ("Reddit", config.social_reddit_url, "reddit"),
            ]
            social_links = [
                {"name": name, "url": url, "icon_id": icon_id}
                for name, url, icon_id in social_platforms
                if url
            ]

            _site_config_cache["config"] = {
                "whatsapp_number": config.whatsapp_number,
                "whatsapp_enabled": config.whatsapp_enabled and bool(config.whatsapp_number),
                "whatsapp_default_message": config.whatsapp_default_message,
                "social_links": social_links,
            }
        except Exception:
            _site_config_cache["config"] = {
                "whatsapp_number": "",
                "whatsapp_enabled": False,
                "whatsapp_default_message": "",
                "social_links": [],
            }
        _site_config_cache["expires"] = now + 300  # 5 minutes

    return _site_config_cache["config"]
