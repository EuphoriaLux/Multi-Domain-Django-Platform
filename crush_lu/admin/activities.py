"""
Activity voting and presentation admin classes for Crush.lu Coach Panel.

Includes:
- GlobalActivityOptionAdmin
- EventActivityOptionAdmin
- EventActivityVoteAdmin
- EventVotingSessionAdmin
- PresentationQueueAdmin
- PresentationRatingAdmin
- SpeedDatingPairAdmin
"""

from django.contrib import admin
from django.contrib import messages as django_messages
from django.utils.translation import gettext_lazy as _

from crush_lu.models import (
    GlobalActivityOption, EventActivityOption, EventActivityVote,
    EventVotingSession, PresentationQueue, PresentationRating, SpeedDatingPair,
)


# Inline admin for Activity Options within Event admin
class EventActivityOptionInline(admin.TabularInline):
    model = EventActivityOption
    extra = 0
    fields = ('display_name', 'activity_type', 'activity_variant', 'vote_count', 'is_winner')
    readonly_fields = ('vote_count', 'is_winner')


class GlobalActivityOptionAdmin(admin.ModelAdmin):
    list_display = ('display_name', 'get_activity_phase', 'activity_variant', 'get_total_votes', 'is_active', 'sort_order', 'created_at')
    list_filter = ('activity_type', 'is_active')
    search_fields = ('display_name', 'description', 'activity_variant')
    readonly_fields = ('created_at', 'updated_at', 'get_total_votes')
    fieldsets = (
        ('Activity Details', {
            'fields': ('activity_type', 'activity_variant', 'display_name', 'description'),
            'description': 'These global options are reused across ALL Crush events'
        }),
        ('Settings', {
            'fields': ('is_active', 'sort_order')
        }),
        ('Statistics', {
            'fields': ('get_total_votes',)
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
    get_activity_phase.short_description = _('Event Phase')

    def get_total_votes(self, obj):
        """Count total votes across all events"""
        return EventActivityVote.objects.filter(selected_option=obj).count()
    get_total_votes.short_description = _('Total Votes (all events)')


class EventActivityOptionAdmin(admin.ModelAdmin):
    list_display = ('event', 'display_name', 'get_activity_phase', 'activity_variant', 'get_actual_votes', 'is_winner', 'created_at')
    list_filter = ('activity_type', 'is_winner', 'event__date_time')
    search_fields = ('event__title', 'display_name', 'description')
    readonly_fields = ('created_at', 'vote_count', 'get_activity_phase', 'get_actual_votes')
    fieldsets = (
        ('Activity Details', {
            'fields': ('event', 'get_activity_phase', 'activity_type', 'activity_variant', 'display_name', 'description'),
            'description': 'Activity Type determines which phase this option belongs to: Presentation Style (Phase 2) or Speed Dating Twist (Phase 3)'
        }),
        ('Voting Results', {
            'fields': ('get_actual_votes', 'vote_count', 'is_winner'),
            'description': 'Actual votes are counted from EventActivityVote records. vote_count is a legacy field (may be stale).'
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
    get_activity_phase.short_description = _('Event Phase')

    def get_actual_votes(self, obj):
        """Count actual votes from EventActivityVote for the matching GlobalActivityOption"""
        # Find the matching GlobalActivityOption by activity_variant
        try:
            global_option = GlobalActivityOption.objects.get(
                activity_variant=obj.activity_variant,
                activity_type=obj.activity_type,
            )
            return EventActivityVote.objects.filter(
                event=obj.event,
                selected_option=global_option,
            ).count()
        except GlobalActivityOption.DoesNotExist:
            return 0
        except GlobalActivityOption.MultipleObjectsReturned:
            global_option = GlobalActivityOption.objects.filter(
                activity_variant=obj.activity_variant,
                activity_type=obj.activity_type,
            ).first()
            return EventActivityVote.objects.filter(
                event=obj.event,
                selected_option=global_option,
            ).count()
    get_actual_votes.short_description = _('Actual Votes')


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

    @admin.action(description=_('Start voting for selected sessions'))
    def start_voting_session(self, request, queryset):
        """Manually start voting sessions"""
        updated = 0
        for session in queryset:
            session.start_voting()
            updated += 1
        django_messages.success(request, _("Started voting for %(count)d session(s)") % {'count': updated})

    @admin.action(description=_('End voting and calculate winners'))
    def end_voting_session(self, request, queryset):
        """Manually end voting sessions and calculate winners"""
        updated = 0
        for session in queryset:
            session.end_voting()
            updated += 1
        django_messages.success(request, _("Ended voting and calculated winners for %(count)d session(s)") % {'count': updated})


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
