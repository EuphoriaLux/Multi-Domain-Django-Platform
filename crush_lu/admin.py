from django.contrib import admin
from django.contrib import messages as django_messages
from django.db import transaction
from django.utils.html import format_html
from .models import (
    CrushCoach, CrushProfile, ProfileSubmission,
    CoachSession, MeetupEvent, EventRegistration,
    EventConnection, ConnectionMessage,
    GlobalActivityOption, EventActivityOption, EventActivityVote, EventVotingSession,
    PresentationQueue, PresentationRating, SpeedDatingPair,
    SpecialUserExperience,
    # Journey System Models
    JourneyConfiguration, JourneyChapter, JourneyChallenge,
    JourneyReward, JourneyProgress, ChapterProgress, ChallengeAttempt, RewardProgress
)


# ============================================================================
# CUSTOM ADMIN SITE - Improved Organization
# ============================================================================

class CrushLuAdminSite(admin.AdminSite):
    site_header = '💕 Crush.lu Administration'
    site_title = 'Crush.lu Admin'
    index_title = 'Welcome to Crush.lu Management'

    def get_app_list(self, request, app_label=None):
        """
        Override to customize the admin index page grouping.
        Groups models into logical categories for better organization.
        """
        app_list = super().get_app_list(request, app_label)

        # Custom ordering and grouping
        custom_order = {
            # 1. Special Journey System (VIP Experience)
            'special_user_experience': {'order': 1, 'icon': '✨', 'group': 'Special Journey System'},
            'journeyconfiguration': {'order': 2, 'icon': '🗺️', 'group': 'Special Journey System'},
            'journeychapter': {'order': 3, 'icon': '📖', 'group': 'Special Journey System'},
            'journeychallenge': {'order': 4, 'icon': '🎯', 'group': 'Special Journey System'},
            'journeyreward': {'order': 5, 'icon': '🎁', 'group': 'Special Journey System'},
            'journeyprogress': {'order': 6, 'icon': '📊', 'group': 'Special Journey System'},
            'chapterprogress': {'order': 7, 'icon': '📈', 'group': 'Special Journey System'},
            'challengeattempt': {'order': 8, 'icon': '🎮', 'group': 'Special Journey System'},
            'rewardprogress': {'order': 9, 'icon': '🏆', 'group': 'Special Journey System'},

            # 2. User Profiles & Onboarding
            'crushprofile': {'order': 10, 'icon': '👤', 'group': 'Users & Profiles'},
            'profilesubmission': {'order': 11, 'icon': '📝', 'group': 'Users & Profiles'},
            'crushcoach': {'order': 12, 'icon': '🎓', 'group': 'Users & Profiles'},
            'coachsession': {'order': 13, 'icon': '💬', 'group': 'Users & Profiles'},

            # 3. Events & Meetups
            'meetupevent': {'order': 20, 'icon': '🎉', 'group': 'Events & Meetups'},
            'eventregistration': {'order': 21, 'icon': '✅', 'group': 'Events & Meetups'},
            'globalactivityoption': {'order': 22, 'icon': '🎯', 'group': 'Events & Meetups'},
            'eventactivityoption': {'order': 23, 'icon': '🎲', 'group': 'Events & Meetups'},
            'eventactivityvote': {'order': 24, 'icon': '🗳️', 'group': 'Events & Meetups'},
            'eventvotingsession': {'order': 25, 'icon': '⏱️', 'group': 'Events & Meetups'},
            'presentationqueue': {'order': 26, 'icon': '📋', 'group': 'Events & Meetups'},
            'presentationrating': {'order': 27, 'icon': '⭐', 'group': 'Events & Meetups'},
            'speeddatingpair': {'order': 28, 'icon': '💑', 'group': 'Events & Meetups'},

            # 4. Connections & Messages
            'eventconnection': {'order': 30, 'icon': '🔗', 'group': 'Connections'},
            'connectionmessage': {'order': 31, 'icon': '💌', 'group': 'Connections'},
        }

        # Apply custom ordering
        for app in app_list:
            if app['app_label'] == 'crush_lu':
                for model in app['models']:
                    model_name = model['object_name'].lower()
                    if model_name in custom_order:
                        config = custom_order[model_name]
                        model['_order'] = config['order']
                        model['_group'] = config['group']
                        # Add icon to model name
                        icon = config['icon']
                        model['name'] = f"{icon} {model['name']}"

                # Sort models by custom order
                app['models'].sort(key=lambda x: x.get('_order', 999))

        return app_list


# Use custom admin site
# admin_site = CrushLuAdminSite(name='crush_admin')


# ============================================================================
# SPECIAL JOURNEY SYSTEM - VIP Experience Models
# ============================================================================


@admin.register(SpecialUserExperience)
class SpecialUserExperienceAdmin(admin.ModelAdmin):
    """
    ✨ SPECIAL JOURNEY SYSTEM - VIP Experience Configuration

    This is the entry point for creating personalized journey experiences.
    Configure who gets the special journey and customize their experience.
    """
    list_display = (
        'first_name', 'last_name', 'is_active',
        'custom_welcome_title', 'animation_style',
        'auto_approve_profile', 'vip_badge',
        'trigger_count', 'last_triggered_at'
    )
    list_filter = ('is_active', 'animation_style', 'auto_approve_profile', 'vip_badge', 'skip_waitlist')
    search_fields = ('first_name', 'last_name', 'custom_welcome_title', 'custom_welcome_message')
    readonly_fields = ('created_at', 'updated_at', 'last_triggered_at', 'trigger_count')
    actions = ['activate_experiences', 'deactivate_experiences']

    fieldsets = (
        ('👤 User Matching', {
            'fields': ('first_name', 'last_name', 'is_active'),
            'description': 'User must match BOTH first name AND last name (case-insensitive)'
        }),
        ('🎨 Custom Welcome Experience', {
            'fields': (
                'custom_welcome_title',
                'custom_welcome_message',
                'custom_theme_color',
                'animation_style',
                'custom_landing_url',
            ),
            'description': 'Customize the special welcome page appearance'
        }),
        ('⭐ VIP Features & Permissions', {
            'fields': (
                'auto_approve_profile',
                'skip_waitlist',
                'vip_badge',
            ),
            'description': 'Special permissions and features for this user'
        }),
        ('📊 Tracking & Analytics', {
            'fields': (
                'trigger_count',
                'last_triggered_at',
            ),
            'classes': ('collapse',),
            'description': 'Track how often this special experience has been used'
        }),
        ('🗓️ Metadata', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )

    @admin.action(description='✅ Activate selected experiences')
    def activate_experiences(self, request, queryset):
        updated = queryset.update(is_active=True)
        django_messages.success(request, f"Activated {updated} special experience(s)")

    @admin.action(description='❌ Deactivate selected experiences')
    def deactivate_experiences(self, request, queryset):
        updated = queryset.update(is_active=False)
        django_messages.success(request, f"Deactivated {updated} special experience(s)")


# ============================================================================
# USER PROFILES & ONBOARDING - Profile Management
# ============================================================================


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


# ============================================================================
# EVENTS & MEETUPS - Event Management System
# ============================================================================


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


# ============================================================================
# CONNECTIONS & MESSAGES - Post-Event Networking
# ============================================================================


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


# ============================================================================
# EVENT ACTIVITIES - Voting & Activity Management
# ============================================================================


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


# ============================================================================
# PRESENTATIONS - Speed Dating Presentation System
# ============================================================================


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


# ============================================================================
# INTERACTIVE JOURNEY SYSTEM - ADMIN INTERFACES
# ============================================================================

# Inline Admins for nested management
class JourneyChallengeInline(admin.TabularInline):
    model = JourneyChallenge
    extra = 0
    fields = ('challenge_order', 'challenge_type', 'question', 'points_awarded')
    show_change_link = True
    ordering = ['challenge_order']


class JourneyRewardInline(admin.StackedInline):
    model = JourneyReward
    extra = 0
    fields = ('reward_type', 'title', 'photo', 'audio_file', 'video_file')
    show_change_link = True


class JourneyChapterInline(admin.TabularInline):
    model = JourneyChapter
    extra = 0
    fields = ('chapter_number', 'title', 'theme', 'background_theme', 'difficulty')
    show_change_link = True
    ordering = ['chapter_number']


class ChapterProgressInline(admin.TabularInline):
    model = ChapterProgress
    extra = 0
    fields = ('chapter', 'is_completed', 'points_earned', 'time_spent_seconds', 'completed_at')
    readonly_fields = ('started_at', 'completed_at')
    can_delete = False


class ChallengeAttemptInline(admin.TabularInline):
    model = ChallengeAttempt
    extra = 0
    fields = ('challenge', 'user_answer', 'is_correct', 'points_earned', 'attempted_at')
    readonly_fields = ('attempted_at',)
    can_delete = False
    ordering = ['-attempted_at']


# ============================================================================
# JOURNEY SYSTEM - Content Configuration (Admin Creates Journey)
# ============================================================================


@admin.register(JourneyConfiguration)
class JourneyConfigurationAdmin(admin.ModelAdmin):
    """
    🗺️ JOURNEY CONFIGURATION - Create the Journey Structure

    Start here to create a new personalized journey experience.
    Define chapters, challenges, and rewards for a specific user.
    """
    list_display = (
        'journey_name', 'get_user_name', 'is_active',
        'total_chapters', 'estimated_duration_minutes',
        'certificate_enabled', 'created_at'
    )
    list_filter = ('is_active', 'certificate_enabled', 'created_at')
    search_fields = (
        'journey_name', 'special_experience__first_name',
        'special_experience__last_name', 'final_message'
    )
    readonly_fields = ('created_at', 'updated_at')
    inlines = [JourneyChapterInline]
    actions = ['activate_journeys', 'deactivate_journeys', 'duplicate_journey']

    fieldsets = (
        ('🎯 Journey Basics', {
            'fields': ('special_experience', 'is_active', 'journey_name'),
            'description': 'Link this journey to a Special User Experience'
        }),
        ('📊 Journey Metadata', {
            'fields': ('total_chapters', 'estimated_duration_minutes')
        }),
        ('💝 Personalization Data', {
            'fields': ('date_first_met', 'location_first_met'),
            'description': 'Personal facts used in riddles and challenges'
        }),
        ('🏆 Completion Settings', {
            'fields': ('certificate_enabled', 'final_message'),
            'description': 'What happens when the journey is completed'
        }),
        ('🗓️ Metadata', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    def get_user_name(self, obj):
        """Display the target user's name"""
        return f"{obj.special_experience.first_name} {obj.special_experience.last_name}"
    get_user_name.short_description = 'For User'
    get_user_name.admin_order_field = 'special_experience__first_name'

    @admin.action(description='✅ Activate selected journeys')
    def activate_journeys(self, request, queryset):
        updated = queryset.update(is_active=True)
        django_messages.success(request, f"Activated {updated} journey(s)")

    @admin.action(description='❌ Deactivate selected journeys')
    def deactivate_journeys(self, request, queryset):
        updated = queryset.update(is_active=False)
        django_messages.success(request, f"Deactivated {updated} journey(s)")

    @admin.action(description='📋 Duplicate journey (create copy)')
    def duplicate_journey(self, request, queryset):
        """Clone a journey for reuse with another user"""
        if queryset.count() != 1:
            django_messages.error(request, "Please select exactly one journey to duplicate")
            return

        original = queryset.first()
        # Note: Actual duplication logic would be implemented here
        django_messages.info(
            request,
            f"Duplication feature coming soon! Would create a copy of '{original.journey_name}'"
        )


@admin.register(JourneyChapter)
class JourneyChapterAdmin(admin.ModelAdmin):
    """
    📖 JOURNEY CHAPTERS - Structure the Journey

    Each chapter represents a section of the journey with multiple challenges.
    """
    list_display = (
        'get_chapter_display', 'journey', 'title', 'theme',
        'background_theme', 'difficulty', 'estimated_duration',
        'get_challenge_count', 'get_reward_count'
    )
    list_filter = ('difficulty', 'background_theme', 'journey')
    search_fields = ('title', 'theme', 'story_introduction', 'journey__journey_name')
    readonly_fields = ('get_challenge_count', 'get_reward_count')
    inlines = [JourneyChallengeInline, JourneyRewardInline]
    ordering = ['journey', 'chapter_number']

    fieldsets = (
        ('🔢 Chapter Identity', {
            'fields': ('journey', 'chapter_number', 'title', 'theme')
        }),
        ('📖 Story & Theme', {
            'fields': ('story_introduction', 'background_theme')
        }),
        ('⚙️ Settings', {
            'fields': ('estimated_duration', 'difficulty', 'requires_previous_completion')
        }),
        ('💬 Completion Message', {
            'fields': ('completion_message',),
            'description': 'Personal message shown after completing all challenges'
        }),
        ('📊 Statistics', {
            'fields': ('get_challenge_count', 'get_reward_count'),
            'classes': ('collapse',)
        }),
    )

    def get_chapter_display(self, obj):
        return f"Chapter {obj.chapter_number}"
    get_chapter_display.short_description = 'Chapter #'
    get_chapter_display.admin_order_field = 'chapter_number'

    def get_challenge_count(self, obj):
        return obj.challenges.count()
    get_challenge_count.short_description = '🎯 Challenges'

    def get_reward_count(self, obj):
        return obj.rewards.count()
    get_reward_count.short_description = '🎁 Rewards'


@admin.register(JourneyChallenge)
class JourneyChallengeAdmin(admin.ModelAdmin):
    """
    🎯 JOURNEY CHALLENGES - Add Interactive Activities

    Create riddles, quizzes, word scrambles, and more.
    Questionnaire mode (blank correct_answer) saves all responses for analysis.
    """
    list_display = (
        'get_chapter_display', 'challenge_order', 'challenge_type',
        'get_question_preview', 'points_awarded', 'has_hints'
    )
    list_filter = ('challenge_type', 'chapter__journey', 'chapter__chapter_number')
    search_fields = ('question', 'correct_answer', 'success_message')
    ordering = ['chapter__chapter_number', 'challenge_order']

    fieldsets = (
        ('📍 Challenge Location', {
            'fields': ('chapter', 'challenge_order', 'challenge_type')
        }),
        ('❓ Challenge Content', {
            'fields': ('question', 'options', 'correct_answer', 'alternative_answers'),
            'description': '''
                <strong>For Quiz Challenges:</strong> Set correct_answer and optionally alternative_answers.<br>
                <strong>For Questionnaires:</strong> Leave correct_answer blank - all answers are saved for analysis.<br>
                <em>Questionnaire types: open_text, would_you_rather, or any challenge in Chapters 2, 4, 5</em>
            '''
        }),
        ('💡 Hints System', {
            'fields': (
                ('hint_1', 'hint_1_cost'),
                ('hint_2', 'hint_2_cost'),
                ('hint_3', 'hint_3_cost'),
            ),
            'classes': ('collapse',)
        }),
        ('🏆 Scoring & Feedback', {
            'fields': ('points_awarded', 'success_message')
        }),
    )

    def get_chapter_display(self, obj):
        return f"Ch{obj.chapter.chapter_number}: {obj.chapter.title}"
    get_chapter_display.short_description = 'Chapter'

    def get_question_preview(self, obj):
        return obj.question[:50] + '...' if len(obj.question) > 50 else obj.question
    get_question_preview.short_description = 'Question Preview'

    def has_hints(self, obj):
        return bool(obj.hint_1 or obj.hint_2 or obj.hint_3)
    has_hints.boolean = True
    has_hints.short_description = 'Has Hints?'


@admin.register(JourneyReward)
class JourneyRewardAdmin(admin.ModelAdmin):
    """
    🎁 JOURNEY REWARDS - Special Surprises & Media

    Upload photos, videos, audio messages, and letters as rewards.
    Photo reveals use jigsaw puzzles that cost points to unlock.
    """
    list_display = (
        'get_chapter_display', 'title', 'reward_type',
        'has_photo', 'has_audio', 'has_video'
    )
    list_filter = ('reward_type', 'chapter__journey', 'chapter__chapter_number')
    search_fields = ('title', 'message')
    ordering = ['chapter__chapter_number']

    fieldsets = (
        ('📍 Reward Location', {
            'fields': ('chapter', 'reward_type', 'title')
        }),
        ('📝 Content', {
            'fields': ('message',)
        }),
        ('🖼️ Media Files', {
            'fields': ('photo', 'audio_file', 'video_file'),
            'description': 'Upload photos, audio recordings, or video messages'
        }),
        ('🧩 Puzzle Settings', {
            'fields': ('puzzle_pieces',),
            'description': 'For photo_reveal type: number of jigsaw pieces'
        }),
    )

    def get_chapter_display(self, obj):
        return f"Ch{obj.chapter.chapter_number}: {obj.chapter.title}"
    get_chapter_display.short_description = 'Chapter'

    def has_photo(self, obj):
        return bool(obj.photo)
    has_photo.boolean = True
    has_photo.short_description = '📷 Photo'

    def has_audio(self, obj):
        return bool(obj.audio_file)
    has_audio.boolean = True
    has_audio.short_description = '🎵 Audio'

    def has_video(self, obj):
        return bool(obj.video_file)
    has_video.boolean = True
    has_video.short_description = '🎬 Video'


# ============================================================================
# JOURNEY SYSTEM - User Progress Tracking (User Experience Data)
# ============================================================================


@admin.register(JourneyProgress)
class JourneyProgressAdmin(admin.ModelAdmin):
    """
    📊 JOURNEY PROGRESS - Track User Experience

    Monitor how users progress through their personalized journey.
    View completion rates, points earned, and time spent.
    """
    list_display = (
        'user', 'get_journey_name', 'current_chapter',
        'get_completion_pct', 'total_points', 'get_time_spent',
        'is_completed', 'final_response', 'last_activity'
    )
    list_filter = ('is_completed', 'final_response', 'started_at', 'completed_at')
    search_fields = (
        'user__username', 'user__email', 'user__first_name', 'user__last_name',
        'journey__journey_name'
    )
    readonly_fields = (
        'started_at', 'last_activity', 'completed_at',
        'get_completion_pct', 'get_time_spent'
    )
    inlines = [ChapterProgressInline]
    ordering = ['-last_activity']

    fieldsets = (
        ('👤 User & Journey', {
            'fields': ('user', 'journey')
        }),
        ('📊 Progress Tracking', {
            'fields': (
                'current_chapter', 'get_completion_pct',
                'total_points', 'get_time_spent'
            )
        }),
        ('✅ Completion Status', {
            'fields': ('is_completed', 'completed_at')
        }),
        ('💖 Final Response', {
            'fields': ('final_response', 'final_response_at'),
            'description': 'User\'s response to the final chapter reveal'
        }),
        ('🗓️ Timestamps', {
            'fields': ('started_at', 'last_activity'),
            'classes': ('collapse',)
        }),
    )

    def get_journey_name(self, obj):
        return obj.journey.journey_name
    get_journey_name.short_description = 'Journey'

    def get_completion_pct(self, obj):
        pct = obj.completion_percentage
        if pct == 100:
            return f"✅ {pct}%"
        elif pct >= 75:
            return f"🟢 {pct}%"
        elif pct >= 50:
            return f"🟡 {pct}%"
        else:
            return f"🔴 {pct}%"
    get_completion_pct.short_description = 'Completion'

    def get_time_spent(self, obj):
        """Convert seconds to human-readable format"""
        seconds = obj.total_time_seconds
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        if hours > 0:
            return f"{hours}h {minutes}m"
        return f"{minutes}m"
    get_time_spent.short_description = 'Time Spent'


@admin.register(ChapterProgress)
class ChapterProgressAdmin(admin.ModelAdmin):
    """
    📈 CHAPTER PROGRESS - Detailed Chapter Tracking

    See how users progress through each chapter and their scores.
    """
    list_display = (
        'get_user', 'get_chapter_display', 'is_completed',
        'points_earned', 'get_time_spent', 'started_at', 'completed_at'
    )
    list_filter = ('is_completed', 'chapter__chapter_number', 'started_at', 'completed_at')
    search_fields = (
        'journey_progress__user__username',
        'chapter__title',
        'journey_progress__journey__journey_name'
    )
    readonly_fields = ('started_at', 'completed_at', 'get_time_spent')
    inlines = [ChallengeAttemptInline]
    ordering = ['journey_progress__user', 'chapter__chapter_number']

    fieldsets = (
        ('📖 Chapter Info', {
            'fields': ('journey_progress', 'chapter')
        }),
        ('📊 Progress', {
            'fields': ('is_completed', 'points_earned', 'time_spent_seconds', 'get_time_spent')
        }),
        ('🗓️ Timestamps', {
            'fields': ('started_at', 'completed_at')
        }),
    )

    def get_user(self, obj):
        return obj.journey_progress.user.username
    get_user.short_description = 'User'

    def get_chapter_display(self, obj):
        return f"Ch{obj.chapter.chapter_number}: {obj.chapter.title}"
    get_chapter_display.short_description = 'Chapter'

    def get_time_spent(self, obj):
        """Convert seconds to human-readable format"""
        seconds = obj.time_spent_seconds
        minutes = seconds // 60
        return f"{minutes}m {seconds % 60}s"
    get_time_spent.short_description = 'Duration'


@admin.register(ChallengeAttempt)
class ChallengeAttemptAdmin(admin.ModelAdmin):
    """
    🎮 CHALLENGE ATTEMPTS - User Answers & Responses

    View all user answers to challenges. Export questionnaire responses to CSV.
    """
    list_display = (
        'get_user', 'get_chapter', 'get_challenge_display', 'is_correct',
        'points_earned', 'get_hints_count', 'attempted_at'
    )
    list_filter = (
        'is_correct', 'attempted_at', 'challenge__challenge_type',
        'challenge__chapter__chapter_number'
    )
    search_fields = (
        'chapter_progress__journey_progress__user__username',
        'challenge__question',
        'user_answer'
    )
    readonly_fields = ('attempted_at',)
    ordering = ['-attempted_at']
    actions = ['export_chapter2_responses']

    fieldsets = (
        ('🎯 Attempt Details', {
            'fields': ('chapter_progress', 'challenge', 'is_correct', 'points_earned')
        }),
        ('📝 User Response', {
            'fields': ('user_answer',)
        }),
        ('💡 Hints Used', {
            'fields': ('hints_used',)
        }),
        ('🗓️ Timestamp', {
            'fields': ('attempted_at',)
        }),
    )

    def get_user(self, obj):
        return obj.chapter_progress.journey_progress.user.username
    get_user.short_description = 'User'

    def get_chapter(self, obj):
        return f"Ch.{obj.challenge.chapter.chapter_number}"
    get_chapter.short_description = 'Chapter'

    def get_challenge_display(self, obj):
        return f"{obj.challenge.get_challenge_type_display()}"
    get_challenge_display.short_description = 'Challenge Type'

    def get_hints_count(self, obj):
        return len(obj.hints_used) if obj.hints_used else 0
    get_hints_count.short_description = '💡 Hints Used'

    def export_chapter2_responses(self, request, queryset):
        """Export Chapter 2 questionnaire responses as CSV"""
        import csv
        from django.http import HttpResponse
        from datetime import datetime

        # Filter for Chapter 2 only
        chapter2_attempts = queryset.filter(challenge__chapter__chapter_number=2).select_related(
            'chapter_progress__journey_progress__user',
            'challenge'
        ).order_by('chapter_progress__journey_progress__user', 'challenge__challenge_order')

        if not chapter2_attempts.exists():
            self.message_user(
                request,
                "No Chapter 2 responses found in selected items.",
                level=django_messages.WARNING
            )
            return

        # Create CSV response
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="chapter2_responses_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv"'

        writer = csv.writer(response)
        # Header row
        writer.writerow([
            'User',
            'Question',
            'User Answer',
            'Option Selected',
            'Points Earned',
            'Submitted At'
        ])

        # Data rows
        for attempt in chapter2_attempts:
            writer.writerow([
                attempt.chapter_progress.journey_progress.user.username,
                attempt.challenge.question,
                attempt.user_answer,
                f"Option {attempt.user_answer}",  # Shows which option letter was chosen
                attempt.points_earned,
                attempt.attempted_at.strftime('%Y-%m-%d %H:%M:%S')
            ])

        self.message_user(
            request,
            f"Exported {chapter2_attempts.count()} Chapter 2 responses.",
            level=django_messages.SUCCESS
        )

        return response

    export_chapter2_responses.short_description = "📊 Export Chapter 2 Questionnaire Responses (CSV)"


@admin.register(RewardProgress)
class RewardProgressAdmin(admin.ModelAdmin):
    """
    🏆 REWARD PROGRESS - Puzzle & Interactive Reward Tracking

    Track which jigsaw puzzle pieces users have unlocked and points spent.
    """
    list_display = (
        'get_user', 'get_reward', 'get_pieces_unlocked',
        'points_spent', 'is_completed', 'started_at'
    )
    list_filter = ('is_completed', 'reward__reward_type', 'started_at')
    search_fields = (
        'journey_progress__user__username',
        'reward__title'
    )
    readonly_fields = ('started_at', 'completed_at')

    def get_user(self, obj):
        return obj.journey_progress.user.username
    get_user.short_description = 'User'

    def get_reward(self, obj):
        return f"{obj.reward.title} (Ch{obj.reward.chapter.chapter_number})"
    get_reward.short_description = 'Reward'

    def get_pieces_unlocked(self, obj):
        total = 16  # Standard jigsaw puzzle size
        unlocked = len(obj.unlocked_pieces)
        return f"{unlocked}/{total}"
    get_pieces_unlocked.short_description = 'Progress'


# ============================================================================
# END INTERACTIVE JOURNEY SYSTEM - ADMIN INTERFACES
# ============================================================================
