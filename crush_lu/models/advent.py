"""
Advent Calendar Models for Crush.lu

This module contains all models for the Advent Calendar feature,
which extends the Journey system to provide a 24-door December experience.
"""
import uuid
from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone

# Import the callable storage function from profiles (ensures consistent migration state)
from crush_lu.models.profiles import get_crush_photo_storage

# Callable used by all file fields - Django calls this when needed
# This prevents migration drift between environments
crush_photo_storage = get_crush_photo_storage


class AdventCalendar(models.Model):
    """
    Main Advent Calendar configuration for a user.
    Links to JourneyConfiguration with journey_type='advent_calendar'.
    """
    journey = models.OneToOneField(
        'JourneyConfiguration',
        on_delete=models.CASCADE,
        related_name='advent_calendar',
        help_text="The journey configuration for this advent calendar"
    )
    year = models.PositiveIntegerField(
        default=2024,
        help_text="Year for this advent calendar (e.g., 2024)"
    )
    start_date = models.DateField(
        help_text="Start date (usually December 1)"
    )
    end_date = models.DateField(
        help_text="End date (usually December 24)"
    )
    allow_catch_up = models.BooleanField(
        default=True,
        help_text="Allow users to open past doors (accumulating access)"
    )
    timezone_name = models.CharField(
        max_length=50,
        default='Europe/Luxembourg',
        help_text="Timezone for date calculations"
    )

    # Customization
    calendar_title = models.CharField(
        max_length=200,
        default="Your Advent Calendar",
        help_text="Title displayed on the calendar"
    )
    calendar_description = models.TextField(
        blank=True,
        help_text="Description shown on the calendar page"
    )
    background_image = models.ImageField(
        upload_to='advent_backgrounds/',
        blank=True,
        null=True,
        storage=crush_photo_storage,
        help_text="Custom background image for the calendar"
    )

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Advent Calendar"
        verbose_name_plural = "🎄 1. Advent Calendars"

    def __str__(self):
        return f"{self.calendar_title} ({self.year}) - {self.journey.special_experience}"

    def is_december(self):
        """Check if we're currently in December of the calendar year"""
        import pytz
        tz = pytz.timezone(self.timezone_name)
        now = timezone.now().astimezone(tz)
        return now.year == self.year and now.month == 12

    def get_current_day(self):
        """Get the current day in December (1-31) or None if not December"""
        import pytz
        tz = pytz.timezone(self.timezone_name)
        now = timezone.now().astimezone(tz)
        if now.year == self.year and now.month == 12:
            return now.day
        return None

    def is_door_available(self, door_number):
        """
        Check if a door can be opened based on current date.

        Args:
            door_number: The door number (1-24)

        Returns:
            bool: True if the door can be opened
        """
        if door_number < 1 or door_number > 24:
            return False

        import pytz
        tz = pytz.timezone(self.timezone_name)
        now = timezone.now().astimezone(tz)
        current_date = now.date()

        # Must be December of the correct year
        if current_date.year != self.year or current_date.month != 12:
            return False

        # Accumulating: doors 1-N available on day N
        if self.allow_catch_up:
            return door_number <= current_date.day and door_number <= 24
        else:
            # Strict: only today's door
            return door_number == current_date.day and door_number <= 24

    def get_available_doors(self):
        """Get list of door numbers that are currently available"""
        current_day = self.get_current_day()
        if current_day is None:
            return []
        if self.allow_catch_up:
            return list(range(1, min(current_day + 1, 25)))
        else:
            return [current_day] if current_day <= 24 else []


class AdventDoor(models.Model):
    """
    Individual door in an Advent Calendar.
    Each door can have different content types and QR code settings.

    When content_type='challenge', use challenge_type to specify which
    of the 11 Wonderland challenge types to use (riddle, word_scramble, etc.)
    """
    CONTENT_TYPES = [
        ('challenge', '🎯 Interactive Challenge'),
        ('poem', '📜 Poem/Letter'),
        ('photo', '📷 Photo Reveal'),
        ('video', '🎥 Video Message'),
        ('audio', '🎵 Audio Message'),
        ('gift_teaser', '🎁 Physical Gift Teaser'),
        ('memory', '💭 Shared Memory'),
        ('quiz', '❓ Fun Quiz'),
        ('countdown', '⏰ Countdown Special'),
    ]

    # Import challenge types from JourneyChallenge for consistency
    CHALLENGE_TYPES = [
        ('riddle', '🧩 Riddle'),
        ('word_scramble', '🔤 Word Scramble'),
        ('multiple_choice', '📝 Multiple Choice'),
        ('memory_match', '🃏 Memory Matching Game'),
        ('photo_puzzle', '🖼️ Photo Jigsaw Puzzle'),
        ('timeline_sort', '📅 Timeline Sorting'),
        ('interactive_story', '📖 Interactive Story Choice'),
        ('open_text', '✍️ Open Text Response'),
        ('would_you_rather', '🤔 Would You Rather'),
        ('constellation', '⭐ Constellation Drawing'),
        ('star_catcher', '🌟 Star Catcher Mini-Game'),
    ]

    QR_MODES = [
        ('none', 'No QR Required'),
        ('required', 'QR Required to Open Door'),
        ('bonus', 'QR Unlocks Bonus Content'),
    ]

    calendar = models.ForeignKey(
        AdventCalendar,
        on_delete=models.CASCADE,
        related_name='doors'
    )
    chapter = models.OneToOneField(
        'JourneyChapter',
        on_delete=models.CASCADE,
        related_name='advent_door',
        null=True,
        blank=True,
        help_text="Optional link to a JourneyChapter for challenge content"
    )
    door_number = models.PositiveIntegerField(
        help_text="Door number (1-24)"
    )
    content_type = models.CharField(
        max_length=20,
        choices=CONTENT_TYPES,
        default='poem'
    )

    # Challenge type (only used when content_type='challenge')
    challenge_type = models.CharField(
        max_length=30,
        choices=CHALLENGE_TYPES,
        blank=True,
        null=True,
        help_text="Type of challenge (only used when content_type is 'challenge')"
    )

    # QR Code settings (configurable per door)
    qr_mode = models.CharField(
        max_length=10,
        choices=QR_MODES,
        default='none',
        help_text="How QR codes work for this door"
    )

    # Visual customization
    door_color = models.CharField(
        max_length=7,
        default='#C41E3A',
        help_text="Hex color for the door (e.g., #C41E3A for Christmas red)"
    )
    door_icon = models.CharField(
        max_length=50,
        blank=True,
        help_text="Emoji or icon class for the door (e.g., 🎁, 🎄, ⭐)"
    )
    teaser_text = models.CharField(
        max_length=200,
        blank=True,
        help_text="Short teaser text shown on the closed door"
    )

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['door_number']
        unique_together = ('calendar', 'door_number')
        verbose_name = "Advent Door"
        verbose_name_plural = "🚪 2. Advent Doors"

    def __str__(self):
        return f"Door {self.door_number}: {self.get_content_type_display()}"

    def is_available(self):
        """Check if this door is currently available"""
        return self.calendar.is_door_available(self.door_number)

    def requires_qr_to_open(self):
        """Check if QR scan is required to open this door"""
        return self.qr_mode == 'required'

    def has_qr_bonus(self):
        """Check if this door has QR-locked bonus content"""
        return self.qr_mode == 'bonus'


class AdventDoorContent(models.Model):
    """
    Flexible content storage for advent doors.
    Supports various content types: poems, photos, videos, audio, gift teasers,
    and interactive challenges (using the same 11 challenge types as Wonderland).
    """
    door = models.OneToOneField(
        AdventDoor,
        on_delete=models.CASCADE,
        related_name='content'
    )

    # Main content
    title = models.CharField(
        max_length=200,
        help_text="Title shown when door is opened"
    )
    message = models.TextField(
        blank=True,
        help_text="Main text content (poem, letter, description, etc.)"
    )

    # =========================================================================
    # CHALLENGE FIELDS (used when door.content_type='challenge')
    # Same structure as JourneyChallenge for consistency
    # =========================================================================
    challenge_question = models.TextField(
        blank=True,
        help_text="The question/prompt/instructions for the challenge"
    )
    challenge_options = models.JSONField(
        default=dict,
        blank=True,
        help_text='JSON data for options/choices: {"A": "option1", "B": "option2"}'
    )
    challenge_correct_answer = models.TextField(
        blank=True,
        help_text="The correct answer. Leave blank for questionnaire mode (all answers accepted)."
    )
    challenge_alternative_answers = models.JSONField(
        default=list,
        blank=True,
        help_text='Alternative acceptable answers: ["answer1", "answer2"]'
    )

    # Hints system (matching Wonderland)
    hint_1 = models.TextField(
        blank=True,
        help_text="First hint (easiest)"
    )
    hint_1_cost = models.IntegerField(
        default=20,
        help_text="Points deducted for using hint 1"
    )
    hint_2 = models.TextField(
        blank=True,
        help_text="Second hint (medium)"
    )
    hint_2_cost = models.IntegerField(
        default=50,
        help_text="Points deducted for using hint 2"
    )
    hint_3 = models.TextField(
        blank=True,
        help_text="Third hint (biggest reveal)"
    )
    hint_3_cost = models.IntegerField(
        default=80,
        help_text="Points deducted for using hint 3"
    )

    # Challenge scoring
    points_awarded = models.IntegerField(
        default=100,
        help_text="Points for correct answer (before hint deductions)"
    )
    success_message = models.TextField(
        blank=True,
        help_text="Personal message shown when user answers correctly"
    )

    # =========================================================================
    # MEDIA CONTENT (using private storage in production)
    # =========================================================================
    photo = models.ImageField(
        upload_to='advent_doors/',
        blank=True,
        null=True,
        storage=crush_photo_storage,
        help_text="Main photo for this door"
    )
    video_file = models.FileField(
        upload_to='advent_doors/video/',
        blank=True,
        null=True,
        storage=crush_photo_storage,
        help_text="Video message"
    )
    audio_file = models.FileField(
        upload_to='advent_doors/audio/',
        blank=True,
        null=True,
        storage=crush_photo_storage,
        help_text="Audio message"
    )

    # =========================================================================
    # QR-LOCKED BONUS CONTENT (only visible after QR scan)
    # =========================================================================
    bonus_title = models.CharField(
        max_length=200,
        blank=True,
        help_text="Title for bonus content unlocked via QR"
    )
    bonus_content = models.TextField(
        blank=True,
        help_text="Bonus text content unlocked via QR code"
    )
    bonus_photo = models.ImageField(
        upload_to='advent_doors/bonus/',
        blank=True,
        null=True,
        storage=crush_photo_storage,
        help_text="Bonus photo unlocked via QR code"
    )

    # =========================================================================
    # PHYSICAL GIFT TEASER FIELDS
    # =========================================================================
    gift_hint = models.CharField(
        max_length=500,
        blank=True,
        help_text="Hint about the physical gift"
    )
    gift_location_clue = models.CharField(
        max_length=500,
        blank=True,
        help_text="Clue about where to find the gift"
    )

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Door Content"
        verbose_name_plural = "📝 3. Door Contents"

    def __str__(self):
        return f"Content for Door {self.door.door_number}: {self.title}"

    def has_media(self):
        """Check if this content has any media attachments"""
        return bool(self.photo or self.video_file or self.audio_file)

    def has_bonus_content(self):
        """Check if this door has any bonus content"""
        return bool(self.bonus_content or self.bonus_photo or self.bonus_title)

    def has_challenge(self):
        """Check if this door has challenge content configured"""
        return bool(self.challenge_question)

    def has_hints(self):
        """Check if any hints are configured"""
        return bool(self.hint_1 or self.hint_2 or self.hint_3)

    def is_questionnaire_mode(self):
        """Check if this challenge is in questionnaire mode (no correct answer)"""
        return self.door.content_type == 'challenge' and not self.challenge_correct_answer

    def check_answer(self, user_answer):
        """
        Check if user's answer is correct.

        Args:
            user_answer: The user's submitted answer

        Returns:
            bool: True if correct (or questionnaire mode), False otherwise
        """
        # Questionnaire mode - all answers are accepted
        if not self.challenge_correct_answer:
            return True

        # Normalize answers for comparison
        normalized_user = user_answer.strip().lower()
        normalized_correct = self.challenge_correct_answer.strip().lower()

        # Check main answer
        if normalized_user == normalized_correct:
            return True

        # Check alternative answers
        if self.challenge_alternative_answers:
            for alt in self.challenge_alternative_answers:
                if normalized_user == alt.strip().lower():
                    return True

        return False


class AdventProgress(models.Model):
    """
    Tracks user's progress through their Advent Calendar.
    """
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='advent_progress'
    )
    calendar = models.ForeignKey(
        AdventCalendar,
        on_delete=models.CASCADE,
        related_name='user_progress'
    )

    # Progress tracking
    doors_opened = models.JSONField(
        default=list,
        help_text="List of door numbers that have been opened [1, 2, 3, ...]"
    )
    qr_scans = models.JSONField(
        default=list,
        help_text="List of door numbers where QR was scanned [1, 5, 12, ...]"
    )

    # Last activity
    last_door_opened = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Last door number opened"
    )
    last_opened_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the last door was opened"
    )

    # Timestamps
    first_visit = models.DateTimeField(auto_now_add=True)
    last_visit = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('user', 'calendar')
        verbose_name = "Advent Progress"
        verbose_name_plural = "📊 4. Advent Progress"

    def __str__(self):
        opened = len(self.doors_opened) if self.doors_opened else 0
        return f"{self.user.username} - {opened}/24 doors opened"

    def open_door(self, door_number):
        """
        Mark a door as opened.

        Args:
            door_number: The door number to open (1-24)

        Returns:
            bool: True if door was newly opened, False if already opened
        """
        if not self.doors_opened:
            self.doors_opened = []

        if door_number not in self.doors_opened:
            self.doors_opened.append(door_number)
            self.last_door_opened = door_number
            self.last_opened_at = timezone.now()
            self.save()
            return True
        return False

    def is_door_opened(self, door_number):
        """Check if a specific door has been opened"""
        return door_number in (self.doors_opened or [])

    def record_qr_scan(self, door_number):
        """
        Record that a QR code was scanned for a door.

        Args:
            door_number: The door number where QR was scanned

        Returns:
            bool: True if QR scan was newly recorded
        """
        if not self.qr_scans:
            self.qr_scans = []

        if door_number not in self.qr_scans:
            self.qr_scans.append(door_number)
            self.save()
            return True
        return False

    def has_scanned_qr(self, door_number):
        """Check if QR was scanned for a specific door"""
        return door_number in (self.qr_scans or [])

    @property
    def completion_percentage(self):
        """Calculate completion percentage (out of 24 doors)"""
        opened = len(self.doors_opened) if self.doors_opened else 0
        return int((opened / 24) * 100)

    @property
    def doors_remaining(self):
        """Count of doors not yet opened"""
        opened = len(self.doors_opened) if self.doors_opened else 0
        return 24 - opened


class QRCodeToken(models.Model):
    """
    Per-user, per-door QR code tokens for physical gift unlocking.
    Each token is unique and can only be used once.
    """
    door = models.ForeignKey(
        AdventDoor,
        on_delete=models.CASCADE,
        related_name='qr_tokens'
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='advent_qr_tokens'
    )
    token = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        help_text="Unique token for QR code"
    )

    # Usage tracking
    is_used = models.BooleanField(
        default=False,
        help_text="Has this token been redeemed?"
    )
    used_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the token was redeemed"
    )

    # Optional expiration
    expires_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Optional expiration time for this token"
    )

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('door', 'user')
        verbose_name = "QR Code Token"
        verbose_name_plural = "🔑 5. QR Code Tokens"

    def __str__(self):
        status = "✅ Used" if self.is_used else "⏳ Pending"
        return f"Door {self.door.door_number} - {self.user.username} ({status})"

    def is_valid(self):
        """Check if this token can still be used"""
        if self.is_used:
            return False
        if self.expires_at and timezone.now() > self.expires_at:
            return False
        return True

    def redeem(self):
        """
        Redeem this token (mark as used).

        Returns:
            bool: True if successfully redeemed, False if already used or expired
        """
        if self.is_valid():
            self.is_used = True
            self.used_at = timezone.now()
            self.save()
            return True
        return False

    def get_qr_url(self, base_url="https://crush.lu"):
        """Get the full URL for the QR code"""
        return f"{base_url}/advent/qr/{self.token}/"
