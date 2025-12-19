"""
Crush.lu Admin Analytics Dashboard Views

Provides comprehensive analytics and insights for the Crush.lu admin panel.
"""
from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import render
from django.db.models import Count, Q, Avg, Sum, F
from django.utils import timezone
from datetime import timedelta

from .models import (
    CrushProfile, CrushCoach, ProfileSubmission, MeetupEvent, EventRegistration,
    EventConnection, JourneyProgress, SpecialUserExperience, CoachSession,
    EmailPreference
)


def crush_admin_dashboard(request):
    """
    Main analytics dashboard for Crush.lu Coach Panel.
    Provides key metrics and insights across all platform areas.

    Access: Only Crush coaches and superusers.
    """
    # Permission check: Only coaches and superusers can access
    if not request.user.is_authenticated:
        from django.contrib.auth.views import redirect_to_login
        return redirect_to_login(request.get_full_path())

    is_coach = False
    if not request.user.is_superuser:
        try:
            coach = request.user.crushcoach
            if not coach.is_active:
                from django.http import HttpResponseForbidden
                return HttpResponseForbidden("You must be an active Crush coach to access this panel.")
            is_coach = True
        except:
            from django.http import HttpResponseForbidden
            return HttpResponseForbidden("You must be a Crush coach to access this panel.")

    # ============================================================================
    # USER METRICS
    # ============================================================================

    # Total users and profiles
    total_profiles = CrushProfile.objects.count()
    active_profiles = CrushProfile.objects.filter(is_active=True).count()
    approved_profiles = CrushProfile.objects.filter(is_approved=True).count()
    pending_approval = CrushProfile.objects.filter(
        is_approved=False,
        is_active=True
    ).count()

    # Profile approval rate
    approval_rate = (approved_profiles / total_profiles * 100) if total_profiles > 0 else 0

    # Recent registrations (last 30 days)
    thirty_days_ago = timezone.now() - timedelta(days=30)
    recent_signups = CrushProfile.objects.filter(
        created_at__gte=thirty_days_ago
    ).count()

    # Gender distribution
    gender_stats = CrushProfile.objects.values('gender').annotate(
        count=Count('id')
    ).order_by('-count')

    # Location distribution (top 5)
    location_stats = CrushProfile.objects.values('location').annotate(
        count=Count('id')
    ).order_by('-count')[:5]

    # Profile completion funnel
    step1_completed = CrushProfile.objects.filter(
        completion_status__in=['step1_complete', 'step2_complete', 'step3_complete', 'submitted']
    ).count()
    step2_completed = CrushProfile.objects.filter(
        completion_status__in=['step2_complete', 'step3_complete', 'submitted']
    ).count()
    step3_completed = CrushProfile.objects.filter(
        completion_status__in=['step3_complete', 'submitted']
    ).count()
    submitted = CrushProfile.objects.filter(completion_status='submitted').count()

    # ============================================================================
    # COACH METRICS
    # ============================================================================

    total_coaches = CrushCoach.objects.count()
    active_coaches = CrushCoach.objects.filter(is_active=True).count()

    # Pending reviews across all coaches
    pending_reviews = ProfileSubmission.objects.filter(
        status='pending'
    ).count()

    # Coach performance
    coach_performance = CrushCoach.objects.filter(is_active=True).annotate(
        total_reviews=Count('assigned_submissions'),
        pending_count=Count('assigned_submissions', filter=Q(assigned_submissions__status='pending')),
        approved_count=Count('assigned_submissions', filter=Q(assigned_submissions__status='approved')),
        rejected_count=Count('assigned_submissions', filter=Q(assigned_submissions__status='rejected'))
    ).order_by('-total_reviews')[:5]

    # Average review time (if reviewed_at exists)
    reviewed_submissions = ProfileSubmission.objects.filter(
        reviewed_at__isnull=False,
        submitted_at__isnull=False
    )

    if reviewed_submissions.exists():
        total_review_time = sum([
            (submission.reviewed_at - submission.submitted_at).total_seconds() / 3600
            for submission in reviewed_submissions
        ])
        avg_review_hours = total_review_time / reviewed_submissions.count()
    else:
        avg_review_hours = 0

    # ============================================================================
    # EVENT METRICS
    # ============================================================================

    total_events = MeetupEvent.objects.count()
    upcoming_events = MeetupEvent.objects.filter(
        date_time__gte=timezone.now(),
        is_published=True,
        is_cancelled=False
    ).count()
    past_events = MeetupEvent.objects.filter(
        date_time__lt=timezone.now()
    ).count()

    # Total registrations
    total_registrations = EventRegistration.objects.count()
    confirmed_registrations = EventRegistration.objects.filter(
        status='confirmed'
    ).count()

    # Revenue metrics
    total_revenue = EventRegistration.objects.filter(
        payment_confirmed=True
    ).aggregate(
        total=Sum(F('event__registration_fee'))
    )['total'] or 0

    # Event type distribution
    event_type_stats = MeetupEvent.objects.values('event_type').annotate(
        count=Count('id')
    ).order_by('-count')

    # Top 5 most popular events (by registrations)
    popular_events = MeetupEvent.objects.annotate(
        registration_count=Count('eventregistration')
    ).order_by('-registration_count')[:5]

    # Average attendance rate
    attended_count = EventRegistration.objects.filter(
        status='attended'
    ).count()
    attendance_rate = (attended_count / confirmed_registrations * 100) if confirmed_registrations > 0 else 0

    # ============================================================================
    # CONNECTION METRICS
    # ============================================================================

    total_connections = EventConnection.objects.count()
    mutual_connections = EventConnection.objects.filter(
        status='mutual'
    ).count()
    pending_connections = EventConnection.objects.filter(
        status='pending'
    ).count()

    # Connection success rate
    connection_rate = (mutual_connections / total_connections * 100) if total_connections > 0 else 0

    # ============================================================================
    # JOURNEY SYSTEM METRICS
    # ============================================================================

    total_special_experiences = SpecialUserExperience.objects.count()
    active_special_experiences = SpecialUserExperience.objects.filter(
        is_active=True
    ).count()

    total_journeys = JourneyProgress.objects.count()
    completed_journeys = JourneyProgress.objects.filter(
        is_completed=True
    ).count()

    # Average completion rate
    journey_completion_rate = (completed_journeys / total_journeys * 100) if total_journeys > 0 else 0

    # Average points earned
    avg_points = JourneyProgress.objects.aggregate(
        avg=Avg('total_points')
    )['avg'] or 0

    # Average time spent (in minutes)
    avg_time_minutes = JourneyProgress.objects.aggregate(
        avg=Avg('total_time_seconds')
    )['avg'] or 0
    avg_time_minutes = avg_time_minutes / 60

    # ============================================================================
    # EMAIL PREFERENCE METRICS
    # ============================================================================

    total_email_preferences = EmailPreference.objects.count()
    unsubscribed_all = EmailPreference.objects.filter(unsubscribed_all=True).count()
    marketing_opted_in = EmailPreference.objects.filter(
        email_marketing=True,
        unsubscribed_all=False
    ).count()

    # Calculate opt-in rates
    email_active_users = total_email_preferences - unsubscribed_all
    marketing_opt_in_rate = (marketing_opted_in / email_active_users * 100) if email_active_users > 0 else 0

    # Email category opt-in counts (excluding unsubscribed users)
    email_category_stats = {
        'profile_updates': EmailPreference.objects.filter(email_profile_updates=True, unsubscribed_all=False).count(),
        'event_reminders': EmailPreference.objects.filter(email_event_reminders=True, unsubscribed_all=False).count(),
        'new_connections': EmailPreference.objects.filter(email_new_connections=True, unsubscribed_all=False).count(),
        'new_messages': EmailPreference.objects.filter(email_new_messages=True, unsubscribed_all=False).count(),
        'marketing': marketing_opted_in,
    }

    # ============================================================================
    # RECENT ACTIVITY
    # ============================================================================

    # Recent profile submissions (last 10)
    recent_submissions = ProfileSubmission.objects.filter(
        status='pending'
    ).select_related('profile__user', 'coach__user').order_by('-submitted_at')[:10]

    # Recent event registrations (last 10)
    recent_event_registrations = EventRegistration.objects.select_related(
        'user', 'event'
    ).order_by('-registered_at')[:10]

    # Recent connections (last 10)
    recent_connections = EventConnection.objects.select_related(
        'requester', 'recipient', 'event'
    ).order_by('-requested_at')[:10]

    # ============================================================================
    # PREPARE CONTEXT
    # ============================================================================

    context = {
        # User metrics
        'total_profiles': total_profiles,
        'active_profiles': active_profiles,
        'approved_profiles': approved_profiles,
        'pending_approval': pending_approval,
        'approval_rate': round(approval_rate, 1),
        'recent_signups': recent_signups,
        'gender_stats': gender_stats,
        'location_stats': location_stats,
        'step1_completed': step1_completed,
        'step2_completed': step2_completed,
        'step3_completed': step3_completed,
        'submitted': submitted,

        # Coach metrics
        'total_coaches': total_coaches,
        'active_coaches': active_coaches,
        'pending_reviews': pending_reviews,
        'coach_performance': coach_performance,
        'avg_review_hours': round(avg_review_hours, 1),

        # Event metrics
        'total_events': total_events,
        'upcoming_events': upcoming_events,
        'past_events': past_events,
        'total_registrations': total_registrations,
        'confirmed_registrations': confirmed_registrations,
        'total_revenue': round(total_revenue, 2),
        'event_type_stats': event_type_stats,
        'popular_events': popular_events,
        'attendance_rate': round(attendance_rate, 1),

        # Connection metrics
        'total_connections': total_connections,
        'mutual_connections': mutual_connections,
        'pending_connections': pending_connections,
        'connection_rate': round(connection_rate, 1),

        # Journey metrics
        'total_special_experiences': total_special_experiences,
        'active_special_experiences': active_special_experiences,
        'total_journeys': total_journeys,
        'completed_journeys': completed_journeys,
        'journey_completion_rate': round(journey_completion_rate, 1),
        'avg_points': round(avg_points, 0),
        'avg_time_minutes': round(avg_time_minutes, 0),

        # Email preference metrics
        'total_email_preferences': total_email_preferences,
        'unsubscribed_all': unsubscribed_all,
        'marketing_opted_in': marketing_opted_in,
        'marketing_opt_in_rate': round(marketing_opt_in_rate, 1),
        'email_active_users': email_active_users,
        'email_category_stats': email_category_stats,

        # Recent activity
        'recent_submissions': recent_submissions,
        'recent_event_registrations': recent_event_registrations,
        'recent_connections': recent_connections,

        # Page metadata
        'title': 'Crush.lu Analytics Dashboard',
        'site_header': '💕 Crush.lu Administration',
    }

    return render(request, 'admin/crush_lu/dashboard.html', context)
