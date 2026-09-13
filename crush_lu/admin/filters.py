"""
Custom admin filters for Crush.lu Coach Panel.

Provides filtering options for coach workflow management.
"""

from django.contrib import admin
from django.db import models
from django.utils import timezone
from datetime import timedelta, date
from django.db.models import Exists, OuterRef, Q

from crush_lu.models.events import SEAT_HOLDING_STATUSES

from .verification_queues import (
    holds_door_seat,
    in_legacy_review_state,
    latest_submission_status,
    never_submitted_profiles,
    resubmitted_after_revision,
)


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
    """Filter profiles by their permanently assigned coach.

    Reads ``CrushProfile.assigned_coach``, the "Assigned Coach" column beside
    it: set at the member's first event, and backfilled from each member's
    latest approving coach (migration 0150). It read the coaches on the
    member's ProfileSubmission rows, which nobody gets since the July 2026
    verification pivot, so "No Coach Assigned" and "Not Submitted for Review"
    listed every member who joined after it. Members who never submitted are
    under Submission History.
    """
    title = 'Coach Assignment'
    parameter_name = 'coach_assignment'

    def lookups(self, request, model_admin):
        return (
            ('has_coach', '👤 Has Coach Assigned'),
            ('no_coach', '❌ No Coach Assigned'),
        )

    def queryset(self, request, queryset):
        if self.value() == 'has_coach':
            return queryset.filter(assigned_coach__isnull=False)
        elif self.value() == 'no_coach':
            return queryset.filter(assigned_coach__isnull=True)
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
    """Filter profiles by age range (5-year bands, only 60+ grouped)."""
    title = 'Age Range'
    parameter_name = 'age_range'

    # value -> (min_age, max_age); max_age None means open-ended (60+)
    RANGES = {
        '18-24': (18, 24),
        '25-29': (25, 29),
        '30-34': (30, 34),
        '35-39': (35, 39),
        '40-44': (40, 44),
        '45-49': (45, 49),
        '50-54': (50, 54),
        '55-59': (55, 59),
        '60+': (60, None),
    }

    def lookups(self, request, model_admin):
        return tuple((value, f'{value} years') for value in self.RANGES)

    def queryset(self, request, queryset):
        if not self.value() or self.value() not in self.RANGES:
            return queryset

        today = date.today()
        min_age, max_age = self.RANGES[self.value()]

        max_birth = date(today.year - min_age, today.month, today.day)
        queryset = queryset.filter(date_of_birth__lte=max_birth)
        if max_age is not None:
            min_birth = date(today.year - max_age - 1, today.month, today.day)
            queryset = queryset.filter(date_of_birth__gt=min_birth)
        return queryset


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
                confirmed_count=Count('eventregistration', filter=models.Q(eventregistration__status__in=SEAT_HOLDING_STATUSES))
            ).filter(confirmed_count__gte=F('max_participants'))
        elif self.value() == 'almost_full':
            return queryset.annotate(
                confirmed_count=Count('eventregistration', filter=models.Q(eventregistration__status__in=SEAT_HOLDING_STATUSES))
            ).filter(
                confirmed_count__lt=F('max_participants'),
                confirmed_count__gte=F('max_participants') - 5
            )
        elif self.value() == 'available':
            return queryset.annotate(
                confirmed_count=Count('eventregistration', filter=models.Q(eventregistration__status__in=SEAT_HOLDING_STATUSES))
            ).filter(confirmed_count__lt=F('max_participants') - 5)
        return queryset


class MutualConnectionFilter(admin.SimpleListFilter):
    """Filter connections by whether the other side asked too.

    "Mutual" means a reciprocal row: the recipient also requested the
    requester, at the same event. That is
    `EventConnectionQuerySet.annotate_is_mutual`, the definition behind the
    changelist's "Mutual" column, so the filter lists exactly the rows the
    column ticks and "One-Way Only" the rest. It filtered a 'mutual' status
    that EventConnection never had: "Mutual" was always empty and "One-Way
    Only" meant "not pending".

    Not ``annotate_is_visible_mutual``, which ignores a crush lead that is not
    ``shared`` yet. That keeps a private crush from members; this staff
    changelist lists every row, crush leads and their notes included, and a
    filter that disagreed with the column beside it would only mislead.
    """
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
            return queryset.annotate_is_mutual().filter(is_mutual_annotated=True)
        elif self.value() == 'pending':
            return queryset.filter(status='pending')
        elif self.value() == 'one_way':
            return queryset.annotate_is_mutual().filter(is_mutual_annotated=False)
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
                filter=models.Q(user__eventregistration__status__in=SEAT_HOLDING_STATUSES)
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


class DoorBookingFilter(admin.SimpleListFilter):
    """Filter profiles by whether an event door can still verify them.

    "Booked" means a seat or waitlist spot on a current or upcoming,
    non-cancelled event: the coach "Unverified profiles" page's "Booked on an
    event" signal. The Action Center splits pending members on it and links
    here, so each tile opens a list of exactly its own count.
    """
    title = 'Event Booking'
    parameter_name = 'door_booking'

    def lookups(self, request, model_admin):
        return (
            ('booked', '🎟️ Booked on a current/upcoming event'),
            ('unbooked', '🚫 Not booked on any current/upcoming event'),
        )

    def queryset(self, request, queryset):
        if self.value() == 'booked':
            return queryset.filter(holds_door_seat(timezone.now()))
        elif self.value() == 'unbooked':
            return queryset.filter(~holds_door_seat(timezone.now()))
        return queryset


# ============================================================================
# PRODUCTION-INFORMED FILTERS (Based on 2026-01-27 Database Analysis)
# ============================================================================


class EmailVerificationStatusFilter(admin.SimpleListFilter):
    """
    Filter profiles by email verification status.

    PRIORITY 1: Production analysis shows 58% unverified emails (92/160 users).
    Critical bottleneck in registration funnel.
    """
    title = 'Email Verification'
    parameter_name = 'email_verification'

    def lookups(self, request, model_admin):
        return (
            ('verified', '✅ Email Verified'),
            ('unverified', '❌ Email Not Verified'),
            ('no_primary', '📧 No Primary Email'),
        )

    def queryset(self, request, queryset):
        # Import here to avoid circular import
        from allauth.account.models import EmailAddress

        if self.value() == 'verified':
            # Users with at least one verified email
            return queryset.filter(
                Exists(
                    EmailAddress.objects.filter(
                        user_id=OuterRef('user_id'),
                        verified=True
                    )
                )
            )
        elif self.value() == 'unverified':
            # Users without any verified email
            return queryset.filter(
                ~Exists(
                    EmailAddress.objects.filter(
                        user_id=OuterRef('user_id'),
                        verified=True
                    )
                )
            )
        elif self.value() == 'no_primary':
            # Users with no email records at all
            return queryset.filter(
                ~Exists(
                    EmailAddress.objects.filter(
                        user_id=OuterRef('user_id')
                    )
                )
            )
        return queryset


class PrivacySettingsFilter(admin.SimpleListFilter):
    """
    Filter profiles by privacy setting combinations.

    PRIORITY 2: Production shows 95.4% hide full names (145/152 profiles).
    Luxembourg market is extremely privacy-conscious - critical for messaging.
    """
    title = 'Privacy Settings'
    parameter_name = 'privacy_settings'

    def lookups(self, request, model_admin):
        return (
            ('high_privacy', '🔒 High Privacy (all flags)'),
            ('name_hidden', '👤 Name Hidden'),
            ('age_hidden', '🎂 Age Hidden'),
            ('default', '🌐 Default (all public)'),
        )

    def queryset(self, request, queryset):
        if self.value() == 'high_privacy':
            # All privacy flags enabled
            return queryset.filter(
                show_full_name=False,
                show_exact_age=False,
            )
        elif self.value() == 'name_hidden':
            return queryset.filter(show_full_name=False)
        elif self.value() == 'age_hidden':
            return queryset.filter(show_exact_age=False)
        elif self.value() == 'default':
            # All public (no privacy flags)
            return queryset.filter(
                show_full_name=True,
                show_exact_age=True,
            )
        return queryset


class ProfileSubmissionDetailFilter(admin.SimpleListFilter):
    """
    Filter profiles by submission workflow status (more granular than existing).

    PRIORITY 3: Production shows 57.2% never submitted (87/152 profiles).
    Identifies users stuck before submission or in revision loops.

    The review options read the member's latest submission, like the profile
    segments (`verification_queues`): a row behind a newer one is history.
    """
    title = 'Submission History'
    parameter_name = 'submission_history'

    def lookups(self, request, model_admin):
        return (
            ('never_submitted', '📝 Never Submitted'),
            ('rejected', '❌ Previously Rejected'),
            ('revision_pending', '🔄 Revision Requested'),
            ('resubmitted', '✅ Resubmitted After Revision'),
        )

    def queryset(self, request, queryset):
        if self.value() == 'never_submitted':
            # Still incomplete, with no submission row. "No row" alone also
            # matched every member who submitted after the July 2026 pivot.
            return never_submitted_profiles(queryset)
        elif self.value() == 'rejected':
            # Latest submission rejected. Verified members stay listed: this
            # is history, and an overturned rejection belongs in it.
            return queryset.alias(
                latest_submission_status=latest_submission_status()
            ).filter(latest_submission_status='rejected')
        elif self.value() == 'revision_pending':
            # Asked to revise and not back yet: the Revision Needed segment.
            # This filtered 'revision_requested', a value the model never had.
            return in_legacy_review_state(queryset, 'revision')
        elif self.value() == 'resubmitted':
            # Back after a revision request. This counted two or more rows,
            # but a resubmission re-queues the same row.
            return resubmitted_after_revision(queryset)
        return queryset


class ConnectionActivityFilter(admin.SimpleListFilter):
    """
    Filter profiles by connection/messaging activity.

    PRIORITY 4: Engagement tracking for approved users.
    Identifies users needing connection encouragement.
    """
    title = 'Connection Activity'
    parameter_name = 'connection_activity'

    def lookups(self, request, model_admin):
        return (
            ('no_connections', '🚫 No Connections'),
            ('pending_sent', '➡️ Pending Sent'),
            ('pending_received', '⬅️ Pending Received'),
            ('coach_approved', '✅ Coach Approved'),
            ('active', '🔥 Active Messaging'),
        )

    def queryset(self, request, queryset):
        # Import here to avoid circular import
        from crush_lu.models import EventConnection, ConnectionMessage

        if self.value() == 'no_connections':
            # No connections at all (sent or received)
            return queryset.filter(
                ~Exists(
                    EventConnection.objects.filter(
                        Q(requester__crushprofile__id=OuterRef('id')) |
                        Q(recipient__crushprofile__id=OuterRef('id'))
                    )
                )
            )
        elif self.value() == 'pending_sent':
            # Has pending connections they initiated
            return queryset.filter(
                Exists(
                    EventConnection.objects.filter(
                        requester__crushprofile__id=OuterRef('id'),
                        status='pending'
                    )
                )
            )
        elif self.value() == 'pending_received':
            # Has pending connections they received
            return queryset.filter(
                Exists(
                    EventConnection.objects.filter(
                        recipient__crushprofile__id=OuterRef('id'),
                        status='pending'
                    )
                )
            )
        elif self.value() == 'coach_approved':
            # Has coach-approved connections ready to share
            return queryset.filter(
                Exists(
                    EventConnection.objects.filter(
                        Q(requester__crushprofile__id=OuterRef('id')) |
                        Q(recipient__crushprofile__id=OuterRef('id')),
                        status='coach_approved'
                    )
                )
            )
        elif self.value() == 'active':
            # Has sent or received messages
            return queryset.filter(
                Exists(
                    ConnectionMessage.objects.filter(
                        Q(connection__requester__crushprofile__id=OuterRef('id')) |
                        Q(connection__recipient__crushprofile__id=OuterRef('id'))
                    )
                )
            )
        return queryset
