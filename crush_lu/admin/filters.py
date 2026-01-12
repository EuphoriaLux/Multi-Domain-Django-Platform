"""
Custom admin filters for Crush.lu Coach Panel.

Provides filtering options for coach workflow management.
"""

from django.contrib import admin
from django.db import models
from django.utils import timezone
from datetime import timedelta, date


class ReviewTimeFilter(admin.SimpleListFilter):
    """Filter submissions by how long they've been pending"""
    title = 'Pending Time'
    parameter_name = 'pending_time'

    def lookups(self, request, model_admin):
        return (
            ('24h', '🚨 Pending > 24 hours'),
            ('3d', '⚠️ Pending > 3 days'),
            ('7d', '🔴 Pending > 7 days'),
        )

    def queryset(self, request, queryset):
        now = timezone.now()
        if self.value() == '24h':
            cutoff = now - timedelta(hours=24)
            return queryset.filter(status='pending', submitted_at__lt=cutoff)
        elif self.value() == '3d':
            cutoff = now - timedelta(days=3)
            return queryset.filter(status='pending', submitted_at__lt=cutoff)
        elif self.value() == '7d':
            cutoff = now - timedelta(days=7)
            return queryset.filter(status='pending', submitted_at__lt=cutoff)
        return queryset


class CoachAssignmentFilter(admin.SimpleListFilter):
    """Filter profiles by coach assignment status"""
    title = 'Coach Assignment'
    parameter_name = 'coach_assignment'

    def lookups(self, request, model_admin):
        return (
            ('has_coach', '👤 Has Coach Assigned'),
            ('no_coach', '❌ No Coach Assigned'),
            ('not_submitted', '📝 Not Submitted for Review'),
        )

    def queryset(self, request, queryset):
        if self.value() == 'has_coach':
            return queryset.filter(
                profilesubmission__coach__isnull=False
            ).distinct()
        elif self.value() == 'no_coach':
            return queryset.filter(
                profilesubmission__coach__isnull=True
            ).distinct()
        elif self.value() == 'not_submitted':
            return queryset.filter(
                profilesubmission__isnull=True
            )
        return queryset


class SubmissionWorkflowFilter(admin.SimpleListFilter):
    """Filter submissions by workflow stage with visual indicators"""
    title = 'Workflow Stage'
    parameter_name = 'workflow'

    def lookups(self, request, model_admin):
        return (
            ('urgent', '🚨 Needs Attention (Pending >24h)'),
            ('new', '🆕 New (Pending <24h)'),
            ('awaiting_call', '📞 Awaiting Screening Call'),
            ('ready_approve', '✅ Ready to Approve (Call Done)'),
            ('completed', '✔️ Completed'),
        )

    def queryset(self, request, queryset):
        now = timezone.now()
        cutoff_24h = now - timedelta(hours=24)

        if self.value() == 'urgent':
            # Pending and submitted > 24h ago
            return queryset.filter(
                status='pending',
                submitted_at__lt=cutoff_24h
            )
        elif self.value() == 'new':
            # Pending and submitted < 24h ago
            return queryset.filter(
                status='pending',
                submitted_at__gte=cutoff_24h
            )
        elif self.value() == 'awaiting_call':
            # Has coach, pending status, call not done
            return queryset.filter(
                status='pending',
                coach__isnull=False,
                review_call_completed=False
            )
        elif self.value() == 'ready_approve':
            # Has coach, pending status, call completed
            return queryset.filter(
                status='pending',
                coach__isnull=False,
                review_call_completed=True
            )
        elif self.value() == 'completed':
            # Approved or rejected
            return queryset.filter(
                status__in=['approved', 'rejected']
            )
        return queryset


class PhoneVerificationFilter(admin.SimpleListFilter):
    """Filter profiles by phone verification status"""
    title = 'Phone Verification'
    parameter_name = 'phone_status'

    def lookups(self, request, model_admin):
        return (
            ('verified', '✅ Phone Verified'),
            ('unverified', '❌ Not Verified (has number)'),
            ('no_phone', '📵 No Phone Number'),
        )

    def queryset(self, request, queryset):
        if self.value() == 'verified':
            return queryset.filter(phone_verified=True)
        elif self.value() == 'unverified':
            return queryset.filter(
                phone_verified=False,
                phone_number__isnull=False
            ).exclude(phone_number='')
        elif self.value() == 'no_phone':
            from django.db.models import Q
            return queryset.filter(
                Q(phone_number__isnull=True) | Q(phone_number='')
            )
        return queryset


class AgeRangeFilter(admin.SimpleListFilter):
    """Filter profiles by age range"""
    title = 'Age Range'
    parameter_name = 'age_range'

    def lookups(self, request, model_admin):
        return (
            ('18-24', '18-24 years'),
            ('25-29', '25-29 years'),
            ('30-34', '30-34 years'),
            ('35-39', '35-39 years'),
            ('40-49', '40-49 years'),
            ('50+', '50+ years'),
        )

    def queryset(self, request, queryset):
        if not self.value():
            return queryset

        today = date.today()

        if self.value() == '18-24':
            max_birth = date(today.year - 18, today.month, today.day)
            min_birth = date(today.year - 25, today.month, today.day)
        elif self.value() == '25-29':
            max_birth = date(today.year - 25, today.month, today.day)
            min_birth = date(today.year - 30, today.month, today.day)
        elif self.value() == '30-34':
            max_birth = date(today.year - 30, today.month, today.day)
            min_birth = date(today.year - 35, today.month, today.day)
        elif self.value() == '35-39':
            max_birth = date(today.year - 35, today.month, today.day)
            min_birth = date(today.year - 40, today.month, today.day)
        elif self.value() == '40-49':
            max_birth = date(today.year - 40, today.month, today.day)
            min_birth = date(today.year - 50, today.month, today.day)
        elif self.value() == '50+':
            max_birth = date(today.year - 50, today.month, today.day)
            min_birth = date(today.year - 100, today.month, today.day)
        else:
            return queryset

        return queryset.filter(
            date_of_birth__gt=min_birth,
            date_of_birth__lte=max_birth
        )


class LastLoginFilter(admin.SimpleListFilter):
    """Filter profiles by user's last login activity"""
    title = 'Last Login'
    parameter_name = 'last_login'

    def lookups(self, request, model_admin):
        return (
            ('today', '🟢 Today'),
            ('week', '🟡 Last 7 days'),
            ('month', '🟠 Last 30 days'),
            ('inactive', '🔴 Over 30 days / Never'),
        )

    def queryset(self, request, queryset):
        now = timezone.now()
        if self.value() == 'today':
            return queryset.filter(
                user__last_login__date=now.date()
            )
        elif self.value() == 'week':
            cutoff = now - timedelta(days=7)
            return queryset.filter(user__last_login__gte=cutoff)
        elif self.value() == 'month':
            cutoff = now - timedelta(days=30)
            return queryset.filter(user__last_login__gte=cutoff)
        elif self.value() == 'inactive':
            from django.db.models import Q
            cutoff = now - timedelta(days=30)
            return queryset.filter(
                Q(user__last_login__lt=cutoff) | Q(user__last_login__isnull=True)
            )
        return queryset


class EventCapacityFilter(admin.SimpleListFilter):
    """Filter events by capacity status"""
    title = 'Capacity Status'
    parameter_name = 'capacity'

    def lookups(self, request, model_admin):
        return (
            ('available', '🟢 Spots Available'),
            ('almost_full', '🟡 Almost Full (<5 spots)'),
            ('full', '🔴 Full'),
        )

    def queryset(self, request, queryset):
        from django.db.models import Count, F

        if self.value() == 'full':
            return queryset.annotate(
                confirmed_count=Count('eventregistration', filter=models.Q(eventregistration__status='confirmed'))
            ).filter(confirmed_count__gte=F('max_participants'))
        elif self.value() == 'almost_full':
            return queryset.annotate(
                confirmed_count=Count('eventregistration', filter=models.Q(eventregistration__status='confirmed'))
            ).filter(
                confirmed_count__lt=F('max_participants'),
                confirmed_count__gte=F('max_participants') - 5
            )
        elif self.value() == 'available':
            return queryset.annotate(
                confirmed_count=Count('eventregistration', filter=models.Q(eventregistration__status='confirmed'))
            ).filter(confirmed_count__lt=F('max_participants') - 5)
        return queryset


class MutualConnectionFilter(admin.SimpleListFilter):
    """Filter connections by mutual status"""
    title = 'Connection Type'
    parameter_name = 'connection_type'

    def lookups(self, request, model_admin):
        return (
            ('mutual', '💕 Mutual Connections'),
            ('pending', '⏳ Pending Response'),
            ('one_way', '➡️ One-Way Only'),
        )

    def queryset(self, request, queryset):
        if self.value() == 'mutual':
            return queryset.filter(status='mutual')
        elif self.value() == 'pending':
            return queryset.filter(status='pending')
        elif self.value() == 'one_way':
            return queryset.exclude(status__in=['mutual', 'pending'])
        return queryset


class HasMessagesFilter(admin.SimpleListFilter):
    """Filter connections by whether they have messages"""
    title = 'Has Messages'
    parameter_name = 'has_messages'

    def lookups(self, request, model_admin):
        return (
            ('yes', '💬 Has Messages'),
            ('no', '🔇 No Messages'),
        )

    def queryset(self, request, queryset):
        from django.db.models import Count

        if self.value() == 'yes':
            return queryset.annotate(
                message_count=Count('messages')
            ).filter(message_count__gt=0)
        elif self.value() == 'no':
            return queryset.annotate(
                message_count=Count('messages')
            ).filter(message_count=0)
        return queryset


# ============================================================================
# NEW QUICK WIN FILTERS (Coach Workflow Improvements)
# ============================================================================


class DaysSinceSignupFilter(admin.SimpleListFilter):
    """Filter profiles by account age (time since creation)"""
    title = 'Account Age'
    parameter_name = 'signup_age'

    def lookups(self, request, model_admin):
        return (
            ('new', '🆕 New (< 7 days)'),
            ('recent', '📅 Recent (7-30 days)'),
            ('established', '📆 Established (> 30 days)'),
        )

    def queryset(self, request, queryset):
        now = timezone.now()
        if self.value() == 'new':
            cutoff = now - timedelta(days=7)
            return queryset.filter(created_at__gte=cutoff)
        elif self.value() == 'recent':
            cutoff_start = now - timedelta(days=30)
            cutoff_end = now - timedelta(days=7)
            return queryset.filter(
                created_at__gte=cutoff_start,
                created_at__lt=cutoff_end
            )
        elif self.value() == 'established':
            cutoff = now - timedelta(days=30)
            return queryset.filter(created_at__lt=cutoff)
        return queryset


class DaysPendingApprovalFilter(admin.SimpleListFilter):
    """Filter submissions by how long they've been waiting for approval"""
    title = 'Days Pending'
    parameter_name = 'days_pending'

    def lookups(self, request, model_admin):
        return (
            ('fresh', '🟢 Fresh (< 1 day)'),
            ('waiting', '🟡 Waiting (1-3 days)'),
            ('overdue', '🟠 Overdue (3-7 days)'),
            ('critical', '🔴 Critical (> 7 days)'),
        )

    def queryset(self, request, queryset):
        now = timezone.now()
        # Only filter pending submissions
        pending_qs = queryset.filter(status='pending')

        if self.value() == 'fresh':
            cutoff = now - timedelta(days=1)
            return pending_qs.filter(submitted_at__gte=cutoff)
        elif self.value() == 'waiting':
            cutoff_start = now - timedelta(days=3)
            cutoff_end = now - timedelta(days=1)
            return pending_qs.filter(
                submitted_at__gte=cutoff_start,
                submitted_at__lt=cutoff_end
            )
        elif self.value() == 'overdue':
            cutoff_start = now - timedelta(days=7)
            cutoff_end = now - timedelta(days=3)
            return pending_qs.filter(
                submitted_at__gte=cutoff_start,
                submitted_at__lt=cutoff_end
            )
        elif self.value() == 'critical':
            cutoff = now - timedelta(days=7)
            return pending_qs.filter(submitted_at__lt=cutoff)
        return queryset


class ProfileCompletenessFilter(admin.SimpleListFilter):
    """Filter profiles by completeness (photos, bio, interests)"""
    title = 'Profile Completeness'
    parameter_name = 'completeness'

    def lookups(self, request, model_admin):
        return (
            ('complete', '✅ Complete'),
            ('missing_photos', '📷 Missing Photos'),
            ('missing_bio', '📝 Missing Bio'),
            ('missing_interests', '❤️ Missing Interests'),
            ('incomplete', '⚠️ Multiple Missing'),
        )

    def queryset(self, request, queryset):
        from django.db.models import Q

        if self.value() == 'complete':
            # Has at least one photo, has bio, has interests
            return queryset.filter(
                photo_1__isnull=False
            ).exclude(
                photo_1=''
            ).exclude(
                Q(bio__isnull=True) | Q(bio='')
            ).exclude(
                Q(interests__isnull=True) | Q(interests='')
            )
        elif self.value() == 'missing_photos':
            # No photos at all
            return queryset.filter(
                Q(photo_1__isnull=True) | Q(photo_1='')
            )
        elif self.value() == 'missing_bio':
            return queryset.filter(
                Q(bio__isnull=True) | Q(bio='')
            )
        elif self.value() == 'missing_interests':
            return queryset.filter(
                Q(interests__isnull=True) | Q(interests='')
            )
        elif self.value() == 'incomplete':
            # Missing 2+ of: photo, bio, interests
            no_photo = Q(photo_1__isnull=True) | Q(photo_1='')
            no_bio = Q(bio__isnull=True) | Q(bio='')
            no_interests = Q(interests__isnull=True) | Q(interests='')
            # At least 2 conditions true
            return queryset.filter(
                (no_photo & no_bio) |
                (no_photo & no_interests) |
                (no_bio & no_interests)
            )
        return queryset


class EventParticipationFilter(admin.SimpleListFilter):
    """Filter profiles by event attendance history"""
    title = 'Event Participation'
    parameter_name = 'event_participation'

    def lookups(self, request, model_admin):
        return (
            ('none', '🚫 No Events'),
            ('one', '1️⃣ One Event'),
            ('multiple', '🌟 Multiple Events (2+)'),
            ('active', '🔥 Very Active (5+)'),
        )

    def queryset(self, request, queryset):
        from django.db.models import Count

        annotated = queryset.annotate(
            event_count=Count(
                'user__eventregistration',
                filter=models.Q(user__eventregistration__status='confirmed')
            )
        )

        if self.value() == 'none':
            return annotated.filter(event_count=0)
        elif self.value() == 'one':
            return annotated.filter(event_count=1)
        elif self.value() == 'multiple':
            return annotated.filter(event_count__gte=2)
        elif self.value() == 'active':
            return annotated.filter(event_count__gte=5)
        return queryset
