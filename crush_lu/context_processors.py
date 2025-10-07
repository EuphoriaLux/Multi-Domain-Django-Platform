"""
Context processors for Crush.lu app
These make variables available to all templates
"""
from django.db.models import Q
from .models import EventConnection, CrushProfile, CrushCoach


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

    return context
