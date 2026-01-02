"""
User Segments View for Crush.lu Admin Panel.

Provides admin dashboard for viewing and managing user segments:
- Incomplete profiles by step
- Inactive users (7d, 14d, 30d)
- Pending reviews (urgent, normal)
- Approved but never registered for event
- No push subscription
- Unsubscribed from emails
- Profile reminder tracking

Access: Superadmins only (due to bulk email capability)
"""
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required, user_passes_test
from django.shortcuts import render, redirect
from django.db.models import Count, Q, F, Exists, OuterRef
from django.utils import timezone
from django.contrib import messages
from django.http import HttpResponse
from datetime import timedelta
import csv

from crush_lu.models import (
    CrushProfile,
    ProfileSubmission,
    MeetupEvent,
    EventRegistration,
    UserActivity,
    EmailPreference,
    PushSubscription,
    ProfileReminder,
)


def is_superuser(user):
    """Check if user is a superuser (required for bulk email access)."""
    return user.is_superuser


# ============================================================================
# SEGMENT DEFINITIONS
# ============================================================================

def get_segment_definitions():
    """
    Return all segment definitions with their queries and metadata.
    Each segment has: name, description, query, count, action_url
    """
    now = timezone.now()
    seven_days_ago = now - timedelta(days=7)
    fourteen_days_ago = now - timedelta(days=14)
    thirty_days_ago = now - timedelta(days=30)

    # Profile completion segments
    incomplete_not_started = CrushProfile.objects.filter(
        completion_status='not_started',
        is_active=True
    )

    incomplete_step1 = CrushProfile.objects.filter(
        completion_status='step1',
        is_active=True
    )

    incomplete_step2 = CrushProfile.objects.filter(
        completion_status='step2',
        is_active=True
    )

    incomplete_step3 = CrushProfile.objects.filter(
        completion_status='step3',
        is_active=True
    )

    # Pending review segments
    pending_reviews_urgent = ProfileSubmission.objects.filter(
        status='pending',
        submitted_at__lt=now - timedelta(hours=72)
    )

    pending_reviews_normal = ProfileSubmission.objects.filter(
        status='pending',
        submitted_at__gte=now - timedelta(hours=72)
    )

    # Inactive user segments (based on UserActivity.last_seen)
    inactive_7d = UserActivity.objects.filter(
        last_seen__lt=seven_days_ago,
        last_seen__gte=fourteen_days_ago
    )

    inactive_14d = UserActivity.objects.filter(
        last_seen__lt=fourteen_days_ago,
        last_seen__gte=thirty_days_ago
    )

    inactive_30d = UserActivity.objects.filter(
        last_seen__lt=thirty_days_ago
    )

    # Approved but never registered for event
    approved_no_events = CrushProfile.objects.filter(
        is_approved=True,
        is_active=True
    ).exclude(
        user__eventregistration__isnull=False
    )

    # No push subscription
    no_push_subscription = CrushProfile.objects.filter(
        is_approved=True,
        is_active=True
    ).exclude(
        user__pushsubscription__isnull=False
    )

    # Unsubscribed from all emails
    unsubscribed_all = EmailPreference.objects.filter(
        unsubscribed_all=True
    )

    # Users eligible for 24h reminder (signed up 24-48h ago, incomplete profile, no 24h reminder sent)
    eligible_24h_reminder = CrushProfile.objects.filter(
        completion_status__in=['not_started', 'step1', 'step2'],
        created_at__lte=now - timedelta(hours=24),
        created_at__gte=now - timedelta(hours=48),
        is_active=True
    ).exclude(
        user__profile_reminders__reminder_type='24h'
    )

    # Users eligible for 72h reminder
    eligible_72h_reminder = CrushProfile.objects.filter(
        completion_status__in=['not_started', 'step1', 'step2'],
        created_at__lte=now - timedelta(hours=72),
        created_at__gte=now - timedelta(hours=96),
        is_active=True,
        user__profile_reminders__reminder_type='24h'  # Must have received 24h reminder
    ).exclude(
        user__profile_reminders__reminder_type='72h'
    )

    # Users eligible for 7d final reminder
    eligible_7d_reminder = CrushProfile.objects.filter(
        completion_status__in=['not_started', 'step1', 'step2'],
        created_at__lte=now - timedelta(hours=168),
        created_at__gte=now - timedelta(hours=192),
        is_active=True,
        user__profile_reminders__reminder_type='72h'  # Must have received 72h reminder
    ).exclude(
        user__profile_reminders__reminder_type='7d'
    )

    return {
        'profile_completion': {
            'title': 'Profile Completion',
            'icon': '👤',
            'segments': [
                {
                    'name': 'Not Started',
                    'key': 'not_started',
                    'description': 'Users who created account but never started profile',
                    'queryset': incomplete_not_started,
                    'count': incomplete_not_started.count(),
                    'color': 'red',
                },
                {
                    'name': 'Step 1 Incomplete',
                    'key': 'step1',
                    'description': 'Started profile basics, stopped before personal info',
                    'queryset': incomplete_step1,
                    'count': incomplete_step1.count(),
                    'color': 'orange',
                },
                {
                    'name': 'Step 2 Incomplete',
                    'key': 'step2',
                    'description': 'Completed personal info, stopped before photos',
                    'queryset': incomplete_step2,
                    'count': incomplete_step2.count(),
                    'color': 'yellow',
                },
                {
                    'name': 'Step 3 Incomplete',
                    'key': 'step3',
                    'description': 'Added photos, never submitted for review',
                    'queryset': incomplete_step3,
                    'count': incomplete_step3.count(),
                    'color': 'blue',
                },
            ]
        },
        'pending_reviews': {
            'title': 'Pending Reviews',
            'icon': '📝',
            'segments': [
                {
                    'name': 'Urgent (>72h)',
                    'key': 'pending_urgent',
                    'description': 'Profiles waiting for review more than 72 hours',
                    'queryset': pending_reviews_urgent,
                    'count': pending_reviews_urgent.count(),
                    'color': 'red',
                    'is_urgent': True,
                },
                {
                    'name': 'Normal (<72h)',
                    'key': 'pending_normal',
                    'description': 'Profiles waiting for review less than 72 hours',
                    'queryset': pending_reviews_normal,
                    'count': pending_reviews_normal.count(),
                    'color': 'green',
                },
            ]
        },
        'user_activity': {
            'title': 'User Activity',
            'icon': '📊',
            'segments': [
                {
                    'name': 'Inactive 7-14 days',
                    'key': 'inactive_7d',
                    'description': 'Users not seen for 7-14 days',
                    'queryset': inactive_7d,
                    'count': inactive_7d.count(),
                    'color': 'yellow',
                },
                {
                    'name': 'Inactive 14-30 days',
                    'key': 'inactive_14d',
                    'description': 'Users not seen for 14-30 days',
                    'queryset': inactive_14d,
                    'count': inactive_14d.count(),
                    'color': 'orange',
                },
                {
                    'name': 'Churned (>30 days)',
                    'key': 'inactive_30d',
                    'description': 'Users not seen for over 30 days',
                    'queryset': inactive_30d,
                    'count': inactive_30d.count(),
                    'color': 'red',
                },
            ]
        },
        'engagement': {
            'title': 'Engagement',
            'icon': '💕',
            'segments': [
                {
                    'name': 'Approved, No Events',
                    'key': 'approved_no_events',
                    'description': 'Approved profiles who never registered for an event',
                    'queryset': approved_no_events,
                    'count': approved_no_events.count(),
                    'color': 'orange',
                },
                {
                    'name': 'No Push Subscription',
                    'key': 'no_push',
                    'description': 'Approved users without push notifications',
                    'queryset': no_push_subscription,
                    'count': no_push_subscription.count(),
                    'color': 'blue',
                },
            ]
        },
        'email_preferences': {
            'title': 'Email Preferences',
            'icon': '📧',
            'segments': [
                {
                    'name': 'Fully Unsubscribed',
                    'key': 'unsubscribed_all',
                    'description': 'Users who unsubscribed from all emails',
                    'queryset': unsubscribed_all,
                    'count': unsubscribed_all.count(),
                    'color': 'gray',
                },
            ]
        },
        'reminder_eligible': {
            'title': 'Reminder Eligible',
            'icon': '🔔',
            'segments': [
                {
                    'name': '24h Reminder Due',
                    'key': 'reminder_24h',
                    'description': 'Incomplete profiles signed up 24-48h ago, no reminder sent',
                    'queryset': eligible_24h_reminder,
                    'count': eligible_24h_reminder.count(),
                    'color': 'green',
                },
                {
                    'name': '72h Reminder Due',
                    'key': 'reminder_72h',
                    'description': 'Incomplete profiles, 72-96h ago, received 24h reminder',
                    'queryset': eligible_72h_reminder,
                    'count': eligible_72h_reminder.count(),
                    'color': 'yellow',
                },
                {
                    'name': '7d Final Reminder Due',
                    'key': 'reminder_7d',
                    'description': 'Incomplete profiles, 7-8 days ago, received 72h reminder',
                    'queryset': eligible_7d_reminder,
                    'count': eligible_7d_reminder.count(),
                    'color': 'orange',
                },
            ]
        },
    }


# ============================================================================
# VIEW FUNCTIONS
# ============================================================================

@login_required
@user_passes_test(is_superuser)
def user_segments_dashboard(request):
    """
    Main user segments dashboard.
    Shows all segment categories with counts and quick actions.

    Access: Superadmins only.
    """
    segments = get_segment_definitions()

    # Calculate totals
    total_incomplete = sum(
        seg['count'] for seg in segments['profile_completion']['segments']
    )
    total_pending = sum(
        seg['count'] for seg in segments['pending_reviews']['segments']
    )
    total_inactive = sum(
        seg['count'] for seg in segments['user_activity']['segments']
    )
    total_reminder_eligible = sum(
        seg['count'] for seg in segments['reminder_eligible']['segments']
    )

    context = {
        'segments': segments,
        'total_incomplete': total_incomplete,
        'total_pending': total_pending,
        'total_inactive': total_inactive,
        'total_reminder_eligible': total_reminder_eligible,
        'title': 'User Segments',
        'site_header': '💕 Crush.lu Administration',
    }

    return render(request, 'admin/crush_lu/user_segments.html', context)


@login_required
@user_passes_test(is_superuser)
def segment_detail(request, segment_key):
    """
    Detailed view of a specific segment with user list.
    Allows CSV export and viewing individual users.

    Access: Superadmins only.
    """
    segments = get_segment_definitions()

    # Find the segment
    target_segment = None
    category_title = None

    for category_key, category in segments.items():
        for segment in category['segments']:
            if segment['key'] == segment_key:
                target_segment = segment
                category_title = category['title']
                break
        if target_segment:
            break

    if not target_segment:
        messages.error(request, f"Segment '{segment_key}' not found.")
        return redirect('user_segments_dashboard')

    # Get the queryset
    queryset = target_segment['queryset']

    # Handle CSV export
    if request.GET.get('export') == 'csv':
        return export_segment_csv(queryset, segment_key, target_segment['name'])

    # Prepare user list based on queryset model
    users = []
    if hasattr(queryset, 'model'):
        model = queryset.model
        if model == CrushProfile:
            users = queryset.select_related('user')[:100]
        elif model == ProfileSubmission:
            users = queryset.select_related('profile__user', 'coach__user')[:100]
        elif model == UserActivity:
            users = queryset.select_related('user')[:100]
        elif model == EmailPreference:
            users = queryset.select_related('user')[:100]
        else:
            users = queryset[:100]

    context = {
        'segment': target_segment,
        'category_title': category_title,
        'users': users,
        'total_count': target_segment['count'],
        'title': f"Segment: {target_segment['name']}",
        'site_header': '💕 Crush.lu Administration',
    }

    return render(request, 'admin/crush_lu/segment_detail.html', context)


def export_segment_csv(queryset, segment_key, segment_name):
    """
    Export segment users to CSV file.
    """
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="segment_{segment_key}_{timezone.now().strftime("%Y%m%d")}.csv"'

    writer = csv.writer(response)
    writer.writerow(['Email', 'First Name', 'Last Name', 'Created At', 'Segment'])

    model = queryset.model

    if model == CrushProfile:
        for profile in queryset.select_related('user'):
            writer.writerow([
                profile.user.email,
                profile.user.first_name,
                profile.user.last_name,
                profile.created_at.strftime('%Y-%m-%d %H:%M'),
                segment_name,
            ])
    elif model == ProfileSubmission:
        for submission in queryset.select_related('profile__user'):
            writer.writerow([
                submission.profile.user.email,
                submission.profile.user.first_name,
                submission.profile.user.last_name,
                submission.submitted_at.strftime('%Y-%m-%d %H:%M') if submission.submitted_at else '',
                segment_name,
            ])
    elif model == UserActivity:
        for activity in queryset.select_related('user'):
            writer.writerow([
                activity.user.email,
                activity.user.first_name,
                activity.user.last_name,
                activity.last_seen.strftime('%Y-%m-%d %H:%M') if activity.last_seen else '',
                segment_name,
            ])
    elif model == EmailPreference:
        for pref in queryset.select_related('user'):
            writer.writerow([
                pref.user.email,
                pref.user.first_name,
                pref.user.last_name,
                pref.created_at.strftime('%Y-%m-%d %H:%M') if hasattr(pref, 'created_at') else '',
                segment_name,
            ])

    return response
