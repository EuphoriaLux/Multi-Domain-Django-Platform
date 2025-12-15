from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from datetime import timedelta
import os
import uuid
from django.db.models import Q, F

# Lazy storage object for ImageField
# Using LazyObject ensures consistent migration state across environments
# The storage is only evaluated when actually accessed, not at import time
from django.utils.functional import LazyObject
from django.core.files.storage import default_storage


class CrushPhotoStorage(LazyObject):
    """
    Lazy storage backend for crush profile photos.
    - Production (AZURE_ACCOUNT_NAME set): CrushProfilePhotoStorage with SAS tokens
    - Development: Default storage (local filesystem)

    Using LazyObject ensures migrations are consistent across environments.
    """
    def _setup(self):
        if os.getenv('AZURE_ACCOUNT_NAME'):
            from crush_lu.storage import CrushProfilePhotoStorage
            self._wrapped = CrushProfilePhotoStorage()
        else:
            self._wrapped = default_storage


# Single instance used by all photo fields
crush_photo_storage = CrushPhotoStorage()


def user_photo_path(instance, filename):
    """
    Generate user-organized path for profile photos.
    Structure: users/{user_id}/photos/{uuid}_{filename}

    Benefits:
    - Easy to find all photos for a specific user
    - Simple GDPR deletion (delete user folder)
    - Better organization in Azure Storage Explorer
    - UUID prefix prevents filename conflicts
    """
    # Get file extension
    ext = os.path.splitext(filename)[1].lower()
    # Generate unique filename with UUID
    unique_filename = f"{uuid.uuid4().hex}{ext}"
    # Return path: users/{user_id}/photos/{unique_filename}
    return f"users/{instance.user.id}/photos/{unique_filename}"


def coach_photo_path(instance, filename):
    """
    Generate path for coach profile photos.
    Structure: coaches/{user_id}/{uuid}.{ext}
    """
    ext = os.path.splitext(filename)[1].lower()
    unique_filename = f"{uuid.uuid4().hex}{ext}"
    return f"coaches/{instance.user.id}/{unique_filename}"


def user_export_path(instance, filename):
    """
    Generate path for user data exports (GDPR compliance, profile exports).
    Structure: users/{user_id}/exports/{uuid}_{filename}

    Used for:
    - GDPR data export requests
    - Profile backup exports
    - Any user-generated exportable data

    Args:
        instance: Model instance with a 'user' attribute
        filename: Original filename

    Returns:
        str: Path in format users/{user_id}/exports/{uuid}_{filename}
    """
    ext = os.path.splitext(filename)[1].lower()
    # Keep original filename for clarity in exports
    base_name = os.path.splitext(os.path.basename(filename))[0]
    unique_filename = f"{uuid.uuid4().hex}_{base_name}{ext}"
    return f"users/{instance.user.id}/exports/{unique_filename}"


class SpecialUserExperience(models.Model):
    """
    Admin-configurable special user experience for VIP/special users.
    When a user with matching first_name and last_name logs in,
    they receive a personalized, unique Crush.lu experience.
    """
    first_name = models.CharField(
        max_length=150,
        help_text="First name to match (case-insensitive)"
    )
    last_name = models.CharField(
        max_length=150,
        help_text="Last name to match (case-insensitive)"
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Enable/disable this special experience"
    )

    # Customization options
    custom_welcome_title = models.CharField(
        max_length=200,
        default="Welcome to Your Special Journey",
        help_text="Custom welcome message title"
    )
    custom_welcome_message = models.TextField(
        default="Something magical awaits you...",
        help_text="Custom welcome message body"
    )
    custom_theme_color = models.CharField(
        max_length=7,
        default="#FF1493",
        help_text="Hex color code for custom theme (e.g., #FF1493 for deep pink)"
    )
    animation_style = models.CharField(
        max_length=20,
        choices=[
            ('hearts', 'Floating Hearts'),
            ('stars', 'Sparkling Stars'),
            ('roses', 'Falling Rose Petals'),
            ('fireworks', 'Fireworks'),
            ('aurora', 'Aurora Borealis'),
        ],
        default='hearts',
        help_text="Animation effect on welcome screen"
    )

    # Auto-approve and special permissions
    auto_approve_profile = models.BooleanField(
        default=True,
        help_text="Automatically approve this user's profile (skip coach review)"
    )
    skip_waitlist = models.BooleanField(
        default=True,
        help_text="Skip event waitlists - always get confirmed spot"
    )
    vip_badge = models.BooleanField(
        default=True,
        help_text="Display VIP badge on profile"
    )

    # Custom landing page URL (optional)
    custom_landing_url = models.CharField(
        max_length=200,
        blank=True,
        help_text="Optional: Custom landing page path (e.g., 'special-welcome')"
    )

    # Tracking
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_triggered_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Last time this special experience was triggered"
    )
    trigger_count = models.PositiveIntegerField(
        default=0,
        help_text="Number of times this experience has been triggered"
    )

    class Meta:
        verbose_name = "Special User Experience"
        verbose_name_plural = "✨ 1. Special User Experiences"
        unique_together = ['first_name', 'last_name']

    def __str__(self):
        return f"Special Experience for {self.first_name} {self.last_name}"

    def matches_user(self, user):
        """Check if this special experience matches the given user"""
        return (
            self.is_active and
            user.first_name.lower() == self.first_name.lower() and
            user.last_name.lower() == self.last_name.lower()
        )

    def trigger(self):
        """Mark this experience as triggered"""
        self.last_triggered_at = timezone.now()
        self.trigger_count += 1
        self.save(update_fields=['last_triggered_at', 'trigger_count'])

    @property
    def journey(self):
        """
        Backwards compatibility: return the Wonderland journey.
        Use this for code that expects the old OneToOne relationship.
        """
        return self.journeys.filter(journey_type='wonderland').first()

    @property
    def advent_calendar_journey(self):
        """Get the Advent Calendar journey for this user"""
        return self.journeys.filter(journey_type='advent_calendar').first()

    def get_journey(self, journey_type='wonderland'):
        """Get a specific journey by type"""
        return self.journeys.filter(journey_type=journey_type).first()

    def has_journey(self, journey_type):
        """Check if user has a specific journey type"""
        return self.journeys.filter(journey_type=journey_type).exists()


class CrushCoach(models.Model):
    """Crush coaches who review and approve profiles"""
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    bio = models.TextField(max_length=500, blank=True)
    specializations = models.CharField(
        max_length=200,
        blank=True,
        help_text="e.g., Young professionals, Students, 30+, etc."
    )
    # Coach profile photo (stored in same private container as user photos)
    photo = models.ImageField(
        upload_to=coach_photo_path,
        blank=True,
        null=True,
        storage=crush_photo_storage,
        help_text="Coach profile photo shown to users"
    )
    is_active = models.BooleanField(default=True)
    max_active_reviews = models.PositiveIntegerField(
        default=10,
        help_text="Maximum number of profiles this coach can review simultaneously"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Coach: {self.user.get_full_name() or self.user.username}"

    def get_active_reviews_count(self):
        return self.profilesubmission_set.filter(status='pending').count()

    def can_accept_reviews(self):
        return self.is_active and self.get_active_reviews_count() < self.max_active_reviews


class CrushProfile(models.Model):
    """User profile for Crush.lu platform"""

    GENDER_CHOICES = [
        ('M', 'Male'),
        ('F', 'Female'),
        ('NB', 'Non-binary'),
        ('O', 'Other'),
        ('P', 'Prefer not to say'),
    ]

    LOOKING_FOR_CHOICES = [
        ('friends', 'New Friends'),
        ('dating', 'Dating'),
        ('both', 'Both'),
        ('networking', 'Social Networking'),
    ]

    COMPLETION_STATUS_CHOICES = [
        ('not_started', 'Not Started'),
        ('step1', 'Step 1: Basic Info Saved'),
        ('step2', 'Step 2: About You Saved'),
        ('step3', 'Step 3: Photos Saved'),
        ('completed', 'Profile Completed'),
        ('submitted', 'Submitted for Review'),
    ]

    user = models.OneToOneField(User, on_delete=models.CASCADE)

    # Completion tracking
    completion_status = models.CharField(
        max_length=20,
        choices=COMPLETION_STATUS_CHOICES,
        default='not_started',
        help_text="Track which step user completed"
    )
    # Note: Screening call tracking has been consolidated into ProfileSubmission.review_call_completed
    # The Step 1 screening system was redundant and has been removed

    # Basic Info (Step 1 - REQUIRED for initial save)
    date_of_birth = models.DateField(null=True, blank=True)
    gender = models.CharField(max_length=2, choices=GENDER_CHOICES, blank=True)
    phone_number = models.CharField(max_length=20, blank=True)  # Required in form, not model
    location = models.CharField(max_length=100, blank=True, help_text="City/Region in Luxembourg")

    # Profile Content (Step 2 - Optional until completion)
    bio = models.TextField(max_length=500, blank=True, help_text="Tell us about yourself!")
    interests = models.TextField(
        max_length=300,
        blank=True,
        help_text="Your hobbies and interests (comma-separated)"
    )
    looking_for = models.CharField(
        max_length=20,
        choices=LOOKING_FOR_CHOICES,
        default='friends',
        blank=True
    )

    # Photos (using private storage in production with SAS tokens)
    # Path structure: users/{user_id}/photos/{uuid}.{ext}
    # Using lazy storage ensures consistent migration state across environments
    photo_1 = models.ImageField(
        upload_to=user_photo_path,
        blank=True,
        null=True,
        storage=crush_photo_storage
    )
    photo_2 = models.ImageField(
        upload_to=user_photo_path,
        blank=True,
        null=True,
        storage=crush_photo_storage
    )
    photo_3 = models.ImageField(
        upload_to=user_photo_path,
        blank=True,
        null=True,
        storage=crush_photo_storage
    )

    # Privacy Settings
    show_full_name = models.BooleanField(
        default=False,
        help_text="Show full name (if false, only first name is shown)"
    )
    show_exact_age = models.BooleanField(
        default=True,
        help_text="Show exact age (if false, show age range)"
    )
    blur_photos = models.BooleanField(
        default=False,
        help_text="Blur photos until mutual interest"
    )

    # Status
    is_approved = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    approved_at = models.DateTimeField(null=True, blank=True)

    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.get_full_name() or self.user.username}'s Crush Profile"

    @property
    def age(self):
        if not self.date_of_birth:
            return None
        today = timezone.now().date()
        return today.year - self.date_of_birth.year - (
            (today.month, today.day) < (self.date_of_birth.month, self.date_of_birth.day)
        )

    @property
    def age_range(self):
        age = self.age
        if age is None:
            return "Not specified"
        if age < 25:
            return "18-24"
        elif age < 30:
            return "25-29"
        elif age < 35:
            return "30-34"
        elif age < 40:
            return "35-39"
        else:
            return "40+"

    def get_age_range(self):
        """Method version of age_range for templates"""
        return self.age_range

    @property
    def display_name(self):
        if self.show_full_name:
            return self.user.get_full_name() or self.user.username
        return self.user.first_name or self.user.username.split('@')[0]

    @property
    def city(self):
        """Alias for location field"""
        return self.location


class ProfileSubmission(models.Model):
    """Track profile submissions for coach review"""

    STATUS_CHOICES = [
        ('pending', 'Pending Review'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
        ('revision', 'Needs Revision'),
    ]

    profile = models.ForeignKey(CrushProfile, on_delete=models.CASCADE)
    coach = models.ForeignKey(
        CrushCoach,
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending', db_index=True)

    # Review details
    coach_notes = models.TextField(blank=True, help_text="Internal notes from coach")
    feedback_to_user = models.TextField(
        blank=True,
        help_text="Feedback shown to user if revision needed"
    )

    # Screening call during review (required before approval)
    review_call_completed = models.BooleanField(
        default=False,
        help_text="Coach must complete screening call before approving profile"
    )
    review_call_date = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When coach completed the screening call"
    )
    review_call_notes = models.TextField(
        blank=True,
        help_text="Notes from coach's screening call during review"
    )

    # Timestamps
    submitted_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-submitted_at']

    def __str__(self):
        return f"{self.profile.user.username} - {self.get_status_display()}"

    def assign_coach(self):
        """Auto-assign to an available coach"""
        available_coach = CrushCoach.objects.filter(
            is_active=True
        ).annotate(
            active_reviews=models.Count('profilesubmission', filter=models.Q(profilesubmission__status='pending'))
        ).filter(
            active_reviews__lt=models.F('max_active_reviews')
        ).order_by('active_reviews').first()

        if available_coach:
            self.coach = available_coach
            self.save()
            return True
        return False


class CoachSession(models.Model):
    """Track interactions between coaches and users"""

    SESSION_TYPE_CHOICES = [
        ('onboarding', 'Onboarding Session'),
        ('feedback', 'Profile Feedback'),
        ('guidance', 'Dating Guidance'),
        ('followup', 'Follow-up'),
    ]

    coach = models.ForeignKey(CrushCoach, on_delete=models.CASCADE)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    session_type = models.CharField(max_length=20, choices=SESSION_TYPE_CHOICES)

    notes = models.TextField(help_text="Session notes and key points discussed")
    scheduled_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.coach} - {self.user.username} ({self.get_session_type_display()})"


class UserActivity(models.Model):
    """
    Tracks user activity and online status.
    Helps identify active vs inactive users and PWA usage.
    """
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name='activity',
        help_text="User being tracked"
    )

    # Activity timestamps
    last_seen = models.DateTimeField(
        help_text="Last time user made a request"
    )
    last_pwa_visit = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Last time user accessed via PWA (standalone mode)"
    )

    # PWA usage
    is_pwa_user = models.BooleanField(
        default=False,
        help_text="Has this user ever used the installed PWA?"
    )

    # Activity stats
    total_visits = models.PositiveIntegerField(
        default=0,
        help_text="Total number of visits/requests"
    )

    # Metadata
    first_seen = models.DateTimeField(
        auto_now_add=True,
        help_text="First time user was tracked"
    )

    class Meta:
        verbose_name = "User Activity"
        verbose_name_plural = "📊 User Activities"
        ordering = ['-last_seen']

    def __str__(self):
        return f"{self.user.username} - Last seen: {self.last_seen}"

    @property
    def is_online(self):
        """User is considered online if seen in last 5 minutes"""
        if not self.last_seen:
            return False
        return (timezone.now() - self.last_seen).seconds < 300

    @property
    def minutes_since_last_seen(self):
        """Minutes since last activity"""
        if not self.last_seen:
            return None
        return int((timezone.now() - self.last_seen).total_seconds() / 60)

    @property
    def is_active_user(self):
        """Active if seen in last 7 days"""
        if not self.last_seen:
            return False
        return (timezone.now() - self.last_seen).days < 7

    @property
    def uses_pwa(self):
        """Check if user actively uses PWA (visited via PWA in last 30 days)"""
        if not self.is_pwa_user or not self.last_pwa_visit:
            return False
        return (timezone.now() - self.last_pwa_visit).days < 30


class PushSubscription(models.Model):
    """
    Stores Web Push API subscription data for sending push notifications to PWA users.
    Each user can have multiple subscriptions (different devices/browsers).
    """
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='push_subscriptions',
        help_text="User who owns this push subscription"
    )

    # Push subscription data (from browser's PushManager API)
    endpoint = models.URLField(
        max_length=500,
        help_text="Push service endpoint URL"
    )
    p256dh_key = models.CharField(
        max_length=255,
        help_text="Public key for encryption (p256dh)"
    )
    auth_key = models.CharField(
        max_length=255,
        help_text="Authentication secret (auth)"
    )

    # Device/browser information (optional but helpful)
    user_agent = models.TextField(
        blank=True,
        help_text="Browser user agent string"
    )
    device_name = models.CharField(
        max_length=100,
        blank=True,
        help_text="Friendly device name (e.g., 'Android Chrome', 'iPhone Safari')"
    )

    # Notification preferences
    enabled = models.BooleanField(
        default=True,
        help_text="User can disable notifications without unsubscribing"
    )
    notify_new_messages = models.BooleanField(
        default=True,
        help_text="Notify about new connection messages"
    )
    notify_event_reminders = models.BooleanField(
        default=True,
        help_text="Notify about upcoming events"
    )
    notify_new_connections = models.BooleanField(
        default=True,
        help_text="Notify about new connection requests"
    )
    notify_profile_updates = models.BooleanField(
        default=True,
        help_text="Notify about profile approval status"
    )

    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_used_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Last time a notification was successfully sent"
    )
    failure_count = models.PositiveIntegerField(
        default=0,
        help_text="Number of consecutive failed deliveries (auto-delete after threshold)"
    )

    class Meta:
        unique_together = ('user', 'endpoint')
        ordering = ['-created_at']
        verbose_name = "Push Notification Subscription"
        verbose_name_plural = "🔔 Push Notification Subscriptions"

    def __str__(self):
        device = self.device_name or "Unknown Device"
        return f"{self.user.username} - {device}"

    def mark_success(self):
        """Mark successful notification delivery"""
        self.last_used_at = timezone.now()
        self.failure_count = 0
        self.save(update_fields=['last_used_at', 'failure_count'])

    def mark_failure(self):
        """Mark failed notification delivery (auto-delete after 5 failures)"""
        self.failure_count += 1
        if self.failure_count >= 5:
            # Subscription likely expired/invalid - delete it
            self.delete()
        else:
            self.save(update_fields=['failure_count'])
