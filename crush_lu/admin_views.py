"""
Crush.lu Admin Analytics Dashboard Views

Provides comprehensive analytics and insights for the Crush.lu admin panel.
"""
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.db.models import Count, Q, Avg, Sum, F
from django.utils import timezone
from datetime import timedelta

from .models import (
    CrushProfile, CrushCoach, ProfileSubmission, MeetupEvent, EventRegistration,
    EventConnection, JourneyProgress, SpecialUserExperience, CoachSession,
    EmailPreference, PWADeviceInstallation, OAuthState, PasskitDeviceRegistration,
    # Additional models for expanded analytics
    ReferralCode, ReferralAttribution, EventInvitation, ConnectionMessage,
    ProfileReminder
)


@login_required
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

    # Profile completion funnel - users CURRENTLY at each step (not cumulative)
    # This shows where users are stuck in the funnel
    funnel_not_started = CrushProfile.objects.filter(completion_status='not_started').count()
    funnel_step1 = CrushProfile.objects.filter(completion_status='step1').count()
    funnel_step2 = CrushProfile.objects.filter(completion_status='step2').count()
    funnel_step3 = CrushProfile.objects.filter(completion_status='step3').count()
    funnel_completed = CrushProfile.objects.filter(completion_status='completed').count()
    funnel_submitted = CrushProfile.objects.filter(completion_status='submitted').count()

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
    step1_completed = CrushProfile.objects.exclude(completion_status='not_started').count()
    step2_completed = CrushProfile.objects.filter(
        completion_status__in=['step2', 'step3', 'completed', 'submitted']
    ).count()
    step3_completed = CrushProfile.objects.filter(
        completion_status__in=['step3', 'completed', 'submitted']
    ).count()
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
        avg_review_hours = (avg_review_result['avg_seconds'] or 0) / 3600
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
        # User metrics
        'total_profiles': total_profiles,
        'active_profiles': active_profiles,
        'approved_profiles': approved_profiles,
        'pending_approval': pending_approval,
        'approval_rate': round(approval_rate, 1),
        'recent_signups': recent_signups,
        'gender_stats': gender_stats,
        'location_stats': location_stats,
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
