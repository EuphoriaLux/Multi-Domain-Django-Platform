"""
Journey system admin classes for Crush.lu Coach Panel.

Includes:
- JourneyConfigurationAdmin
- JourneyChapterAdmin
- JourneyChallengeAdmin
- JourneyRewardAdmin
- JourneyProgressAdmin
- ChapterProgressAdmin
- ChallengeAttemptAdmin
- RewardProgressAdmin
- Journey inlines

Uses django-modeltranslation for multi-language support (EN/DE/FR).
TranslationAdmin provides tabbed interface for managing translations.
"""

from django.contrib import admin
from django.contrib import messages as django_messages
from django.utils.translation import gettext_lazy as _
from modeltranslation.admin import TranslationAdmin, TranslationTabularInline, TranslationStackedInline

from crush_lu.models import (
    JourneyConfiguration, JourneyChapter, JourneyChallenge,
    JourneyReward, JourneyProgress, ChapterProgress, ChallengeAttempt, RewardProgress,
    JourneyGift,
)


# Inline Admins for nested management (with translation support)
class JourneyChallengeInline(TranslationTabularInline):
    model = JourneyChallenge
    extra = 0
    fields = ('challenge_order', 'challenge_type', 'question', 'points_awarded')
    show_change_link = True
    ordering = ['challenge_order']


class JourneyRewardInline(TranslationStackedInline):
    model = JourneyReward
    extra = 0
    fields = ('reward_type', 'title', 'photo', 'audio_file', 'video_file')
    show_change_link = True


class JourneyChapterInline(TranslationTabularInline):
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


class JourneyConfigurationAdmin(TranslationAdmin):
    """
    🗺️ JOURNEY CONFIGURATION - Create the Journey Structure

    Start here to create a new personalized journey experience.
    Define chapters, challenges, and rewards for a specific user.
    Uses django-modeltranslation for tabbed EN/DE/FR translation interface.
    """
    list_display = (
        'journey_name', 'get_user_name', 'journey_type', 'is_active',
        'total_chapters', 'estimated_duration_minutes',
        'certificate_enabled', 'created_at'
    )
    list_filter = ('journey_type', 'is_active', 'certificate_enabled', 'created_at')
    search_fields = (
        'journey_name', 'special_experience__first_name',
        'special_experience__last_name', 'final_message'
    )
    readonly_fields = ('created_at', 'updated_at')
    inlines = [JourneyChapterInline]
    actions = ['activate_journeys', 'deactivate_journeys', 'duplicate_journey']

    fieldsets = (
        ('🎯 Journey Basics', {
            'fields': ('special_experience', 'journey_type', 'is_active', 'journey_name'),
            'description': 'Link this journey to a Special User Experience. Translations managed via tabs above.'
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
    get_user_name.short_description = _('For User')
    get_user_name.admin_order_field = 'special_experience__first_name'

    @admin.action(description=_('✅ Activate selected journeys'))
    def activate_journeys(self, request, queryset):
        updated = queryset.update(is_active=True)
        django_messages.success(request, _("Activated {count} journey(s)").format(count=updated))

    @admin.action(description=_('❌ Deactivate selected journeys'))
    def deactivate_journeys(self, request, queryset):
        updated = queryset.update(is_active=False)
        django_messages.success(request, _("Deactivated {count} journey(s)").format(count=updated))

    @admin.action(description=_('📋 Duplicate journey (create copy)'))
    def duplicate_journey(self, request, queryset):
        """Clone a journey for reuse with another user"""
        if queryset.count() != 1:
            django_messages.error(request, _("Please select exactly one journey to duplicate"))
            return

        original = queryset.first()
        django_messages.info(
            request,
            _("Duplication feature coming soon! Would create a copy of '{name}'").format(name=original.journey_name)
        )


class JourneyChapterAdmin(TranslationAdmin):
    """
    📖 JOURNEY CHAPTERS - Structure the Journey

    Each chapter represents a section of the journey with multiple challenges.
    Uses django-modeltranslation for tabbed EN/DE/FR translation interface.
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
    get_chapter_display.short_description = _('Chapter #')
    get_chapter_display.admin_order_field = 'chapter_number'

    def get_challenge_count(self, obj):
        return obj.challenges.count()
    get_challenge_count.short_description = _('🎯 Challenges')

    def get_reward_count(self, obj):
        return obj.rewards.count()
    get_reward_count.short_description = _('🎁 Rewards')


class JourneyChallengeAdmin(TranslationAdmin):
    """
    🎯 JOURNEY CHALLENGES - Add Interactive Activities

    Create riddles, quizzes, word scrambles, and more.
    Questionnaire mode (blank correct_answer) saves all responses for analysis.
    Uses django-modeltranslation for tabbed EN/DE/FR translation interface.
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
    get_chapter_display.short_description = _('Chapter')

    def get_question_preview(self, obj):
        return obj.question[:50] + '...' if len(obj.question) > 50 else obj.question
    get_question_preview.short_description = _('Question Preview')

    def has_hints(self, obj):
        return bool(obj.hint_1 or obj.hint_2 or obj.hint_3)
    has_hints.boolean = True
    has_hints.short_description = _('Has Hints?')


class JourneyRewardAdmin(TranslationAdmin):
    """
    🎁 JOURNEY REWARDS - Special Surprises & Media

    Upload photos, videos, audio messages, and letters as rewards.
    Photo reveals use jigsaw puzzles that cost points to unlock.
    Uses django-modeltranslation for tabbed EN/DE/FR translation interface.
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
    get_chapter_display.short_description = _('Chapter')

    def has_photo(self, obj):
        return bool(obj.photo)
    has_photo.boolean = True
    has_photo.short_description = _('📷 Photo')

    def has_audio(self, obj):
        return bool(obj.audio_file)
    has_audio.boolean = True
    has_audio.short_description = _('🎵 Audio')

    def has_video(self, obj):
        return bool(obj.video_file)
    has_video.boolean = True
    has_video.short_description = _('🎬 Video')


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
    get_journey_name.short_description = _('Journey')

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
    get_completion_pct.short_description = _('Completion')

    def get_time_spent(self, obj):
        """Convert seconds to human-readable format"""
        seconds = obj.total_time_seconds
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        if hours > 0:
            return f"{hours}h {minutes}m"
        return f"{minutes}m"
    get_time_spent.short_description = _('Time Spent')


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
    get_user.short_description = _('User')

    def get_chapter_display(self, obj):
        return f"Ch{obj.chapter.chapter_number}: {obj.chapter.title}"
    get_chapter_display.short_description = _('Chapter')

    def get_time_spent(self, obj):
        """Convert seconds to human-readable format"""
        seconds = obj.time_spent_seconds
        minutes = seconds // 60
        return f"{minutes}m {seconds % 60}s"
    get_time_spent.short_description = _('Duration')


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
    get_user.short_description = _('User')

    def get_chapter(self, obj):
        return f"Ch.{obj.challenge.chapter.chapter_number}"
    get_chapter.short_description = _('Chapter')

    def get_challenge_display(self, obj):
        return f"{obj.challenge.get_challenge_type_display()}"
    get_challenge_display.short_description = _('Challenge Type')

    def get_hints_count(self, obj):
        return len(obj.hints_used) if obj.hints_used else 0
    get_hints_count.short_description = _('💡 Hints Used')

    def export_chapter2_responses(self, request, queryset):
        """Export Chapter 2 questionnaire responses as CSV"""
        import csv
        from django.http import HttpResponse
        from datetime import datetime

        chapter2_attempts = queryset.filter(challenge__chapter__chapter_number=2).select_related(
            'chapter_progress__journey_progress__user',
            'challenge'
        ).order_by('chapter_progress__journey_progress__user', 'challenge__challenge_order')

        if not chapter2_attempts.exists():
            self.message_user(
                request,
                _("No Chapter 2 responses found in selected items."),
                level=django_messages.WARNING
            )
            return

        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="chapter2_responses_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv"'

        writer = csv.writer(response)
        writer.writerow([
            'User', 'Question', 'User Answer', 'Option Selected',
            'Points Earned', 'Submitted At'
        ])

        for attempt in chapter2_attempts:
            writer.writerow([
                attempt.chapter_progress.journey_progress.user.username,
                attempt.challenge.question,
                attempt.user_answer,
                f"Option {attempt.user_answer}",
                attempt.points_earned,
                attempt.attempted_at.strftime('%Y-%m-%d %H:%M:%S')
            ])

        self.message_user(
            request,
            _("Exported {count} Chapter 2 responses.").format(count=chapter2_attempts.count()),
            level=django_messages.SUCCESS
        )

        return response

    export_chapter2_responses.short_description = _("📊 Export Chapter 2 Questionnaire Responses (CSV)")


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
    get_user.short_description = _('User')

    def get_reward(self, obj):
        return f"{obj.reward.title} (Ch{obj.reward.chapter.chapter_number})"
    get_reward.short_description = _('Reward')

    def get_pieces_unlocked(self, obj):
        total = 16  # Standard jigsaw puzzle size
        unlocked = len(obj.unlocked_pieces)
        return f"{unlocked}/{total}"
    get_pieces_unlocked.short_description = _('Progress')


class JourneyGiftAdmin(admin.ModelAdmin):
    """
    🎁 JOURNEY GIFTS - Shareable Journey Experiences

    Track gifts sent by users to non-users via QR codes.
    Each gift creates a personalized Wonderland journey when claimed.
    """
    list_display = (
        'gift_code', 'get_sender', 'recipient_name', 'status',
        'get_claimed_by', 'created_at', 'claimed_at'
    )
    list_filter = ('status', 'created_at', 'claimed_at')
    search_fields = (
        'gift_code', 'sender__username', 'sender__email',
        'recipient_name', 'recipient_email', 'claimed_by__username'
    )
    readonly_fields = ('gift_code', 'created_at', 'claimed_at', 'qr_code_image')
    ordering = ['-created_at']
    actions = ['mark_as_expired']

    fieldsets = (
        ('🎁 Gift Information', {
            'fields': ('gift_code', 'status', 'qr_code_image')
        }),
        ('👤 Sender', {
            'fields': ('sender', 'sender_message')
        }),
        ('💝 Recipient & Personalization', {
            'fields': ('recipient_name', 'recipient_email', 'date_first_met', 'location_first_met')
        }),
        ('✅ Claim Status', {
            'fields': ('claimed_by', 'claimed_at', 'journey', 'special_experience'),
            'classes': ('collapse',)
        }),
        ('🗓️ Timestamps', {
            'fields': ('created_at', 'expires_at'),
            'classes': ('collapse',)
        }),
    )

    def get_sender(self, obj):
        return f"{obj.sender.first_name} {obj.sender.last_name}"
    get_sender.short_description = _('Sender')
    get_sender.admin_order_field = 'sender__first_name'

    def get_claimed_by(self, obj):
        if obj.claimed_by:
            return f"{obj.claimed_by.first_name} {obj.claimed_by.last_name}"
        return "-"
    get_claimed_by.short_description = _('Claimed By')

    @admin.action(description=_('⏰ Mark selected gifts as expired'))
    def mark_as_expired(self, request, queryset):
        updated = queryset.filter(status=JourneyGift.Status.PENDING).update(
            status=JourneyGift.Status.EXPIRED
        )
        django_messages.success(request, _("Marked {count} gift(s) as expired").format(count=updated))
