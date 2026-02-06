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
from django.db.models import Count, Q, F, Exists, OuterRef, Case, When, Value, CharField
from django.utils import timezone
from django.contrib import messages
from django.http import HttpResponse
from datetime import timedelta, date
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
# DEMOGRAPHIC STATISTICS
# ============================================================================

def get_demographic_stats():
    """
    Return demographic statistics for active CrushProfiles.
    Includes gender, looking-for, age range, language, and location distributions.
    """
    active_profiles = CrushProfile.objects.filter(is_active=True)
    approved_profiles = active_profiles.filter(is_approved=True)

    total_active = active_profiles.count()
    total_approved = approved_profiles.count()

    # Gender distribution
    gender_labels = dict(CrushProfile.GENDER_CHOICES)
    gender_all = list(
        active_profiles.exclude(gender='')
        .values('gender')
        .annotate(count=Count('id'))
        .order_by('-count')
    )
    gender_approved = list(
        approved_profiles.exclude(gender='')
        .values('gender')
        .annotate(count=Count('id'))
        .order_by('-count')
    )
    gender_all_total = sum(g['count'] for g in gender_all)
    gender_approved_total = sum(g['count'] for g in gender_approved)

    for g in gender_all:
        g['label'] = str(gender_labels.get(g['gender'], g['gender']))
        g['pct'] = round(g['count'] / gender_all_total * 100, 1) if gender_all_total else 0

    for g in gender_approved:
        g['label'] = str(gender_labels.get(g['gender'], g['gender']))
        g['pct'] = round(g['count'] / gender_approved_total * 100, 1) if gender_approved_total else 0

    # Looking-for distribution
    looking_for_labels = dict(CrushProfile.LOOKING_FOR_CHOICES)
    looking_for_dist = list(
        active_profiles.exclude(looking_for='')
        .values('looking_for')
        .annotate(count=Count('id'))
        .order_by('-count')
    )
    lf_total = sum(lf['count'] for lf in looking_for_dist)

    for lf in looking_for_dist:
        lf['label'] = str(looking_for_labels.get(lf['looking_for'], lf['looking_for']))
        lf['pct'] = round(lf['count'] / lf_total * 100, 1) if lf_total else 0

    # Age range distribution
    today = date.today()
    age_ranges = [
        ('18-24', 18, 24),
        ('25-29', 25, 29),
        ('30-34', 30, 34),
        ('35-39', 35, 39),
        ('40+', 40, None),
    ]
    age_dist = []
    profiles_with_dob = active_profiles.exclude(date_of_birth__isnull=True)
    age_total = profiles_with_dob.count()

    for label, min_age, max_age in age_ranges:
        # Born before = older, born after = younger
        max_born = date(today.year - min_age, today.month, today.day)
        if max_age is not None:
            min_born = date(today.year - max_age - 1, today.month, today.day)
            count = profiles_with_dob.filter(
                date_of_birth__gt=min_born,
                date_of_birth__lte=max_born,
            ).count()
        else:
            count = profiles_with_dob.filter(
                date_of_birth__lte=max_born,
            ).count()
        pct = round(count / age_total * 100, 1) if age_total else 0
        age_dist.append({'label': label, 'count': count, 'pct': pct})

    # Language distribution
    language_flags = {'en': '🇬🇧', 'de': '🇩🇪', 'fr': '🇫🇷'}
    lang_dist = list(
        active_profiles.exclude(preferred_language='')
        .values('preferred_language')
        .annotate(count=Count('id'))
        .order_by('-count')
    )
    lang_total = sum(l['count'] for l in lang_dist)
    for l in lang_dist:
        l['label'] = language_flags.get(l['preferred_language'], '') + ' ' + l['preferred_language'].upper()
        l['pct'] = round(l['count'] / lang_total * 100, 1) if lang_total else 0

    # Location distribution (top 10 cities)
    location_dist = list(
        active_profiles.exclude(location='')
        .values('location')
        .annotate(count=Count('id'))
        .order_by('-count')[:10]
    )
    loc_total = active_profiles.exclude(location='').count()
    for loc in location_dist:
        loc['label'] = loc['location']
        loc['pct'] = round(loc['count'] / loc_total * 100, 1) if loc_total else 0

    return {
        'total_active': total_active,
        'total_approved': total_approved,
        'gender_all': gender_all,
        'gender_approved': gender_approved,
        'looking_for': looking_for_dist,
        'age_ranges': age_dist,
        'languages': lang_dist,
        'locations': location_dist,
    }


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
        user__push_subscriptions__isnull=False
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

    # Gender segments
    gender_male = CrushProfile.objects.filter(gender='M', is_active=True)
    gender_female = CrushProfile.objects.filter(gender='F', is_active=True)
    gender_nonbinary = CrushProfile.objects.filter(gender='NB', is_active=True)
    gender_other = CrushProfile.objects.filter(gender='O', is_active=True)
    gender_prefer_not = CrushProfile.objects.filter(gender='P', is_active=True)

    # Looking-for segments
    lf_friends = CrushProfile.objects.filter(looking_for='friends', is_active=True)
    lf_dating = CrushProfile.objects.filter(looking_for='dating', is_active=True)
    lf_both = CrushProfile.objects.filter(looking_for='both', is_active=True)
    lf_networking = CrushProfile.objects.filter(looking_for='networking', is_active=True)

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
        'demographics_gender': {
            'title': 'Demographics: Gender',
            'icon': '⚧',
            'segments': [
                {
                    'name': 'Male',
                    'key': 'gender_male',
                    'description': 'Active profiles identifying as male',
                    'queryset': gender_male,
                    'count': gender_male.count(),
                    'color': 'blue',
                },
                {
                    'name': 'Female',
                    'key': 'gender_female',
                    'description': 'Active profiles identifying as female',
                    'queryset': gender_female,
                    'count': gender_female.count(),
                    'color': 'pink',
                },
                {
                    'name': 'Non-Binary',
                    'key': 'gender_nonbinary',
                    'description': 'Active profiles identifying as non-binary',
                    'queryset': gender_nonbinary,
                    'count': gender_nonbinary.count(),
                    'color': 'purple',
                },
                {
                    'name': 'Other',
                    'key': 'gender_other',
                    'description': 'Active profiles with gender set to other',
                    'queryset': gender_other,
                    'count': gender_other.count(),
                    'color': 'green',
                },
                {
                    'name': 'Prefer Not to Say',
                    'key': 'gender_prefer_not',
                    'description': 'Active profiles who prefer not to disclose gender',
                    'queryset': gender_prefer_not,
                    'count': gender_prefer_not.count(),
                    'color': 'gray',
                },
            ]
        },
        'demographics_looking_for': {
            'title': 'Demographics: Looking For',
            'icon': '💫',
            'segments': [
                {
                    'name': 'New Friends',
                    'key': 'lf_friends',
                    'description': 'Active profiles looking for new friends',
                    'queryset': lf_friends,
                    'count': lf_friends.count(),
                    'color': 'green',
                },
                {
                    'name': 'Dating',
                    'key': 'lf_dating',
                    'description': 'Active profiles looking for dating',
                    'queryset': lf_dating,
                    'count': lf_dating.count(),
                    'color': 'red',
                },
                {
                    'name': 'Both (Friends & Dating)',
                    'key': 'lf_both',
                    'description': 'Active profiles open to both friends and dating',
                    'queryset': lf_both,
                    'count': lf_both.count(),
                    'color': 'purple',
                },
                {
                    'name': 'Social Networking',
                    'key': 'lf_networking',
                    'description': 'Active profiles interested in social networking',
                    'queryset': lf_networking,
                    'count': lf_networking.count(),
                    'color': 'blue',
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
    demographics = get_demographic_stats()

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
        'demographics': demographics,
        'total_profiles': demographics['total_active'],
        'total_approved': demographics['total_approved'],
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
    is_profile_segment = False
    if hasattr(queryset, 'model'):
        model = queryset.model
        if model == CrushProfile:
            is_profile_segment = True
            users = queryset.select_related('user').annotate(
                event_count=Count('user__eventregistration', distinct=True),
                sent_connections=Count('user__connection_requests_sent', distinct=True),
                received_connections=Count('user__connection_requests_received', distinct=True),
            )[:100]
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
        'is_profile_segment': is_profile_segment,
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
    model = queryset.model

    if model == CrushProfile:
        writer.writerow([
            'Email', 'First Name', 'Last Name', 'Gender', 'Age', 'Location',
            'Phone Verified', 'Looking For', 'Language', 'Is Approved',
            'Event Count', 'Connection Count', 'Created At', 'Segment',
        ])
        gender_labels = dict(CrushProfile.GENDER_CHOICES)
        lf_labels = dict(CrushProfile.LOOKING_FOR_CHOICES)
        profiles = queryset.select_related('user').annotate(
            event_count=Count('user__eventregistration', distinct=True),
            sent_connections=Count('user__connection_requests_sent', distinct=True),
            received_connections=Count('user__connection_requests_received', distinct=True),
        )
        for profile in profiles:
            try:
                age = profile.age
            except Exception:
                age = ''
            writer.writerow([
                profile.user.email,
                profile.user.first_name,
                profile.user.last_name,
                str(gender_labels.get(profile.gender, profile.gender)),
                age if age else '',
                profile.location,
                'Yes' if profile.phone_verified else 'No',
                str(lf_labels.get(profile.looking_for, profile.looking_for)),
                profile.preferred_language,
                'Yes' if profile.is_approved else 'No',
                profile.event_count,
                profile.sent_connections + profile.received_connections,
                profile.created_at.strftime('%Y-%m-%d %H:%M'),
                segment_name,
            ])
    elif model == ProfileSubmission:
        writer.writerow(['Email', 'First Name', 'Last Name', 'Created At', 'Segment'])
        for submission in queryset.select_related('profile__user'):
            writer.writerow([
                submission.profile.user.email,
                submission.profile.user.first_name,
                submission.profile.user.last_name,
                submission.submitted_at.strftime('%Y-%m-%d %H:%M') if submission.submitted_at else '',
                segment_name,
            ])
    elif model == UserActivity:
        writer.writerow(['Email', 'First Name', 'Last Name', 'Created At', 'Segment'])
        for activity in queryset.select_related('user'):
            writer.writerow([
                activity.user.email,
                activity.user.first_name,
                activity.user.last_name,
                activity.last_seen.strftime('%Y-%m-%d %H:%M') if activity.last_seen else '',
                segment_name,
            ])
    elif model == EmailPreference:
        writer.writerow(['Email', 'First Name', 'Last Name', 'Created At', 'Segment'])
        for pref in queryset.select_related('user'):
            writer.writerow([
                pref.user.email,
                pref.user.first_name,
                pref.user.last_name,
                pref.created_at.strftime('%Y-%m-%d %H:%M') if hasattr(pref, 'created_at') else '',
                segment_name,
            ])

    return response
