"""
Crush.lu Admin Analytics Dashboard Views

Provides comprehensive analytics and insights for the Crush.lu admin panel.
"""
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.db.models import Count, Q, Avg, Sum, F
from django.db.models.functions import TruncDate, TruncWeek, TruncMonth
from django.http import JsonResponse
from django.utils import timezone
from datetime import timedelta
import logging
import traceback

logger = logging.getLogger(__name__)

from .models import (
    CrushProfile, CrushCoach, ProfileSubmission, MeetupEvent, EventRegistration,
    EventConnection, JourneyProgress, SpecialUserExperience, CoachSession,
    EmailPreference, PWADeviceInstallation, OAuthState, PasskitDeviceRegistration,
    # Additional models for expanded analytics
    ReferralCode, ReferralAttribution, EventInvitation, ConnectionMessage,
    ProfileReminder, UserActivity
)


def _get_date_range(request):
    """
    Parse date range from request GET parameter.

    Returns:
        tuple: (start_date, range_label) where start_date is a datetime
               and range_label is the string identifier.
    """
    range_param = request.GET.get('range', '30d')
    now = timezone.now()

    if range_param == '7d':
        return now - timedelta(days=7), '7d'
    elif range_param == '90d':
        return now - timedelta(days=90), '90d'
    elif range_param == 'all':
        return None, 'all'
    else:
        # Default to 30 days
        return now - timedelta(days=30), '30d'


@login_required
def crush_admin_dashboard(request):
    """
    Main analytics dashboard for Crush.lu Coach Panel.
    Provides key metrics and insights across all platform areas.

    Access: Only Crush coaches and superusers.

    Query Parameters:
        - range: Date range filter ('7d', '30d', '90d', 'all')
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

    # Parse date range filter
    date_start, date_range = _get_date_range(request)

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

    # Recent registrations (based on date filter or default 30 days)
    thirty_days_ago = timezone.now() - timedelta(days=30)
    recent_date = date_start if date_start else thirty_days_ago
    recent_signups = CrushProfile.objects.filter(
        created_at__gte=recent_date
    ).count()

    # Gender distribution
    gender_stats = CrushProfile.objects.values('gender').annotate(
        count=Count('id')
    ).order_by('-count')

    # Location distribution (top 10 with percentages)
    location_stats_raw = CrushProfile.objects.values('location').annotate(
        count=Count('id')
    ).order_by('-count')[:10]

    # Convert to list and add percentages
    location_stats = []
    max_location_count = 0
    for stat in location_stats_raw:
        percentage = round(stat['count'] / total_profiles * 100, 1) if total_profiles > 0 else 0
        location_stats.append({
            'location': stat['location'],
            'count': stat['count'],
            'percentage': percentage,
        })
        if stat['count'] > max_location_count:
            max_location_count = stat['count']

    # New users by location (last 30 days)
    location_recent = CrushProfile.objects.filter(
        created_at__gte=thirty_days_ago
    ).values('location').annotate(
        count=Count('id')
    ).order_by('-count')[:5]

    # Profile completion funnel - users CURRENTLY at each step (not cumulative)
    # This shows where users are stuck in the funnel
    # OPTIMIZATION: Use single aggregate query instead of 6 separate COUNT queries
    from django.db.models import Case, When, IntegerField
    funnel_stats = CrushProfile.objects.aggregate(
        funnel_not_started=Count('id', filter=Q(completion_status='not_started')),
        funnel_step1=Count('id', filter=Q(completion_status='step1')),
        funnel_step2=Count('id', filter=Q(completion_status='step2')),
        funnel_step3=Count('id', filter=Q(completion_status='step3')),
        funnel_completed=Count('id', filter=Q(completion_status='completed')),
        funnel_submitted=Count('id', filter=Q(completion_status='submitted')),
    )
    funnel_not_started = funnel_stats['funnel_not_started']
    funnel_step1 = funnel_stats['funnel_step1']
    funnel_step2 = funnel_stats['funnel_step2']
    funnel_step3 = funnel_stats['funnel_step3']
    funnel_completed = funnel_stats['funnel_completed']
    funnel_submitted = funnel_stats['funnel_submitted']

    # Calculate percentages for each step (of total profiles)
    if total_profiles > 0:
        funnel_not_started_pct = round(funnel_not_started / total_profiles * 100, 1)
        funnel_step1_pct = round(funnel_step1 / total_profiles * 100, 1)
        funnel_step2_pct = round(funnel_step2 / total_profiles * 100, 1)
        funnel_step3_pct = round(funnel_step3 / total_profiles * 100, 1)
        funnel_completed_pct = round(funnel_completed / total_profiles * 100, 1)
        funnel_submitted_pct = round(funnel_submitted / total_profiles * 100, 1)
    else:
        funnel_not_started_pct = funnel_step1_pct = funnel_step2_pct = 0
        funnel_step3_pct = funnel_completed_pct = funnel_submitted_pct = 0

    # Legacy cumulative counts (for backward compatibility)
    # OPTIMIZATION: Use single aggregate query instead of 3 separate COUNT queries
    legacy_stats = CrushProfile.objects.aggregate(
        step1_completed=Count('id', filter=~Q(completion_status='not_started')),
        step2_completed=Count('id', filter=Q(completion_status__in=['step2', 'step3', 'completed', 'submitted'])),
        step3_completed=Count('id', filter=Q(completion_status__in=['step3', 'completed', 'submitted'])),
    )
    step1_completed = legacy_stats['step1_completed']
    step2_completed = legacy_stats['step2_completed']
    step3_completed = legacy_stats['step3_completed']
    submitted = funnel_submitted

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
        total_reviews=Count('profilesubmission'),
        pending_count=Count('profilesubmission', filter=Q(profilesubmission__status='pending')),
        approved_count=Count('profilesubmission', filter=Q(profilesubmission__status='approved')),
        rejected_count=Count('profilesubmission', filter=Q(profilesubmission__status='rejected'))
    ).order_by('-total_reviews')[:5]

    # Average review time (if reviewed_at exists) - optimized with DB aggregation
    from django.db.models import ExpressionWrapper, DurationField
    from django.db.models.functions import Extract

    reviewed_submissions = ProfileSubmission.objects.filter(
        reviewed_at__isnull=False,
        submitted_at__isnull=False
    )

    if reviewed_submissions.exists():
        # Use database-level aggregation instead of Python iteration (N+1 fix)
        avg_review_result = reviewed_submissions.annotate(
            review_duration=ExpressionWrapper(
                F('reviewed_at') - F('submitted_at'),
                output_field=DurationField()
            )
        ).aggregate(
            avg_seconds=Avg(Extract('review_duration', 'epoch'))
        )
        # Fix: Ensure non-negative value (handles edge cases with null or negative results)
        avg_seconds = avg_review_result['avg_seconds'] or 0
        avg_review_hours = max(0, avg_seconds / 3600)
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
    # OPTIMIZATION: Use single aggregate query instead of 4 separate COUNT queries
    email_category_raw = EmailPreference.objects.aggregate(
        profile_updates=Count('id', filter=Q(email_profile_updates=True, unsubscribed_all=False)),
        event_reminders=Count('id', filter=Q(email_event_reminders=True, unsubscribed_all=False)),
        new_connections=Count('id', filter=Q(email_new_connections=True, unsubscribed_all=False)),
        new_messages=Count('id', filter=Q(email_new_messages=True, unsubscribed_all=False)),
    )
    email_category_stats = {
        'profile_updates': email_category_raw['profile_updates'],
        'event_reminders': email_category_raw['event_reminders'],
        'new_connections': email_category_raw['new_connections'],
        'new_messages': email_category_raw['new_messages'],
        'marketing': marketing_opted_in,
    }

    # ============================================================================
    # PWA ANALYTICS
    # ============================================================================

    pwa_week_ago = timezone.now() - timedelta(days=7)
    pwa_month_ago = timezone.now() - timedelta(days=30)

    pwa_total = PWADeviceInstallation.objects.count()
    pwa_metrics = {
        'total_installations': pwa_total,
        'unique_users': PWADeviceInstallation.objects.values('user').distinct().count(),
        'active_7d': PWADeviceInstallation.objects.filter(last_used_at__gte=pwa_week_ago).count(),
        'inactive_7d': PWADeviceInstallation.objects.filter(last_used_at__lt=pwa_week_ago).count() if pwa_total > 0 else 0,
        'new_this_month': PWADeviceInstallation.objects.filter(installed_at__gte=pwa_month_ago).count(),
        'os_distribution': list(PWADeviceInstallation.objects.values('os_type')
            .annotate(count=Count('id')).order_by('-count')),
        'form_factor_distribution': list(PWADeviceInstallation.objects.values('form_factor')
            .annotate(count=Count('id')).order_by('-count')),
        'browser_distribution': list(PWADeviceInstallation.objects.values('browser')
            .annotate(count=Count('id')).order_by('-count')[:5]),
        'recent_installations': PWADeviceInstallation.objects.select_related('user')
            .order_by('-installed_at')[:10],
        'inactive_devices': PWADeviceInstallation.objects.filter(last_used_at__lt=pwa_week_ago)
            .select_related('user').order_by('last_used_at')[:10] if pwa_total > 0 else [],
    }

    # ============================================================================
    # OAUTH STATE METRICS (for debugging Android PWA issues)
    # ============================================================================

    oauth_hour_ago = timezone.now() - timedelta(hours=1)

    oauth_metrics = {
        'total_states': OAuthState.objects.count(),
        'active_states': OAuthState.objects.filter(
            used=False,
            expires_at__gt=timezone.now()
        ).count(),
        'used_states': OAuthState.objects.filter(used=True).count(),
        'expired_states': OAuthState.objects.filter(
            used=False,
            expires_at__lt=timezone.now()
        ).count(),
        'completed_auth': OAuthState.objects.filter(auth_completed=True).count(),
        'recent_states': OAuthState.objects.filter(created_at__gte=oauth_hour_ago).count(),
        'provider_distribution': list(OAuthState.objects.exclude(provider='')
            .values('provider').annotate(count=Count('state_id')).order_by('-count')),
    }

    # ============================================================================
    # PASSKIT DEVICE METRICS (Apple Wallet registrations)
    # ============================================================================

    passkit_metrics = {
        'total_registrations': PasskitDeviceRegistration.objects.count(),
        'unique_devices': PasskitDeviceRegistration.objects.values(
            'device_library_identifier'
        ).distinct().count(),
        'unique_passes': PasskitDeviceRegistration.objects.values(
            'serial_number'
        ).distinct().count(),
        'recent_registrations': PasskitDeviceRegistration.objects.filter(
            created_at__gte=pwa_month_ago
        ).count(),
        'pass_types': list(PasskitDeviceRegistration.objects.values('pass_type_identifier')
            .annotate(count=Count('id')).order_by('-count')),
    }

    # ============================================================================
    # REFERRAL PROGRAM METRICS
    # ============================================================================

    referral_metrics = {
        'total_codes': ReferralCode.objects.count(),
        'active_codes': ReferralCode.objects.filter(is_active=True).count(),
        'total_attributions': ReferralAttribution.objects.count(),
        'converted_attributions': ReferralAttribution.objects.filter(
            status='converted'
        ).count(),
        'pending_attributions': ReferralAttribution.objects.filter(
            status='pending'
        ).count(),
        'rewards_applied': ReferralAttribution.objects.filter(
            reward_applied=True
        ).count(),
        'total_reward_points': ReferralAttribution.objects.filter(
            reward_applied=True
        ).aggregate(total=Sum('reward_points'))['total'] or 0,
        'recent_conversions': ReferralAttribution.objects.filter(
            status='converted',
            converted_at__gte=thirty_days_ago
        ).count(),
    }

    # Calculate conversion rate
    if referral_metrics['total_attributions'] > 0:
        referral_metrics['conversion_rate'] = round(
            referral_metrics['converted_attributions'] / referral_metrics['total_attributions'] * 100, 1
        )
    else:
        referral_metrics['conversion_rate'] = 0

    # Top referrers (by conversions)
    top_referrers = ReferralCode.objects.filter(
        is_active=True
    ).annotate(
        conversion_count=Count(
            'attributions',
            filter=Q(attributions__status='converted')
        )
    ).filter(
        conversion_count__gt=0
    ).select_related('referrer__user').order_by('-conversion_count')[:5]

    # ============================================================================
    # EVENT INVITATION METRICS
    # ============================================================================

    invitation_metrics = {
        'total_invitations': EventInvitation.objects.count(),
        'pending_invitations': EventInvitation.objects.filter(status='pending').count(),
        'accepted_invitations': EventInvitation.objects.filter(status='accepted').count(),
        'declined_invitations': EventInvitation.objects.filter(status='declined').count(),
        'expired_invitations': EventInvitation.objects.filter(status='expired').count(),
        'external_guests': EventInvitation.objects.filter(created_user__isnull=True).count(),
        'recent_invitations': EventInvitation.objects.filter(
            invitation_sent_at__gte=thirty_days_ago
        ).count(),
    }

    # Invitation acceptance rate
    responded = invitation_metrics['accepted_invitations'] + invitation_metrics['declined_invitations']
    if responded > 0:
        invitation_metrics['acceptance_rate'] = round(
            invitation_metrics['accepted_invitations'] / responded * 100, 1
        )
    else:
        invitation_metrics['acceptance_rate'] = 0

    # ============================================================================
    # CONNECTION MESSAGE METRICS
    # ============================================================================

    message_metrics = {
        'total_messages': ConnectionMessage.objects.count(),
        'coach_messages': ConnectionMessage.objects.filter(is_coach_message=True).count(),
        'user_messages': ConnectionMessage.objects.filter(is_coach_message=False).count(),
        'unread_messages': ConnectionMessage.objects.filter(read_at__isnull=True).count(),
        'recent_messages': ConnectionMessage.objects.filter(
            sent_at__gte=thirty_days_ago
        ).count(),
    }

    # Messages per connection (engagement)
    if total_connections > 0:
        message_metrics['avg_messages_per_connection'] = round(
            message_metrics['total_messages'] / total_connections, 1
        )
    else:
        message_metrics['avg_messages_per_connection'] = 0

    # ============================================================================
    # PROFILE REMINDER METRICS
    # ============================================================================

    reminder_metrics = {
        'total_reminders': ProfileReminder.objects.count(),
        'reminders_24h': ProfileReminder.objects.filter(reminder_type='24h').count(),
        'reminders_72h': ProfileReminder.objects.filter(reminder_type='72h').count(),
        'reminders_7d': ProfileReminder.objects.filter(reminder_type='7d').count(),
        'recent_reminders': ProfileReminder.objects.filter(
            sent_at__gte=thirty_days_ago
        ).count(),
    }

    # Check effectiveness: users who received reminders and completed profile
    users_with_reminders = ProfileReminder.objects.values('user_id').distinct()
    completed_after_reminder = CrushProfile.objects.filter(
        user_id__in=users_with_reminders,
        completion_status__in=['completed', 'submitted']
    ).count()

    if reminder_metrics['total_reminders'] > 0:
        # Approximate effectiveness (users who completed / users who received reminders)
        reminder_metrics['effectiveness_rate'] = round(
            completed_after_reminder / users_with_reminders.count() * 100, 1
        ) if users_with_reminders.count() > 0 else 0
    else:
        reminder_metrics['effectiveness_rate'] = 0

    # ============================================================================
    # PENDING ACTIONS (Coach Workflow Quick Links)
    # ============================================================================

    now = timezone.now()
    cutoff_24h = now - timedelta(hours=24)

    # Urgent reviews (pending > 24 hours)
    urgent_reviews = ProfileSubmission.objects.filter(
        status='pending',
        submitted_at__lt=cutoff_24h
    ).count()

    # Awaiting screening call (has coach, pending, no call)
    awaiting_call = ProfileSubmission.objects.filter(
        status='pending',
        coach__isnull=False,
        review_call_completed=False
    ).count()

    # Ready to approve (call completed, still pending)
    ready_to_approve = ProfileSubmission.objects.filter(
        status='pending',
        coach__isnull=False,
        review_call_completed=True
    ).count()

    # Unassigned (pending, no coach)
    unassigned_submissions = ProfileSubmission.objects.filter(
        status='pending',
        coach__isnull=True
    ).count()

    pending_actions = {
        'urgent_reviews': urgent_reviews,
        'awaiting_call': awaiting_call,
        'ready_to_approve': ready_to_approve,
        'unassigned': unassigned_submissions,
        'total_pending': pending_reviews,
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
        # Date filter info
        'date_range': date_range,
        'date_start': date_start,

        # User metrics
        'total_profiles': total_profiles,
        'active_profiles': active_profiles,
        'approved_profiles': approved_profiles,
        'pending_approval': pending_approval,
        'approval_rate': round(approval_rate, 1),
        'recent_signups': recent_signups,
        'gender_stats': gender_stats,
        'location_stats': location_stats,
        'location_recent': location_recent,
        'max_location_count': max_location_count,
        # New funnel metrics - users currently at each step
        'funnel_not_started': funnel_not_started,
        'funnel_step1': funnel_step1,
        'funnel_step2': funnel_step2,
        'funnel_step3': funnel_step3,
        'funnel_completed': funnel_completed,
        'funnel_submitted': funnel_submitted,
        'funnel_not_started_pct': funnel_not_started_pct,
        'funnel_step1_pct': funnel_step1_pct,
        'funnel_step2_pct': funnel_step2_pct,
        'funnel_step3_pct': funnel_step3_pct,
        'funnel_completed_pct': funnel_completed_pct,
        'funnel_submitted_pct': funnel_submitted_pct,
        # Legacy cumulative counts (for backward compatibility)
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

        # PWA Analytics
        'pwa_metrics': pwa_metrics,

        # OAuth State Metrics (debugging)
        'oauth_metrics': oauth_metrics,

        # PassKit Device Metrics (Apple Wallet)
        'passkit_metrics': passkit_metrics,

        # Referral Program Metrics
        'referral_metrics': referral_metrics,
        'top_referrers': top_referrers,

        # Event Invitation Metrics
        'invitation_metrics': invitation_metrics,

        # Connection Message Metrics
        'message_metrics': message_metrics,

        # Profile Reminder Metrics
        'reminder_metrics': reminder_metrics,

        # Recent activity
        'recent_submissions': recent_submissions,
        'recent_event_registrations': recent_event_registrations,
        'recent_connections': recent_connections,

        # Pending actions (workflow quick links)
        'pending_actions': pending_actions,

        # Quick action URLs for dashboard links
        'quick_links': {
            'urgent_reviews': '/crush-admin/crush_lu/profilesubmission/?workflow=urgent',
            'awaiting_call': '/crush-admin/crush_lu/profilesubmission/?workflow=awaiting_call',
            'ready_approve': '/crush-admin/crush_lu/profilesubmission/?workflow=ready_approve',
            'unassigned': '/crush-admin/crush_lu/profilesubmission/?coach_assignment=no_coach',
            'all_pending': '/crush-admin/crush_lu/profilesubmission/?status__exact=pending',
            'all_profiles': '/crush-admin/crush_lu/crushprofile/',
            'all_events': '/crush-admin/crush_lu/meetupevent/',
            'upcoming_events': '/crush-admin/crush_lu/meetupevent/?is_published__exact=1&is_cancelled__exact=0',
            'all_connections': '/crush-admin/crush_lu/eventconnection/',
            'mutual_connections': '/crush-admin/crush_lu/eventconnection/?connection_type=mutual',
            'all_journeys': '/crush-admin/crush_lu/journeyconfiguration/',
            'phone_unverified': '/crush-admin/crush_lu/crushprofile/?phone_status=unverified',
            'inactive_users': '/crush-admin/crush_lu/crushprofile/?last_login=inactive',
        },

        # Page metadata
        'title': 'Crush.lu Analytics Dashboard',
        'site_header': '💕 Crush.lu Administration',
    }

    return render(request, 'admin/crush_lu/dashboard.html', context)


# =============================================================================
# GROWTH ANALYTICS API VIEWS
# =============================================================================


def _parse_growth_params(request):
    """
    Parse range and granularity from request query params.

    Returns:
        tuple: (start_date_or_None, granularity, trunc_function)
    """
    range_param = request.GET.get('range', '30d')
    granularity = request.GET.get('granularity', '')
    now = timezone.now()

    if range_param == '7d':
        start_date = now - timedelta(days=7)
        auto_gran = 'day'
    elif range_param == '90d':
        start_date = now - timedelta(days=90)
        auto_gran = 'week'
    elif range_param == 'all':
        start_date = None
        auto_gran = 'month'
    else:
        start_date = now - timedelta(days=30)
        auto_gran = 'day'

    # Use explicit granularity if provided, otherwise auto
    gran = granularity if granularity in ('day', 'week', 'month') else auto_gran
    trunc_fn = {'day': TruncDate, 'week': TruncWeek, 'month': TruncMonth}[gran]
    return start_date, gran, trunc_fn


def _check_admin_access(request):
    """Check if user is a coach or superuser. Returns error response or None."""
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Not authenticated'}, status=401)
    if request.user.is_superuser:
        return None
    try:
        if request.user.crushcoach.is_active:
            return None
    except Exception:
        pass
    return JsonResponse({'error': 'Access denied'}, status=403)


@login_required
def signup_trend_api(request):
    """
    API: Signups and approvals per period.

    Query params:
        - range: 7d, 30d, 90d, all
        - granularity: day, week, month (auto-detected if omitted)

    Returns JSON with labels and two datasets (signups, approved).
    """
    error = _check_admin_access(request)
    if error:
        return error

    start_date, gran, trunc_fn = _parse_growth_params(request)

    # Signups per period
    qs = CrushProfile.objects.all()
    if start_date:
        qs = qs.filter(created_at__gte=start_date)

    signups = (
        qs.annotate(period=trunc_fn('created_at'))
        .values('period')
        .annotate(count=Count('id'))
        .order_by('period')
    )

    # Approvals per period (using approved_at)
    aq = CrushProfile.objects.filter(is_approved=True, approved_at__isnull=False)
    if start_date:
        aq = aq.filter(approved_at__gte=start_date)

    approvals = (
        aq.annotate(period=trunc_fn('approved_at'))
        .values('period')
        .annotate(count=Count('id'))
        .order_by('period')
    )

    # Merge into aligned labels
    signup_map = {str(r['period']): r['count'] for r in signups}
    approval_map = {str(r['period']): r['count'] for r in approvals}
    all_labels = sorted(set(list(signup_map.keys()) + list(approval_map.keys())))

    total_signups = sum(signup_map.values())
    total_approved = sum(approval_map.values())
    days = max((timezone.now() - start_date).days, 1) if start_date else max(len(all_labels), 1)

    return JsonResponse({
        'labels': all_labels,
        'signups': [signup_map.get(l, 0) for l in all_labels],
        'approved': [approval_map.get(l, 0) for l in all_labels],
        'granularity': gran,
        'summary': {
            'total_signups': total_signups,
            'total_approved': total_approved,
            'approval_rate': round(total_approved / total_signups * 100, 1) if total_signups else 0,
            'avg_per_day': round(total_signups / days, 1),
        }
    })


@login_required
def verification_trend_api(request):
    """
    API: Profile verification outcomes per period.

    Returns JSON with stacked bar data: approved, rejected, revision counts per period.
    """
    error = _check_admin_access(request)
    if error:
        return error

    start_date, gran, trunc_fn = _parse_growth_params(request)

    qs = ProfileSubmission.objects.filter(reviewed_at__isnull=False)
    if start_date:
        qs = qs.filter(reviewed_at__gte=start_date)

    results = (
        qs.annotate(period=trunc_fn('reviewed_at'))
        .values('period')
        .annotate(
            approved=Count('id', filter=Q(status='approved')),
            rejected=Count('id', filter=Q(status='rejected')),
            revision=Count('id', filter=Q(status='revision')),
        )
        .order_by('period')
    )

    labels = [str(r['period']) for r in results]
    approved = [r['approved'] for r in results]
    rejected = [r['rejected'] for r in results]
    revision = [r['revision'] for r in results]

    total_approved = sum(approved)
    total_rejected = sum(rejected)
    total_revision = sum(revision)
    total_reviews = total_approved + total_rejected + total_revision

    return JsonResponse({
        'labels': labels,
        'approved': approved,
        'rejected': rejected,
        'revision': revision,
        'granularity': gran,
        'summary': {
            'total_reviews': total_reviews,
            'total_approved': total_approved,
            'total_rejected': total_rejected,
            'total_revision': total_revision,
            'approval_rate': round(total_approved / total_reviews * 100, 1) if total_reviews else 0,
        }
    })


@login_required
def cumulative_growth_api(request):
    """
    API: Cumulative profile and approval totals over time.

    Returns JSON with two line datasets: total profiles and total approved (running sum).
    """
    error = _check_admin_access(request)
    if error:
        return error

    start_date, gran, trunc_fn = _parse_growth_params(request)

    # All profiles grouped by period
    pq = CrushProfile.objects.all()
    if start_date:
        # For cumulative, we need all data but only show labels from start_date
        # Get count before start_date as baseline
        baseline_total = CrushProfile.objects.filter(created_at__lt=start_date).count()
        baseline_approved = CrushProfile.objects.filter(
            is_approved=True, approved_at__isnull=False, approved_at__lt=start_date
        ).count()
        pq = pq.filter(created_at__gte=start_date)
    else:
        baseline_total = 0
        baseline_approved = 0

    signups = (
        pq.annotate(period=trunc_fn('created_at'))
        .values('period')
        .annotate(count=Count('id'))
        .order_by('period')
    )

    aq = CrushProfile.objects.filter(is_approved=True, approved_at__isnull=False)
    if start_date:
        aq = aq.filter(approved_at__gte=start_date)

    approvals = (
        aq.annotate(period=trunc_fn('approved_at'))
        .values('period')
        .annotate(count=Count('id'))
        .order_by('period')
    )

    signup_map = {str(r['period']): r['count'] for r in signups}
    approval_map = {str(r['period']): r['count'] for r in approvals}
    all_labels = sorted(set(list(signup_map.keys()) + list(approval_map.keys())))

    # Build cumulative arrays
    cum_total = []
    cum_approved = []
    running_total = baseline_total
    running_approved = baseline_approved
    for l in all_labels:
        running_total += signup_map.get(l, 0)
        running_approved += approval_map.get(l, 0)
        cum_total.append(running_total)
        cum_approved.append(running_approved)

    return JsonResponse({
        'labels': all_labels,
        'total_profiles': cum_total,
        'total_approved': cum_approved,
        'granularity': gran,
    })


@login_required
def daily_active_users_api(request):
    """
    API: Daily active users (DAU) per period.

    Counts distinct users with UserActivity.last_seen in each period.

    Query params:
        - range: 7d, 30d, 90d, all
        - granularity: day, week, month (auto-detected if omitted)

    Returns JSON with labels, active_users array, and summary stats.
    """
    error = _check_admin_access(request)
    if error:
        return error

    start_date, gran, trunc_fn = _parse_growth_params(request)

    qs = UserActivity.objects.all()
    if start_date:
        qs = qs.filter(last_seen__gte=start_date)

    results = (
        qs.annotate(period=trunc_fn('last_seen'))
        .values('period')
        .annotate(count=Count('user', distinct=True))
        .order_by('period')
    )

    labels = [str(r['period']) for r in results]
    active_users = [r['count'] for r in results]

    total_days = len(active_users)
    avg_dau = round(sum(active_users) / total_days, 1) if total_days else 0
    max_dau = max(active_users) if active_users else 0
    min_dau = min(active_users) if active_users else 0

    return JsonResponse({
        'labels': labels,
        'active_users': active_users,
        'granularity': gran,
        'summary': {
            'total_days': total_days,
            'avg_dau': avg_dau,
            'max_dau': max_dau,
            'min_dau': min_dau,
        }
    })


# =============================================================================
# EMAIL TEMPLATE MANAGER VIEWS
# =============================================================================

@login_required
def email_template_manager(request):
    """
    Main Email Template Manager page.

    Displays all email templates grouped by category with search and preview functionality.
    Access: Superusers and active Crush coaches only.
    """
    from .admin.email_templates_config import (
        EMAIL_CATEGORIES,
        get_templates_by_category,
    )

    # Permission check
    if not _has_email_template_access(request.user):
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden("You must be a Crush coach or superuser to access this page.")

    templates_by_category = get_templates_by_category()

    context = {
        'categories': EMAIL_CATEGORIES,
        'templates_by_category': templates_by_category,
        'title': 'Email Template Manager',
    }

    return render(request, 'admin/crush_lu/email_template_manager.html', context)


@login_required
def email_template_user_search(request):
    """
    HTMX endpoint: Search users by name or email.

    Returns a partial template with matching users for autocomplete.
    """
    from django.contrib.auth.models import User

    if not _has_email_template_access(request.user):
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden("Access denied")

    query = request.GET.get('q', '').strip()

    if len(query) < 2:
        return render(request, 'admin/crush_lu/partials/_user_search_results.html', {'users': []})

    # Search by email, first name, or last name
    users = User.objects.filter(
        Q(email__icontains=query) |
        Q(first_name__icontains=query) |
        Q(last_name__icontains=query)
    ).select_related('crushprofile').order_by('first_name', 'last_name')[:10]

    return render(request, 'admin/crush_lu/partials/_user_search_results.html', {'users': users})


@login_required
def email_template_preview(request):
    """
    HTMX endpoint: Render email template preview with selected user/context.

    GET parameters:
        - template_key: Template identifier (e.g., 'welcome')
        - user_id: Selected user ID
        - event_id: (optional) Event ID for event templates
        - connection_id: (optional) Connection ID for connection templates
        - invitation_id: (optional) Invitation ID for invitation templates
        - gift_id: (optional) Gift ID for journey templates
    """
    from django.template.loader import render_to_string
    from django.utils.html import strip_tags
    from .admin.email_templates_config import get_template_by_key

    if not _has_email_template_access(request.user):
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden("Access denied")

    template_key = request.GET.get('template_key')
    user_id = request.GET.get('user_id')

    template_meta = get_template_by_key(template_key)
    if not template_meta:
        return render(request, 'admin/crush_lu/partials/_template_preview_error.html', {
            'error': f'Template "{template_key}" not found'
        })

    # Build context based on template requirements
    try:
        context = _build_template_context(request, template_meta, user_id)
    except ValueError as e:
        return render(request, 'admin/crush_lu/partials/_template_preview_error.html', {
            'error': str(e)
        })

    # Render the email template
    try:
        html_content = render_to_string(template_meta['template'], context)
        plain_content = _html_to_plain_text(html_content)
    except Exception as e:
        logger.error(f"Error rendering email template preview: {e}")
        logger.error(traceback.format_exc())
        return render(request, 'admin/crush_lu/partials/_template_preview_error.html', {
            'error': 'Error rendering template. Please check the server logs for details.'
        })

    # Base64 encode for safe transfer through HTML attributes
    import base64
    html_content_base64 = base64.b64encode(html_content.encode('utf-8')).decode('ascii')
    plain_content_base64 = base64.b64encode(plain_content.encode('utf-8')).decode('ascii')

    return render(request, 'admin/crush_lu/partials/_template_preview.html', {
        'template_meta': template_meta,
        'template_key': template_key,
        'html_content_base64': html_content_base64,
        'plain_content_base64': plain_content_base64,
        'subject': template_meta.get('subject', ''),
        'user_id': user_id,
        'context': context,
    })


@login_required
def email_template_send(request):
    """
    POST endpoint: Send email using Django email backend.

    POST parameters:
        - template_key: Template identifier
        - user_id: Recipient user ID
        - (optional context params based on template)
    """
    from django.http import JsonResponse
    from django.template.loader import render_to_string
    from django.utils.html import strip_tags
    from azureproject.email_utils import send_domain_email
    from .admin.email_templates_config import get_template_by_key

    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required'}, status=405)

    if not _has_email_template_access(request.user):
        return JsonResponse({'success': False, 'error': 'Access denied'}, status=403)

    template_key = request.POST.get('template_key')
    user_id = request.POST.get('user_id')

    template_meta = get_template_by_key(template_key)
    if not template_meta:
        return JsonResponse({'success': False, 'error': f'Template "{template_key}" not found'})

    # Build context
    try:
        context = _build_template_context(request, template_meta, user_id)
    except ValueError as e:
        logger.error(f"Validation error building template context: {e}")
        return JsonResponse({'success': False, 'error': 'Invalid template parameters'})

    # Get recipient email
    recipient_email = _get_recipient_email(template_meta, context)
    if not recipient_email:
        return JsonResponse({'success': False, 'error': 'Could not determine recipient email'})

    # Render email
    try:
        html_content = render_to_string(template_meta['template'], context)
        plain_content = strip_tags(html_content)
    except Exception as e:
        logger.error(f"Error rendering email template: {e}")
        logger.error(traceback.format_exc())
        return JsonResponse({'success': False, 'error': 'Error rendering template'})

    # Send email
    try:
        result = send_domain_email(
            subject=template_meta.get('subject', 'Crush.lu Notification'),
            message=plain_content,
            html_message=html_content,
            recipient_list=[recipient_email],
            request=request,
            fail_silently=False,
        )

        if result > 0:
            return JsonResponse({
                'success': True,
                'message': f'Email sent successfully to {recipient_email}'
            })
        else:
            return JsonResponse({'success': False, 'error': 'Email send returned 0 (possible delivery issue)'})

    except Exception as e:
        logger.error(f"Failed to send email: {e}")
        logger.error(traceback.format_exc())
        return JsonResponse({'success': False, 'error': 'Failed to send email'})


@login_required
def email_template_create_draft(request):
    """
    POST endpoint: Create a draft email in Outlook via Microsoft Graph API.

    This creates a draft in the sender's Outlook mailbox that can be opened and sent manually.
    Supports full HTML formatting unlike mailto: links.

    POST parameters:
        - template_key: Template identifier
        - user_id: Recipient user ID
        - (optional context params based on template)

    Returns:
        JSON with web_link to open the draft in Outlook Web
    """
    from django.http import JsonResponse
    from django.template.loader import render_to_string
    from django.conf import settings
    from .admin.email_templates_config import get_template_by_key

    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required'}, status=405)

    if not _has_email_template_access(request.user):
        return JsonResponse({'success': False, 'error': 'Access denied'}, status=403)

    # Check if Graph API credentials are available using the domain config
    from azureproject.email_utils import get_domain_email_config
    config = get_domain_email_config(request=request)
    if not all([config.get('GRAPH_TENANT_ID'), config.get('GRAPH_CLIENT_ID'), config.get('GRAPH_CLIENT_SECRET')]):
        return JsonResponse({
            'success': False,
            'error': 'Graph API credentials not configured. Set GRAPH_TENANT_ID, GRAPH_CLIENT_ID, and GRAPH_CLIENT_SECRET environment variables.'
        })

    template_key = request.POST.get('template_key')
    user_id = request.POST.get('user_id')

    template_meta = get_template_by_key(template_key)
    if not template_meta:
        return JsonResponse({'success': False, 'error': f'Template "{template_key}" not found'})

    # Build context
    try:
        context = _build_template_context(request, template_meta, user_id)
    except ValueError as e:
        logger.error(f"Validation error building template context: {e}")
        return JsonResponse({'success': False, 'error': 'Invalid template parameters'})

    # Get recipient email
    recipient_email = _get_recipient_email(template_meta, context)
    if not recipient_email:
        return JsonResponse({'success': False, 'error': 'Could not determine recipient email'})

    # Render email
    try:
        html_content = render_to_string(template_meta['template'], context)
    except Exception as e:
        logger.error(f"Error rendering email template: {e}")
        logger.error(traceback.format_exc())
        return JsonResponse({'success': False, 'error': 'Error rendering template'})

    # Create draft via Graph API
    try:
        from azureproject.graph_email_backend import create_outlook_draft

        result = create_outlook_draft(
            subject=template_meta.get('subject', 'Crush.lu Notification'),
            html_content=html_content,
            recipient_email=recipient_email,
        )

        if result['success']:
            return JsonResponse({
                'success': True,
                'message': f'Draft created for {recipient_email}',
                'web_link': result.get('web_link', '')
            })
        else:
            return JsonResponse({'success': False, 'error': result.get('error', 'Unknown error')})

    except Exception as e:
        logger.error(f"Failed to create Outlook draft: {e}")
        logger.error(traceback.format_exc())
        return JsonResponse({'success': False, 'error': 'Failed to create draft'})


@login_required
def email_template_load_events(request):
    """
    HTMX endpoint: Load events for dropdown.

    Returns upcoming events plus recent past events for preview testing.
    """
    if not _has_email_template_access(request.user):
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden("Access denied")

    # Get upcoming events + some recent past events (for testing)
    events = MeetupEvent.objects.filter(
        is_published=True
    ).order_by('-date_time')[:20]

    return render(request, 'admin/crush_lu/partials/_event_dropdown.html', {'events': events})


@login_required
def email_template_load_connections(request):
    """
    HTMX endpoint: Load connections for dropdown based on selected user.
    """
    if not _has_email_template_access(request.user):
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden("Access denied")

    user_id = request.GET.get('user_id')
    if not user_id:
        return render(request, 'admin/crush_lu/partials/_connection_dropdown.html', {'connections': []})

    connections = EventConnection.objects.filter(
        Q(requester_id=user_id) | Q(recipient_id=user_id)
    ).select_related('requester', 'recipient', 'event').order_by('-requested_at')[:20]

    return render(request, 'admin/crush_lu/partials/_connection_dropdown.html', {'connections': connections})


@login_required
def email_template_load_invitations(request):
    """
    HTMX endpoint: Load invitations for dropdown.
    """
    from .models import EventInvitation

    if not _has_email_template_access(request.user):
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden("Access denied")

    invitations = EventInvitation.objects.select_related(
        'event', 'created_user'
    ).order_by('-invitation_sent_at')[:20]

    return render(request, 'admin/crush_lu/partials/_invitation_dropdown.html', {'invitations': invitations})


@login_required
def email_template_load_gifts(request):
    """
    HTMX endpoint: Load journey gifts for dropdown.
    """
    from .models import JourneyGift

    if not _has_email_template_access(request.user):
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden("Access denied")

    gifts = JourneyGift.objects.select_related('sender').order_by('-created_at')[:20]

    return render(request, 'admin/crush_lu/partials/_gift_dropdown.html', {'gifts': gifts})


def _has_email_template_access(user):
    """
    Check if user has access to email template manager.

    Returns True for superusers and active Crush coaches.
    """
    if user.is_superuser:
        return True
    try:
        return user.crushcoach.is_active
    except Exception:
        return False


def _build_template_context(request, template_meta, user_id):
    """
    Build context dictionary for rendering email template.

    Args:
        request: Django request object
        template_meta: Template metadata dict
        user_id: Selected user ID

    Returns:
        dict: Context for template rendering

    Raises:
        ValueError: If required context cannot be built
    """
    from django.contrib.auth.models import User
    from .email_helpers import get_email_context_with_unsubscribe, get_user_language_url

    context = {}
    required = template_meta.get('required_context', [])

    # Handle templates that require a user
    if 'user' in required:
        if not user_id:
            raise ValueError('Please select a user')
        try:
            user = User.objects.select_related('crushprofile').get(id=user_id)
            context['user'] = user
            context['first_name'] = user.first_name
        except User.DoesNotExist:
            raise ValueError(f'User with ID {user_id} not found')

    # Add profile if user has one
    if 'profile' in required or 'profile' in template_meta.get('optional_context', []):
        user = context.get('user')
        if user and hasattr(user, 'crushprofile'):
            context['profile'] = user.crushprofile
            context['completion_status'] = user.crushprofile.completion_status

    # Handle profile submission context
    if 'submission' in required:
        user = context.get('user')
        if user:
            try:
                submission = ProfileSubmission.objects.filter(
                    profile__user=user
                ).select_related('profile', 'coach').latest('submitted_at')
                context['submission'] = submission
                context['profile'] = submission.profile
            except ProfileSubmission.DoesNotExist:
                # Create mock submission for preview
                context['submission'] = _create_mock_submission(user)

    # Handle coach context (for recontact email)
    if 'coach' in required:
        user = context.get('user')
        if user:
            try:
                submission = ProfileSubmission.objects.filter(
                    profile__user=user
                ).select_related('coach', 'coach__user').latest('submitted_at')
                if submission.coach:
                    context['coach'] = submission.coach
            except ProfileSubmission.DoesNotExist:
                pass
        if 'coach' not in context:
            # Create mock coach for preview
            from types import SimpleNamespace
            mock_coach_user = SimpleNamespace(
                first_name='Sarah',
                last_name='Coach',
                email='coach@crush.lu',
                get_full_name=lambda: 'Sarah Coach',
            )
            context['coach'] = SimpleNamespace(user=mock_coach_user)

    # Handle event context
    if 'event' in required:
        event_id = request.GET.get('event_id') or request.POST.get('event_id')
        if event_id:
            try:
                event = MeetupEvent.objects.get(id=event_id)
                context['event'] = event
            except MeetupEvent.DoesNotExist:
                # Use first upcoming event as fallback
                event = MeetupEvent.objects.filter(is_published=True).first()
                context['event'] = event
        else:
            # Use first upcoming event for preview
            event = MeetupEvent.objects.filter(is_published=True).first()
            context['event'] = event

    # Handle registration context
    if 'registration' in required:
        user = context.get('user')
        event = context.get('event')
        if user and event:
            try:
                registration = EventRegistration.objects.get(user=user, event=event)
                context['registration'] = registration
            except EventRegistration.DoesNotExist:
                # Create mock registration for preview
                context['registration'] = _create_mock_registration(user, event)

    # Handle connection context
    if 'connection' in required:
        connection_id = request.GET.get('connection_id') or request.POST.get('connection_id')
        if connection_id:
            try:
                connection = EventConnection.objects.select_related(
                    'requester', 'recipient', 'event'
                ).get(id=connection_id)
                context['connection'] = connection
                # Set requester/accepter names for templates
                if context.get('user'):
                    user = context['user']
                    if connection.requester == user:
                        other_user = connection.recipient
                    else:
                        other_user = connection.requester
                    if hasattr(other_user, 'crushprofile'):
                        context['requester_name'] = other_user.crushprofile.display_name
                        context['accepter_name'] = other_user.crushprofile.display_name
                    else:
                        context['requester_name'] = other_user.first_name
                        context['accepter_name'] = other_user.first_name
                    if connection.event:
                        context['event_title'] = connection.event.title
                        context['event_date'] = connection.event.date_time.strftime('%B %d, %Y')
            except EventConnection.DoesNotExist:
                pass

    # Handle message context
    if 'message' in required:
        from .models import ConnectionMessage
        message_id = request.GET.get('message_id') or request.POST.get('message_id')
        if message_id:
            try:
                message = ConnectionMessage.objects.select_related(
                    'sender', 'connection'
                ).get(id=message_id)
                context['message'] = message
                if hasattr(message.sender, 'crushprofile'):
                    context['sender_name'] = message.sender.crushprofile.display_name
                else:
                    context['sender_name'] = message.sender.first_name
                context['message_preview'] = message.message[:100] + ('...' if len(message.message) > 100 else '')
            except ConnectionMessage.DoesNotExist:
                # Create mock message for preview
                context['message'] = _create_mock_message()
                context['sender_name'] = 'Jane'
                context['message_preview'] = 'Hey! It was great meeting you at the event...'

    # Handle invitation context
    if 'invitation' in required:
        from .models import EventInvitation
        invitation_id = request.GET.get('invitation_id') or request.POST.get('invitation_id')
        if invitation_id:
            try:
                invitation = EventInvitation.objects.select_related('event', 'created_user').get(id=invitation_id)
                context['invitation'] = invitation
                context['event'] = invitation.event
                context['guest_first_name'] = invitation.guest_first_name
                # Build invitation URL for preview
                context['invitation_url'] = f'https://crush.lu/en/invitation/{invitation.invitation_code}/'
            except EventInvitation.DoesNotExist:
                pass
        elif context.get('event'):
            # Create mock invitation for preview
            context['guest_first_name'] = context.get('user', type('obj', (object,), {'first_name': 'Guest'})).first_name
            context['invitation_url'] = 'https://crush.lu/en/invitation/MOCK123/'

    # Handle gift context
    if 'gift' in required:
        from .models import JourneyGift
        gift_id = request.GET.get('gift_id') or request.POST.get('gift_id')
        if gift_id:
            try:
                gift = JourneyGift.objects.select_related('sender').get(id=gift_id)
                context['gift'] = gift
                context['recipient_name'] = gift.recipient_name
                context['sender_name'] = gift.sender.first_name
                context['sender_message'] = gift.sender_message
                context['gift_code'] = gift.gift_code
                context['claim_url'] = f'https://crush.lu/en/journey/gift/{gift.gift_code}/'
            except JourneyGift.DoesNotExist:
                pass
        else:
            # Create mock gift context for preview
            user = context.get('user')
            context['recipient_name'] = user.first_name if user else 'Alice'
            context['sender_name'] = 'Someone Special'
            context['sender_message'] = 'I created this magical journey just for you!'
            context['gift_code'] = 'MOCK123'
            context['claim_url'] = 'https://crush.lu/en/journey/gift/MOCK123/'

    # Add common URLs
    user = context.get('user')
    if user:
        try:
            context['profile_url'] = get_user_language_url(user, 'crush_lu:create_profile', request)
            context['events_url'] = get_user_language_url(user, 'crush_lu:event_list', request)
            context['how_it_works_url'] = get_user_language_url(user, 'crush_lu:how_it_works', request)
            context['connections_url'] = get_user_language_url(user, 'crush_lu:my_connections', request)

            # Add base URLs for footer
            from .email_helpers import get_email_base_urls, get_unsubscribe_url
            base_urls = get_email_base_urls(user, request)
            context.update(base_urls)
            context['unsubscribe_url'] = get_unsubscribe_url(user, request)
        except Exception:
            # Fallback URLs for preview
            context['profile_url'] = 'https://crush.lu/en/create-profile/'
            context['events_url'] = 'https://crush.lu/en/events/'
            context['how_it_works_url'] = 'https://crush.lu/en/how-it-works/'
            context['connections_url'] = 'https://crush.lu/en/connections/'
            context['home_url'] = 'https://crush.lu/en/'
            context['about_url'] = 'https://crush.lu/en/about/'
            context['settings_url'] = 'https://crush.lu/en/account/settings/'
            context['unsubscribe_url'] = 'https://crush.lu/en/email/unsubscribe/TOKEN/'

    # Add event-specific URLs
    event = context.get('event')
    if event and user:
        try:
            context['event_url'] = get_user_language_url(
                user, 'crush_lu:event_detail', request, kwargs={'event_id': event.id}
            )
            context['cancel_url'] = get_user_language_url(
                user, 'crush_lu:event_cancel', request, kwargs={'event_id': event.id}
            )
        except Exception:
            context['event_url'] = f'https://crush.lu/en/events/{event.id}/'
            context['cancel_url'] = f'https://crush.lu/en/events/{event.id}/cancel/'

    # Add connection-specific URLs
    connection = context.get('connection')
    if connection and user:
        try:
            context['connection_url'] = get_user_language_url(
                user, 'crush_lu:connection_detail', request, kwargs={'connection_id': connection.id}
            )
        except Exception:
            context['connection_url'] = f'https://crush.lu/en/connections/{connection.id}/'

    # Add optional context fields with mock data for preview
    optional = template_meta.get('optional_context', [])
    if 'coach_notes' in optional and 'coach_notes' not in context:
        context['coach_notes'] = 'Great profile! Looking forward to seeing you at events.'
    if 'feedback' in optional and 'feedback' not in context:
        context['feedback'] = 'Please add at least one clear photo of yourself.'
    if 'reason' in optional and 'reason' not in context:
        context['reason'] = 'Profile does not meet our community guidelines.'
    if 'days_until_event' in optional and 'days_until_event' not in context:
        context['days_until_event'] = 3

    return context


def _get_recipient_email(template_meta, context):
    """
    Determine recipient email based on template type and context.
    """
    recipient_type = template_meta.get('recipient_type', 'user')

    if recipient_type == 'coach':
        # Coach assignment emails go to the coach
        submission = context.get('submission')
        if submission and submission.coach:
            return submission.coach.user.email
        return None

    # Check for invitation (external guest email)
    invitation = context.get('invitation')
    if invitation and hasattr(invitation, 'guest_email') and invitation.guest_email:
        return invitation.guest_email

    # Check for gift recipient
    gift = context.get('gift')
    if gift and hasattr(gift, 'recipient_email') and gift.recipient_email:
        return gift.recipient_email

    # Default: user's email
    user = context.get('user')
    if user:
        return user.email

    return None


def _create_mock_submission(user):
    """Create a mock ProfileSubmission object for preview."""
    class MockSubmission:
        def __init__(self, user):
            self.profile = user.crushprofile if hasattr(user, 'crushprofile') else None
            self.coach = None
            self.status = 'pending'
            self.submitted_at = timezone.now()
    return MockSubmission(user)


def _create_mock_registration(user, event):
    """Create a mock EventRegistration object for preview."""
    class MockRegistration:
        def __init__(self, user, event):
            self.user = user
            self.event = event
            self.status = 'confirmed'
            self.registered_at = timezone.now()
            self.dietary_restrictions = ''
    return MockRegistration(user, event)


def _create_mock_message():
    """Create a mock ConnectionMessage object for preview."""
    class MockMessage:
        def __init__(self):
            self.message = "Hey! It was great meeting you at the event. Would love to grab coffee sometime!"
            self.sent_at = timezone.now()
    return MockMessage()


def _html_to_plain_text(html_content):
    """
    Convert HTML email to clean plain text.

    Removes style/script tags, converts common HTML elements to text equivalents,
    and cleans up whitespace.
    """
    import re
    from django.utils.html import strip_tags

    text = html_content

    # Remove style and script tags with their content
    text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL | re.IGNORECASE)

    # Remove HTML comments
    text = re.sub(r'<!--.*?-->', '', text, flags=re.DOTALL)

    # Convert <br> and <br/> to newlines
    text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)

    # Convert </p>, </div>, </tr>, </li> to double newlines
    text = re.sub(r'</p>', '\n\n', text, flags=re.IGNORECASE)
    text = re.sub(r'</div>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'</tr>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'</li>', '\n', text, flags=re.IGNORECASE)

    # Convert <li> to bullet points
    text = re.sub(r'<li[^>]*>', '  • ', text, flags=re.IGNORECASE)

    # Convert headings to uppercase with newlines
    text = re.sub(r'<h[1-6][^>]*>(.*?)</h[1-6]>', r'\n\n\1\n', text, flags=re.DOTALL | re.IGNORECASE)

    # Extract href from links and show as [text](url)
    text = re.sub(r'<a[^>]*href=["\']([^"\']*)["\'][^>]*>(.*?)</a>', r'\2 (\1)', text, flags=re.DOTALL | re.IGNORECASE)

    # Now strip remaining HTML tags
    text = strip_tags(text)

    # Decode HTML entities
    import html
    text = html.unescape(text)

    # Clean up whitespace
    # Replace multiple spaces with single space
    text = re.sub(r'[ \t]+', ' ', text)

    # Replace 3+ newlines with 2 newlines
    text = re.sub(r'\n{3,}', '\n\n', text)

    # Strip leading/trailing whitespace from each line
    lines = [line.strip() for line in text.split('\n')]
    text = '\n'.join(lines)

    # Remove leading/trailing whitespace from entire text
    text = text.strip()

    return text
