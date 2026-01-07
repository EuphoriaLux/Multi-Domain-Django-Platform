from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from .profiles import SpecialUserExperience, get_crush_photo_storage

# Callable used by all file fields - Django calls this when needed
# This prevents migration drift between environments
crush_photo_storage = get_crush_photo_storage


class JourneyConfiguration(models.Model):
    """
    Main configuration for an interactive journey experience.
    Links to SpecialUserExperience to create personalized multi-chapter journeys.
    Supports multiple journey types per user (Wonderland, Advent Calendar, etc.)
    """
    JOURNEY_TYPES = [
        ('wonderland', 'The Wonderland of You'),
        ('advent_calendar', 'Advent Calendar'),
        ('custom', 'Custom Journey'),
    ]

    special_experience = models.ForeignKey(
        SpecialUserExperience,
        on_delete=models.CASCADE,
        related_name='journeys'
    )
    journey_type = models.CharField(
        max_length=20,
        choices=JOURNEY_TYPES,
        default='wonderland',
        help_text=_("Type of journey experience")
    )
    is_active = models.BooleanField(default=True)
    journey_name = models.CharField(
        max_length=200,
        default="The Wonderland of You",
        help_text=_("Name of this journey")
    )

    # Metadata
    total_chapters = models.IntegerField(
        default=6,
        help_text=_("Total number of chapters in this journey")
    )
    estimated_duration_minutes = models.IntegerField(
        default=90,
        help_text=_("Estimated total time to complete")
    )

    # Personalization data (for riddles/challenges)
    date_first_met = models.DateField(
        null=True,
        blank=True,
        help_text=_("Date you first met (for Chapter 1 riddle)")
    )
    location_first_met = models.CharField(
        max_length=200,
        blank=True,
        help_text=_("Where you first met")
    )

    # Journey completion
    certificate_enabled = models.BooleanField(
        default=True,
        help_text=_("Generate completion certificate")
    )
    final_message = models.TextField(
        help_text=_("The big reveal message shown in final chapter"),
        default="You've completed every challenge and discovered every secret..."
    )

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Journey Configuration")
        verbose_name_plural = _("🗺️ 2. Journey Configurations")
        unique_together = ('special_experience', 'journey_type')

    def __str__(self):
        return f"{self.journey_name} ({self.get_journey_type_display()}) for {self.special_experience}"


class JourneyChapter(models.Model):
    """
    Individual chapter in a journey with theme, challenges, and rewards.
    """
    BACKGROUND_THEMES = [
        ('wonderland_night', 'Wonderland Night (Dark starry sky)'),
        ('enchanted_garden', 'Enchanted Garden (Flowers & butterflies)'),
        ('art_gallery', 'Art Gallery (Golden frames & vintage)'),
        ('carnival', 'Carnival (Warm lights & mirrors)'),
        ('starlit_sky', 'Starlit Observatory (Deep space & cosmos)'),
        ('magical_door', 'Magical Door (Sunrise & celebration)'),
    ]

    DIFFICULTY_CHOICES = [
        ('easy', 'Easy'),
        ('medium', 'Medium'),
        ('hard', 'Hard'),
    ]

    journey = models.ForeignKey(
        JourneyConfiguration,
        on_delete=models.CASCADE,
        related_name='chapters'
    )
    chapter_number = models.IntegerField(help_text=_("1, 2, 3, etc."))

    # Chapter metadata
    title = models.CharField(
        max_length=200,
        help_text=_('e.g., "Down the Rabbit Hole"')
    )
    theme = models.CharField(
        max_length=100,
        help_text=_('e.g., "Mystery & Curiosity"')
    )
    story_introduction = models.TextField(
        help_text=_("The story/narrative shown at chapter start")
    )

    # Visual design
    background_theme = models.CharField(
        max_length=20,
        choices=BACKGROUND_THEMES,
        default='wonderland_night'
    )

    # Chapter settings
    estimated_duration = models.IntegerField(
        default=10,
        help_text=_("Estimated minutes to complete")
    )
    difficulty = models.CharField(
        max_length=20,
        choices=DIFFICULTY_CHOICES,
        default='easy'
    )

    # Unlock logic
    requires_previous_completion = models.BooleanField(
        default=True,
        help_text=_("Must complete previous chapter first")
    )

    # Completion message
    completion_message = models.TextField(
        help_text=_("Personal message shown after completing all challenges")
    )

    class Meta:
        ordering = ['chapter_number']
        unique_together = ('journey', 'chapter_number')
        verbose_name = _("Journey Chapter")
        verbose_name_plural = _("📖 3. Journey Chapters")

    def __str__(self):
        return f"Chapter {self.chapter_number}: {self.title}"


class JourneyChallenge(models.Model):
    """
    Individual challenges/puzzles within a chapter.
    """
    CHALLENGE_TYPES = [
        ('riddle', 'Riddle'),
        ('word_scramble', 'Word Scramble'),
        ('multiple_choice', 'Multiple Choice'),
        ('memory_match', 'Memory Matching Game'),
        ('photo_puzzle', 'Photo Jigsaw Puzzle'),
        ('timeline_sort', 'Timeline Sorting'),
        ('interactive_story', 'Interactive Story Choice'),
        ('open_text', 'Open Text Response'),
        ('would_you_rather', 'Would You Rather'),
        ('constellation', 'Constellation Drawing'),
        ('star_catcher', 'Star Catcher Mini-Game'),
    ]

    chapter = models.ForeignKey(
        JourneyChapter,
        on_delete=models.CASCADE,
        related_name='challenges'
    )
    challenge_order = models.IntegerField(
        help_text=_("Order within chapter (1, 2, 3...)")
    )
    challenge_type = models.CharField(
        max_length=30,
        choices=CHALLENGE_TYPES
    )

    # Challenge content
    question = models.TextField(
        help_text=_("The question/prompt/instructions")
    )

    # Flexible data storage for different challenge types
    options = models.JSONField(
        default=dict,
        blank=True,
        help_text=_('JSON data for options, choices, etc. ({"A": "option1", "B": "option2"})')
    )

    correct_answer = models.TextField(
        blank=True,
        help_text=_(
            "The correct answer for QUIZ mode. "
            "**LEAVE BLANK for QUESTIONNAIRE mode** (all answers accepted & saved for review). "
            "Chapters 2/4/5 and types 'open_text'/'would_you_rather' auto-detect questionnaire mode."
        )
    )
    alternative_answers = models.JSONField(
        default=list,
        blank=True,
        help_text=_('Alternative acceptable answers ["answer1", "answer2"]')
    )

    # Hints system
    hint_1 = models.TextField(blank=True)
    hint_1_cost = models.IntegerField(default=20, help_text=_("Points deducted for hint 1"))
    hint_2 = models.TextField(blank=True)
    hint_2_cost = models.IntegerField(default=50, help_text=_("Points deducted for hint 2"))
    hint_3 = models.TextField(blank=True)
    hint_3_cost = models.IntegerField(default=80, help_text=_("Points deducted for hint 3"))

    # Scoring
    points_awarded = models.IntegerField(
        default=100,
        help_text=_("Points for correct answer (before hint deductions)")
    )

    # Feedback
    success_message = models.TextField(
        help_text=_("Personal message shown when user answers correctly")
    )

    class Meta:
        ordering = ['challenge_order']
        verbose_name = _("Journey Challenge")
        verbose_name_plural = _("🎯 4. Journey Challenges")

    def __str__(self):
        return f"{self.chapter.title} - Challenge {self.challenge_order} ({self.get_challenge_type_display()})"


class JourneyReward(models.Model):
    """
    Rewards unlocked after completing chapters (photos, poems, videos, etc.)
    """
    REWARD_TYPES = [
        ('photo_reveal', 'Photo Reveal (Jigsaw)'),
        ('poem', 'Poem/Letter'),
        ('voice_message', 'Voice Recording'),
        ('video_message', 'Video Message'),
        ('photo_slideshow', 'Photo Slideshow'),
        ('future_letter', 'Future Letter'),
        ('certificate', 'Completion Certificate'),
    ]

    chapter = models.ForeignKey(
        JourneyChapter,
        on_delete=models.CASCADE,
        related_name='rewards'
    )
    reward_type = models.CharField(
        max_length=30,
        choices=REWARD_TYPES
    )

    # Content
    title = models.CharField(max_length=200)
    message = models.TextField(
        blank=True,
        help_text=_("Text content (poem, letter, caption, etc.)")
    )

    # Media uploads (use existing Crush.lu private storage)
    photo = models.ImageField(
        upload_to='journey_rewards/',
        blank=True,
        null=True,
        storage=crush_photo_storage
    )
    audio_file = models.FileField(
        upload_to='journey_rewards/audio/',
        blank=True,
        null=True,
        storage=crush_photo_storage
    )
    video_file = models.FileField(
        upload_to='journey_rewards/video/',
        blank=True,
        null=True,
        storage=crush_photo_storage
    )

    # For puzzles
    puzzle_pieces = models.IntegerField(
        default=16,
        help_text=_("Number of jigsaw pieces (4x4=16, 5x4=20, 6x5=30)")
    )

    class Meta:
        verbose_name = _("Journey Reward")
        verbose_name_plural = _("🎁 5. Journey Rewards")

    def __str__(self):
        return f"{self.chapter.title} - {self.title}"


class JourneyProgress(models.Model):
    """
    Tracks user's progress through a journey.
    """
    FINAL_RESPONSE_CHOICES = [
        ('yes', 'Yes, let\'s see where this goes 💫'),
        ('thinking', 'I need to think about this ✨'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE)
    journey = models.ForeignKey(JourneyConfiguration, on_delete=models.CASCADE)

    # Progress tracking
    current_chapter = models.IntegerField(default=1)
    total_points = models.IntegerField(default=0)
    total_time_seconds = models.IntegerField(
        default=0,
        help_text=_("Total time spent in journey")
    )

    # Completion
    is_completed = models.BooleanField(default=False)
    completed_at = models.DateTimeField(null=True, blank=True)

    # Session tracking
    started_at = models.DateTimeField(auto_now_add=True)
    last_activity = models.DateTimeField(auto_now=True)

    # Final response (from Chapter 6)
    final_response = models.CharField(
        max_length=20,
        choices=FINAL_RESPONSE_CHOICES,
        blank=True
    )
    final_response_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ('user', 'journey')
        verbose_name = _("Journey Progress")
        verbose_name_plural = _("📊 6. Journey Progress (User Tracking)")

    def __str__(self):
        return f"{self.user.username} - {self.journey.journey_name} (Chapter {self.current_chapter})"

    @property
    def completion_percentage(self):
        """Calculate completion percentage"""
        if self.is_completed:
            return 100
        completed_chapters = self.chapter_completions.filter(is_completed=True).count()
        total_chapters = self.journey.total_chapters
        return int((completed_chapters / total_chapters) * 100) if total_chapters > 0 else 0


class ChapterProgress(models.Model):
    """
    Tracks completion status of individual chapters.
    """
    journey_progress = models.ForeignKey(
        JourneyProgress,
        on_delete=models.CASCADE,
        related_name='chapter_completions'
    )
    chapter = models.ForeignKey(JourneyChapter, on_delete=models.CASCADE)

    # Progress
    is_completed = models.BooleanField(default=False)
    points_earned = models.IntegerField(default=0)
    time_spent_seconds = models.IntegerField(default=0)

    # Timestamps
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ('journey_progress', 'chapter')
        verbose_name = _("Chapter Progress")
        verbose_name_plural = _("📈 7. Chapter Progress (User Tracking)")

    def __str__(self):
        status = "✅" if self.is_completed else "🔄"
        return f"{status} {self.journey_progress.user.username} - {self.chapter.title}"


class ChallengeAttempt(models.Model):
    """
    Records user attempts at challenges (for tracking and admin review).
    """
    chapter_progress = models.ForeignKey(
        ChapterProgress,
        on_delete=models.CASCADE,
        related_name='attempts'
    )
    challenge = models.ForeignKey(JourneyChallenge, on_delete=models.CASCADE)

    # Attempt data
    user_answer = models.TextField()
    is_correct = models.BooleanField(default=False)
    hints_used = models.JSONField(
        default=list,
        help_text=_('List of hint numbers used [1, 2, 3]')
    )
    points_earned = models.IntegerField(default=0)

    # Timing
    attempted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-attempted_at']
        verbose_name = _("Challenge Attempt")
        verbose_name_plural = _("🎮 8. Challenge Attempts (User Answers)")

    def __str__(self):
        result = "✅" if self.is_correct else "❌"
        return f"{result} {self.challenge} - {self.points_earned} pts"


class RewardProgress(models.Model):
    """
    Tracks user's progress on interactive rewards (jigsaw puzzles, etc.)
    """
    journey_progress = models.ForeignKey(
        JourneyProgress,
        on_delete=models.CASCADE,
        related_name='reward_progress'
    )
    reward = models.ForeignKey(JourneyReward, on_delete=models.CASCADE)

    # Progress data (JSON for flexibility)
    unlocked_pieces = models.JSONField(
        default=list,
        help_text=_('List of unlocked piece indices [0, 1, 5, 7, ...]')
    )
    points_spent = models.IntegerField(default=0)
    is_completed = models.BooleanField(default=False)

    # Timestamps
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ('journey_progress', 'reward')
        verbose_name = _("Reward Progress")
        verbose_name_plural = _("🏆 9. Reward Progress (Puzzle Tracking)")

    def __str__(self):
        completion = "✅" if self.is_completed else f"{len(self.unlocked_pieces)}/16"
        return f"{self.journey_progress.user.username} - {self.reward.title} ({completion})"
