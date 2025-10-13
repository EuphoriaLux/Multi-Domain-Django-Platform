from django.contrib import admin
from django.contrib import messages as django_messages
from django.db import transaction
from .models import (
    CrushCoach, CrushProfile, ProfileSubmission,
    CoachSession, MeetupEvent, EventRegistration,
    EventConnection, ConnectionMessage,
    GlobalActivityOption, EventActivityOption, EventActivityVote, EventVotingSession,
    PresentationQueue, PresentationRating, SpeedDatingPair
)


@admin.register(CrushCoach)
class CrushCoachAdmin(admin.ModelAdmin):
    list_display = ('user', 'get_email', 'specializations', 'is_active', 'max_active_reviews', 'created_at', 'has_dating_profile')
    list_filter = ('is_active', 'created_at')
    search_fields = ('user__username', 'user__email', 'user__first_name', 'user__last_name')
    readonly_fields = ('created_at',)
    actions = ['deactivate_coach_allow_dating', 'deactivate_coaches', 'activate_coaches']

    def get_email(self, obj):
        """Display coach's email address"""
        return obj.user.email
    get_email.short_description = 'Email'
    get_email.admin_order_field = 'user__email'  # Allow sorting by email

    def has_dating_profile(self, obj):
        """Check if this coach also has a dating profile"""
        return hasattr(obj.user, 'crushprofile')
    has_dating_profile.boolean = True
    has_dating_profile.short_description = 'Has Dating Profile'

    @admin.action(description='Deactivate coach role (allows them to date)')
    def deactivate_coach_allow_dating(self, request, queryset):
        """Deactivate coach so they can create/use dating profile"""
        deactivated = queryset.update(is_active=False)
        django_messages.success(
            request,
            f"Deactivated {deactivated} coach(es). They can now create/use dating profiles."
        )

    @admin.action(description='Deactivate selected coaches')
    def deactivate_coaches(self, request, queryset):
        updated = queryset.update(is_active=False)
        django_messages.success(request, f"Deactivated {updated} coach(es)")

    @admin.action(description='Activate selected coaches')
    def activate_coaches(self, request, queryset):
        updated = queryset.update(is_active=True)
        django_messages.success(request, f"Activated {updated} coach(es)")


@admin.register(CrushProfile)
class CrushProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'get_email', 'age', 'gender', 'location', 'screening_call_completed', 'is_approved', 'is_active', 'created_at', 'is_coach')
    list_filter = ('is_approved', 'is_active', 'screening_call_completed', 'gender', 'created_at')
    search_fields = ('user__username', 'user__email', 'location', 'bio')
    readonly_fields = ('created_at', 'updated_at', 'approved_at')
    actions = ['promote_to_coach', 'approve_profiles', 'deactivate_profiles']
    fieldsets = (
        ('User Information', {
            'fields': ('user', 'date_of_birth', 'gender', 'phone_number', 'location')
        }),
        ('Profile Content', {
            'fields': ('bio', 'interests', 'looking_for')
        }),
        ('Photos', {
            'fields': ('photo_1', 'photo_2', 'photo_3')
        }),
        ('Privacy Settings', {
            'fields': ('show_full_name', 'show_exact_age', 'blur_photos')
        }),
        ('Screening Call', {
            'fields': ('needs_screening_call', 'screening_call_completed', 'screening_call_scheduled', 'screening_notes'),
            'classes': ('collapse',),
            'description': 'Coach screening call tracking (after Step 1)'
        }),
        ('Profile Completion', {
            'fields': ('completion_status',),
            'classes': ('collapse',),
            'description': 'Track which step of profile creation user completed'
        }),
        ('Status', {
            'fields': ('is_approved', 'is_active', 'approved_at')
        }),
        ('Metadata', {
            'fields': ('created_at', 'updated_at')
        }),
    )

    def get_email(self, obj):
        """Display user's email address"""
        return obj.user.email
    get_email.short_description = 'Email'
    get_email.admin_order_field = 'user__email'  # Allow sorting by email

    def is_coach(self, obj):
        """Check if this user is also a coach"""
        return hasattr(obj.user, 'crushcoach')
    is_coach.boolean = True
    is_coach.short_description = 'Is Coach'

    @admin.action(description='Promote selected profiles to Crush Coach role')
    def promote_to_coach(self, request, queryset):
        """Convert dating profiles to coaches"""
        promoted_count = 0
        errors = []

        for profile in queryset:
            # Check if user is already a coach
            if hasattr(profile.user, 'crushcoach'):
                errors.append(f"{profile.user.username} is already a coach")
                continue

            try:
                with transaction.atomic():
                    # Create coach profile
                    CrushCoach.objects.create(
                        user=profile.user,
                        bio=profile.bio,  # Transfer bio from dating profile
                        is_active=True,
                        max_active_reviews=10
                    )

                    # Optionally deactivate dating profile
                    # (Comment this out if you want to allow dual roles)
                    profile.is_active = False
                    profile.save()

                    promoted_count += 1

            except Exception as e:
                errors.append(f"{profile.user.username}: {str(e)}")

        if promoted_count > 0:
            django_messages.success(
                request,
                f"Successfully promoted {promoted_count} profile(s) to Crush Coach. "
                f"Their dating profiles have been deactivated."
            )

        for error in errors:
            django_messages.error(request, error)

    @admin.action(description='Approve selected profiles')
    def approve_profiles(self, request, queryset):
        from django.utils import timezone
        updated = queryset.update(is_approved=True, approved_at=timezone.now())
        django_messages.success(request, f"Approved {updated} profile(s)")

    @admin.action(description='Deactivate selected profiles')
    def deactivate_profiles(self, request, queryset):
        updated = queryset.update(is_active=False)
        django_messages.success(request, f"Deactivated {updated} profile(s)")


@admin.register(ProfileSubmission)
class ProfileSubmissionAdmin(admin.ModelAdmin):
    list_display = ('profile', 'coach', 'status', 'review_call_completed', 'submitted_at', 'reviewed_at')
    list_filter = ('status', 'review_call_completed', 'submitted_at', 'reviewed_at')
    search_fields = ('profile__user__username', 'coach__user__username')
    readonly_fields = ('submitted_at',)
    fieldsets = (
        ('Submission Details', {
            'fields': ('profile', 'coach', 'status')
        }),
        ('Screening Call (During Review)', {
            'fields': ('review_call_completed', 'review_call_date', 'review_call_notes'),
            'description': 'Coach must complete screening call before approving profile'
        }),
        ('Review', {
            'fields': ('coach_notes', 'feedback_to_user')
        }),
        ('Timestamps', {
            'fields': ('submitted_at', 'reviewed_at')
        }),
    )


@admin.register(CoachSession)
class CoachSessionAdmin(admin.ModelAdmin):
    list_display = ('coach', 'user', 'session_type', 'scheduled_at', 'completed_at', 'created_at')
    list_filter = ('session_type', 'scheduled_at', 'completed_at')
    search_fields = ('coach__user__username', 'user__username', 'notes')
    readonly_fields = ('created_at',)


# Inline admin for Event Registrations
class EventRegistrationInline(admin.TabularInline):
    model = EventRegistration
    extra = 0
    fields = ('user', 'status', 'payment_confirmed', 'registered_at')
    readonly_fields = ('registered_at',)
    can_delete = False
    show_change_link = True


# Inline admin for Voting Session
class EventVotingSessionInline(admin.StackedInline):
    model = EventVotingSession
    extra = 0
    fields = (
        ('is_active', 'total_votes'),
        ('voting_start_time', 'voting_end_time'),
        ('winning_presentation_style', 'winning_speed_dating_twist')
    )
    readonly_fields = ('total_votes',)
    can_delete = False


# Inline admin for Presentation Queue
class PresentationQueueInline(admin.TabularInline):
    model = PresentationQueue
    extra = 0
    fields = ('user', 'presentation_order', 'status', 'started_at', 'completed_at', 'duration_seconds')
    readonly_fields = ('duration_seconds', 'started_at', 'completed_at')
    can_delete = False
    ordering = ['presentation_order']
    show_change_link = True


# Inline admin for Speed Dating Pairs
class SpeedDatingPairInline(admin.TabularInline):
    model = SpeedDatingPair
    extra = 0
    fields = ('round_number', 'user1', 'user2', 'mutual_rating_score', 'is_top_match', 'duration_minutes')
    readonly_fields = ('mutual_rating_score', 'duration_minutes')
    can_delete = False
    ordering = ['round_number']
    show_change_link = True


@admin.register(MeetupEvent)
class MeetupEventAdmin(admin.ModelAdmin):
    list_display = (
        'title', 'event_type', 'date_time', 'location',
        'get_registration_count', 'get_confirmed_count', 'get_waitlist_count',
        'max_participants', 'get_spots_remaining',
        'get_voting_status', 'is_published', 'is_cancelled'
    )
    list_filter = ('event_type', 'is_published', 'is_cancelled', 'date_time')
    search_fields = ('title', 'description', 'location', 'address')
    readonly_fields = (
        'created_at', 'updated_at',
        'get_registration_count', 'get_confirmed_count', 'get_waitlist_count',
        'get_spots_remaining', 'get_revenue',
        'get_voting_status', 'get_presentation_status', 'get_speed_dating_status'
    )
    inlines = [EventRegistrationInline, EventVotingSessionInline, PresentationQueueInline, SpeedDatingPairInline]
    actions = ['publish_events', 'unpublish_events', 'cancel_events']

    fieldsets = (
        ('Event Information', {
            'fields': ('title', 'description', 'event_type')
        }),
        ('Location & Timing', {
            'fields': ('location', 'address', 'date_time', 'duration_minutes')
        }),
        ('Capacity & Requirements', {
            'fields': ('max_participants', 'min_age', 'max_age')
        }),
        ('Registration', {
            'fields': ('registration_deadline', 'registration_fee')
        }),
        ('📊 Event Statistics', {
            'fields': (
                'get_registration_count', 'get_confirmed_count', 'get_waitlist_count',
                'get_spots_remaining', 'get_revenue'
            ),
            'classes': ('collapse',),
            'description': 'Real-time event statistics and capacity information'
        }),
        ('🎯 Phase Status Overview', {
            'fields': ('get_voting_status', 'get_presentation_status', 'get_speed_dating_status'),
            'classes': ('collapse',),
            'description': 'Track progress through the 3-phase event system'
        }),
        ('Status', {
            'fields': ('is_published', 'is_cancelled')
        }),
        ('Metadata', {
            'fields': ('created_at', 'updated_at')
        }),
    )

    # Custom display methods
    def get_registration_count(self, obj):
        """Total registrations (all statuses)"""
        return obj.registrations.count()
    get_registration_count.short_description = '📝 Total Registrations'

    def get_confirmed_count(self, obj):
        """Confirmed registrations only"""
        return obj.get_confirmed_count()
    get_confirmed_count.short_description = '✅ Confirmed'

    def get_waitlist_count(self, obj):
        """Waitlisted registrations"""
        return obj.get_waitlist_count()
    get_waitlist_count.short_description = '⏳ Waitlist'

    def get_spots_remaining(self, obj):
        """Calculate remaining spots"""
        remaining = obj.spots_remaining
        if remaining == 0:
            return f"🔴 FULL (0/{obj.max_participants})"
        elif remaining <= 5:
            return f"🟡 {remaining}/{obj.max_participants}"
        else:
            return f"🟢 {remaining}/{obj.max_participants}"
    get_spots_remaining.short_description = 'Spots Available'

    def get_revenue(self, obj):
        """Calculate total revenue from confirmed payments"""
        confirmed = obj.registrations.filter(payment_confirmed=True).count()
        revenue = confirmed * obj.registration_fee
        return f"€{revenue:.2f} ({confirmed} paid)"
    get_revenue.short_description = '💰 Revenue'

    def get_voting_status(self, obj):
        """Display Phase 1 voting status"""
        try:
            voting_session = obj.voting_session
            if not voting_session.is_active and voting_session.voting_end_time:
                # Voting ended
                return f"✅ Completed ({voting_session.total_votes} votes) | Winners: {voting_session.winning_presentation_style or 'N/A'} & {voting_session.winning_speed_dating_twist or 'N/A'}"
            elif voting_session.is_active:
                return f"🟢 ACTIVE ({voting_session.total_votes} votes so far)"
            else:
                return "⏸️ Not Started"
        except EventVotingSession.DoesNotExist:
            return "❌ No Voting Session"
    get_voting_status.short_description = '🗳️ Phase 1: Voting'

    def get_presentation_status(self, obj):
        """Display Phase 2 presentation status"""
        presentations = obj.presentation_queue.all()
        if not presentations.exists():
            return "❌ Not Initialized"

        total = presentations.count()
        completed = presentations.filter(status='completed').count()
        in_progress = presentations.filter(status='in_progress').exists()

        if completed == total:
            return f"✅ All Complete ({total}/{total})"
        elif in_progress:
            return f"🟢 IN PROGRESS ({completed}/{total} done)"
        elif completed > 0:
            return f"⏸️ Paused ({completed}/{total} done)"
        else:
            return f"⏳ Ready to Start (0/{total})"
    get_presentation_status.short_description = '🎤 Phase 2: Presentations'

    def get_speed_dating_status(self, obj):
        """Display Phase 3 speed dating status"""
        pairs = obj.speed_dating_pairs.all()
        if not pairs.exists():
            return "❌ Not Initialized"

        total_pairs = pairs.count()
        completed_pairs = pairs.filter(completed_at__isnull=False).count()
        in_progress = pairs.filter(started_at__isnull=False, completed_at__isnull=True).exists()

        if completed_pairs == total_pairs:
            return f"✅ All Rounds Complete ({total_pairs} pairs)"
        elif in_progress:
            return f"🟢 IN PROGRESS ({completed_pairs}/{total_pairs} rounds done)"
        elif completed_pairs > 0:
            return f"⏸️ Paused ({completed_pairs}/{total_pairs} rounds done)"
        else:
            return f"⏳ Ready to Start (0/{total_pairs} pairs)"
    get_speed_dating_status.short_description = '💕 Phase 3: Speed Dating'

    # Admin actions
    @admin.action(description='✅ Publish selected events')
    def publish_events(self, request, queryset):
        updated = queryset.update(is_published=True)
        django_messages.success(request, f"Published {updated} event(s)")

    @admin.action(description='❌ Unpublish selected events')
    def unpublish_events(self, request, queryset):
        updated = queryset.update(is_published=False)
        django_messages.success(request, f"Unpublished {updated} event(s)")

    @admin.action(description='🚫 Cancel selected events')
    def cancel_events(self, request, queryset):
        updated = queryset.update(is_cancelled=True)
        django_messages.success(request, f"Cancelled {updated} event(s)")


@admin.register(EventRegistration)
class EventRegistrationAdmin(admin.ModelAdmin):
    list_display = ('user', 'event', 'status', 'payment_confirmed', 'registered_at')
    list_filter = ('status', 'payment_confirmed', 'registered_at')
    search_fields = ('user__username', 'event__title')
    readonly_fields = ('registered_at', 'updated_at')
    fieldsets = (
        ('Registration Details', {
            'fields': ('event', 'user', 'status')
        }),
        ('Additional Information', {
            'fields': ('dietary_restrictions', 'special_requests')
        }),
        ('Payment', {
            'fields': ('payment_confirmed', 'payment_date')
        }),
        ('Timestamps', {
            'fields': ('registered_at', 'updated_at')
        }),
    )


@admin.register(EventConnection)
class EventConnectionAdmin(admin.ModelAdmin):
    list_display = ('requester', 'recipient', 'event', 'status', 'is_mutual', 'assigned_coach', 'requested_at')
    list_filter = ('status', 'requested_at', 'coach_approved_at')
    search_fields = ('requester__username', 'recipient__username', 'event__title')
    readonly_fields = ('requested_at', 'responded_at', 'coach_approved_at', 'shared_at', 'is_mutual')
    fieldsets = (
        ('Connection Details', {
            'fields': ('requester', 'recipient', 'event', 'status', 'is_mutual')
        }),
        ('Requester Info', {
            'fields': ('requester_note', 'requester_consents_to_share')
        }),
        ('Recipient Info', {
            'fields': ('recipient_consents_to_share',)
        }),
        ('Coach Facilitation', {
            'fields': ('assigned_coach', 'coach_notes', 'coach_introduction')
        }),
        ('Timestamps', {
            'fields': ('requested_at', 'responded_at', 'coach_approved_at', 'shared_at')
        }),
    )

    def is_mutual(self, obj):
        return obj.is_mutual
    is_mutual.boolean = True
    is_mutual.short_description = 'Mutual'


@admin.register(ConnectionMessage)
class ConnectionMessageAdmin(admin.ModelAdmin):
    list_display = ('connection', 'sender', 'is_coach_message', 'coach_approved', 'sent_at')
    list_filter = ('is_coach_message', 'coach_approved', 'sent_at')
    search_fields = ('sender__username', 'message', 'connection__event__title')
    readonly_fields = ('sent_at', 'read_at')
    fieldsets = (
        ('Message Details', {
            'fields': ('connection', 'sender', 'message')
        }),
        ('Moderation', {
            'fields': ('is_coach_message', 'coach_approved')
        }),
        ('Timestamps', {
            'fields': ('sent_at', 'read_at')
        }),
    )


# Inline admin for Activity Options within Event admin
class EventActivityOptionInline(admin.TabularInline):
    model = EventActivityOption
    extra = 0
    fields = ('display_name', 'activity_type', 'activity_variant', 'vote_count', 'is_winner')
    readonly_fields = ('vote_count', 'is_winner')


@admin.register(GlobalActivityOption)
class GlobalActivityOptionAdmin(admin.ModelAdmin):
    list_display = ('display_name', 'get_activity_phase', 'activity_variant', 'is_active', 'sort_order', 'created_at')
    list_filter = ('activity_type', 'is_active')
    search_fields = ('display_name', 'description', 'activity_variant')
    readonly_fields = ('created_at', 'updated_at')
    fieldsets = (
        ('Activity Details', {
            'fields': ('activity_type', 'activity_variant', 'display_name', 'description'),
            'description': 'These global options are reused across ALL Crush events'
        }),
        ('Settings', {
            'fields': ('is_active', 'sort_order')
        }),
        ('Metadata', {
            'fields': ('created_at', 'updated_at')
        }),
    )

    def get_activity_phase(self, obj):
        """Display friendly name for activity type"""
        phase_map = {
            'presentation_style': '🎤 Phase 2: Presentation Style',
            'speed_dating_twist': '💕 Phase 3: Speed Dating Twist',
        }
        return phase_map.get(obj.activity_type, obj.activity_type)
    get_activity_phase.short_description = 'Event Phase'


@admin.register(EventActivityOption)
class EventActivityOptionAdmin(admin.ModelAdmin):
    list_display = ('event', 'display_name', 'get_activity_phase', 'activity_variant', 'vote_count', 'is_winner', 'created_at')
    list_filter = ('activity_type', 'is_winner', 'event__date_time')
    search_fields = ('event__title', 'display_name', 'description')
    readonly_fields = ('created_at', 'vote_count', 'get_activity_phase')
    fieldsets = (
        ('Activity Details', {
            'fields': ('event', 'get_activity_phase', 'activity_type', 'activity_variant', 'display_name', 'description'),
            'description': 'Activity Type determines which phase this option belongs to: Presentation Style (Phase 2) or Speed Dating Twist (Phase 3)'
        }),
        ('Voting Results', {
            'fields': ('vote_count', 'is_winner')
        }),
        ('Metadata', {
            'fields': ('created_at',)
        }),
    )

    def get_activity_phase(self, obj):
        """Display friendly name for activity type"""
        phase_map = {
            'presentation_style': '🎤 Phase 2: Presentation Style',
            'speed_dating_twist': '💕 Phase 3: Speed Dating Twist',
        }
        return phase_map.get(obj.activity_type, obj.activity_type)
    get_activity_phase.short_description = 'Event Phase'


@admin.register(EventActivityVote)
class EventActivityVoteAdmin(admin.ModelAdmin):
    list_display = ('user', 'event', 'selected_option', 'voted_at')
    list_filter = ('event', 'voted_at')
    search_fields = ('user__username', 'event__title', 'selected_option__display_name')
    readonly_fields = ('voted_at',)
    fieldsets = (
        ('Vote Details', {
            'fields': ('event', 'user', 'selected_option')
        }),
        ('Metadata', {
            'fields': ('voted_at',)
        }),
    )


@admin.register(EventVotingSession)
class EventVotingSessionAdmin(admin.ModelAdmin):
    list_display = ('event', 'is_active', 'voting_start_time', 'voting_end_time', 'total_votes', 'winning_presentation_style', 'winning_speed_dating_twist')
    list_filter = ('is_active', 'voting_start_time', 'voting_end_time')
    search_fields = ('event__title',)
    readonly_fields = ('created_at', 'updated_at', 'total_votes')
    actions = ['start_voting_session', 'end_voting_session']
    fieldsets = (
        ('Event', {
            'fields': ('event',)
        }),
        ('Voting Schedule', {
            'fields': ('voting_start_time', 'voting_end_time', 'is_active')
        }),
        ('Results', {
            'fields': ('total_votes', 'winning_presentation_style', 'winning_speed_dating_twist')
        }),
        ('Metadata', {
            'fields': ('created_at', 'updated_at')
        }),
    )

    @admin.action(description='Start voting for selected sessions')
    def start_voting_session(self, request, queryset):
        """Manually start voting sessions"""
        updated = 0
        for session in queryset:
            session.start_voting()
            updated += 1
        django_messages.success(request, f"Started voting for {updated} session(s)")

    @admin.action(description='End voting and calculate winners')
    def end_voting_session(self, request, queryset):
        """Manually end voting sessions and calculate winners"""
        updated = 0
        for session in queryset:
            session.end_voting()
            updated += 1
        django_messages.success(request, f"Ended voting and calculated winners for {updated} session(s)")


@admin.register(PresentationQueue)
class PresentationQueueAdmin(admin.ModelAdmin):
    list_display = ('event', 'user', 'presentation_order', 'status', 'started_at', 'completed_at', 'duration_seconds')
    list_filter = ('status', 'event')
    search_fields = ('user__username', 'event__title')
    readonly_fields = ('created_at', 'updated_at', 'duration_seconds')
    ordering = ['event', 'presentation_order']

    fieldsets = (
        ('Presentation Details', {
            'fields': ('event', 'user', 'presentation_order', 'status')
        }),
        ('Timing', {
            'fields': ('started_at', 'completed_at', 'duration_seconds')
        }),
        ('Metadata', {
            'fields': ('created_at', 'updated_at')
        }),
    )


@admin.register(PresentationRating)
class PresentationRatingAdmin(admin.ModelAdmin):
    list_display = ('event', 'presenter', 'rater', 'rating', 'rated_at')
    list_filter = ('rating', 'event')
    search_fields = ('presenter__username', 'rater__username', 'event__title')
    readonly_fields = ('rated_at',)
    ordering = ['-rated_at']

    fieldsets = (
        ('Rating Details', {
            'fields': ('event', 'presenter', 'rater', 'rating')
        }),
        ('Metadata', {
            'fields': ('rated_at',)
        }),
    )


@admin.register(SpeedDatingPair)
class SpeedDatingPairAdmin(admin.ModelAdmin):
    list_display = ('event', 'round_number', 'user1', 'user2', 'mutual_rating_score', 'is_top_match', 'duration_minutes')
    list_filter = ('is_top_match', 'event', 'round_number')
    search_fields = ('user1__username', 'user2__username', 'event__title')
    readonly_fields = ('created_at', 'duration_minutes')
    ordering = ['event', 'round_number']

    fieldsets = (
        ('Pairing Details', {
            'fields': ('event', 'user1', 'user2', 'round_number')
        }),
        ('Matching Data', {
            'fields': ('mutual_rating_score', 'is_top_match', 'duration_minutes')
        }),
        ('Timing', {
            'fields': ('started_at', 'completed_at')
        }),
        ('Metadata', {
            'fields': ('created_at',)
        }),
    )
