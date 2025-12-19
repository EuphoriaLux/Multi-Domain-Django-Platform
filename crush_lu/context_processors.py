"""
Context processors for Crush.lu app
These make variables available to all templates
"""
from django.db.models import Q
from django.utils import timezone
from django.conf import settings
from .models import (
    EventConnection, CrushProfile, CrushCoach, SpecialUserExperience,
    JourneyProgress, ProfileSubmission, EventRegistration, MeetupEvent
)


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

        # Profile submission status for visual indicators
        profile_submission = None
        if hasattr(request.user, 'crushprofile'):
            profile_submission = ProfileSubmission.objects.filter(
                profile=request.user.crushprofile
            ).order_by('-submitted_at').first()

            if profile_submission:
                context['profile_submission'] = profile_submission
                context['profile_status'] = profile_submission.status
                context['profile_is_approved'] = request.user.crushprofile.is_approved
                context['profile_needs_action'] = profile_submission.status == 'revision'

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
        special_experience = SpecialUserExperience.objects.filter(
            first_name__iexact=request.user.first_name,
            last_name__iexact=request.user.last_name,
            is_active=True
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
