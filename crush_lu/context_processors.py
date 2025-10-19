"""
Context processors for Crush.lu app
These make variables available to all templates
"""
from django.db.models import Q
from .models import EventConnection, CrushProfile, CrushCoach, SpecialUserExperience, JourneyProgress


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

        # Pending screening calls count for coaches
        if hasattr(request.user, 'crushcoach') and request.user.crushcoach.is_active:
            pending_screening_count = CrushProfile.objects.filter(
                needs_screening_call=True,
                screening_call_completed=False
            ).count()
            context['pending_screening_count'] = pending_screening_count

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
