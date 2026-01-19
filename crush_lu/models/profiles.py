from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from datetime import timedelta
import os
import uuid
from django.db.models import Q, F

# Storage selection for ImageField using Django 4.2+ storages utility
# Using storages.backends ensures consistent migration state across environments
# because Django stores the alias reference, not the evaluated storage instance
from django.core.files.storage import storages, default_storage


def get_crush_photo_storage():
    """
    Return the appropriate storage backend for crush profile photos.
    - Production: Uses 'crush_private' storage alias (CrushProfilePhotoStorage with SAS tokens)
    - Development: Uses default storage (local filesystem)

    Using the storages utility ensures Django's migration system sees a consistent
    callable reference, preventing false "model changes detected" warnings.
    """
    try:
        # Try to get the crush_private storage (defined in production STORAGES)
        return storages["crush_private"]
    except (KeyError, Exception):
        # Fall back to default storage in development
        return default_storage


# Callable used by all photo fields - Django calls this when needed
crush_photo_storage = get_crush_photo_storage


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
        help_text=_("First name to match (case-insensitive)")
    )
    last_name = models.CharField(
        max_length=150,
        help_text=_("Last name to match (case-insensitive)")
    )
    linked_user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='special_experiences',
        help_text=_("Direct link to user (bypasses name matching). Used for gifted journeys.")
    )
    is_active = models.BooleanField(
        default=True,
        help_text=_("Enable/disable this special experience")
    )

    # Customization options
    custom_welcome_title = models.CharField(
        max_length=200,
        default="Welcome to Your Special Journey",
        help_text=_("Custom welcome message title")
    )
    custom_welcome_message = models.TextField(
        default="Something magical awaits you...",
        help_text=_("Custom welcome message body")
    )
    custom_theme_color = models.CharField(
        max_length=7,
        default="#FF1493",
        help_text=_("Hex color code for custom theme (e.g., #FF1493 for deep pink)")
    )
    animation_style = models.CharField(
        max_length=20,
        choices=[
            ('hearts', _('Floating Hearts')),
            ('stars', _('Sparkling Stars')),
            ('roses', _('Falling Rose Petals')),
            ('fireworks', _('Fireworks')),
            ('aurora', _('Aurora Borealis')),
        ],
        default='hearts',
        help_text=_("Animation effect on welcome screen")
    )

    # Auto-approve and special permissions
    auto_approve_profile = models.BooleanField(
        default=True,
        help_text=_("Automatically approve this user's profile (skip coach review)")
    )
    skip_waitlist = models.BooleanField(
        default=True,
        help_text=_("Skip event waitlists - always get confirmed spot")
    )
    vip_badge = models.BooleanField(
        default=True,
        help_text=_("Display VIP badge on profile")
    )

    # Custom landing page URL (optional)
    custom_landing_url = models.CharField(
        max_length=200,
        blank=True,
        help_text=_("Optional: Custom landing page path (e.g., 'special-welcome')")
    )

    # Tracking
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_triggered_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_("Last time this special experience was triggered")
    )
    trigger_count = models.PositiveIntegerField(
        default=0,
        help_text=_("Number of times this experience has been triggered")
    )

    class Meta:
        verbose_name = _("Special User Experience")
        verbose_name_plural = _("Special User Experiences")
        constraints = [
            # For legacy name-matching: unique (first_name, last_name) when no linked_user
            models.UniqueConstraint(
                fields=['first_name', 'last_name'],
                condition=models.Q(linked_user__isnull=True),
                name='unique_name_when_no_linked_user'
            ),
            # Each user can only have one directly-linked special experience
            models.UniqueConstraint(
                fields=['linked_user'],
                condition=models.Q(linked_user__isnull=False),
                name='unique_linked_user'
            ),
        ]

    def __str__(self):
        return f"Special Experience for {self.first_name} {self.last_name}"

    def matches_user(self, user):
        """Check if this special experience matches the given user.

        Matches if:
        - linked_user is set and matches the user (direct link from gifts), OR
        - first_name and last_name match (legacy name-based matching)
        """
        if not self.is_active:
            return False

        # Direct link takes priority (used for gifted journeys)
        if self.linked_user_id and self.linked_user_id == user.id:
            return True

        # Fallback to name matching (legacy behavior)
        return (
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
        help_text=_("e.g., Young professionals, Students, 30+, etc.")
    )
    # Coach profile photo (stored in same private container as user photos)
    photo = models.ImageField(
        upload_to=coach_photo_path,
        blank=True,
        null=True,
        storage=crush_photo_storage,
        help_text=_("Coach profile photo shown to users")
    )
    is_active = models.BooleanField(default=True)
    max_active_reviews = models.PositiveIntegerField(
        default=10,
        help_text=_("Maximum number of profiles this coach can review simultaneously")
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
        ('M', _('Male')),
        ('F', _('Female')),
        ('NB', _('Non-binary')),
        ('O', _('Other')),
        ('P', _('Prefer not to say')),
    ]

    LOOKING_FOR_CHOICES = [
        ('friends', _('New Friends')),
        ('dating', _('Dating')),
        ('both', _('Both')),
        ('networking', _('Social Networking')),
    ]

    COMPLETION_STATUS_CHOICES = [
        ('not_started', _('Not Started')),
        ('step1', _('Step 1: Basic Info Saved')),
        ('step2', _('Step 2: About You Saved')),
        ('step3', _('Step 3: Photos Saved')),
        ('completed', _('Profile Completed')),
        ('submitted', _('Submitted for Review')),
    ]

    user = models.OneToOneField(User, on_delete=models.CASCADE)

    # Completion tracking
    completion_status = models.CharField(
        max_length=20,
        choices=COMPLETION_STATUS_CHOICES,
        default='not_started',
        help_text=_("Track which step user completed")
    )
    # Note: Screening call tracking has been consolidated into ProfileSubmission.review_call_completed
    # The Step 1 screening system was redundant and has been removed

    # Basic Info (Step 1 - REQUIRED for initial save)
    date_of_birth = models.DateField(null=True, blank=True)
    gender = models.CharField(max_length=2, choices=GENDER_CHOICES, blank=True)
    phone_number = models.CharField(max_length=20, blank=True)  # Required in form, not model
    phone_verified = models.BooleanField(default=False, help_text=_("Whether phone was verified via SMS OTP"))
    phone_verified_at = models.DateTimeField(null=True, blank=True, help_text=_("When phone was verified"))
    phone_verification_uid = models.CharField(
        max_length=128,
        null=True,
        blank=True,
        help_text=_("Firebase UID from phone verification (for audit/anti-replay)")
    )
    location = models.CharField(max_length=100, blank=True, help_text=_("City/Region in Luxembourg"))

    # Profile Content (Step 2 - Optional until completion)
    bio = models.TextField(max_length=500, blank=True, help_text=_("Tell us about yourself!"))
    interests = models.TextField(
        max_length=300,
        blank=True,
        help_text=_("Your hobbies and interests (comma-separated)")
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
        help_text=_("Show full name (if false, only first name is shown)")
    )
    show_exact_age = models.BooleanField(
        default=True,
        help_text=_("Show exact age (if false, show age range)")
    )
    blur_photos = models.BooleanField(
        default=False,
        help_text=_("Blur photos until mutual interest")
    )

    # Language Preference
    preferred_language = models.CharField(
        max_length=5,
        choices=[
            ('en', _('English')),
            ('de', _('Deutsch')),
            ('fr', _('Français')),
        ],
        default='en',
        help_text=_("Preferred language for emails and notifications")
    )

    # Event Languages (languages user can speak at in-person events)
    EVENT_LANGUAGE_CHOICES = [
        ('en', _('English')),
        ('de', _('Deutsch')),
        ('fr', _('Français')),
        ('lu', _('Lëtzebuergesch')),
    ]
    event_languages = models.JSONField(
        default=list,
        blank=True,
        help_text=_("Languages the user can speak at in-person events")
    )

    # Wallet passes
    apple_pass_serial = models.CharField(
        max_length=64,
        blank=True,
        help_text=_("Apple Wallet pass serial number")
    )
    apple_auth_token = models.CharField(
        max_length=64,
        blank=True,
        help_text=_("Apple Wallet authentication token")
    )
    google_wallet_object_id = models.CharField(
        max_length=128,
        blank=True,
        help_text=_("Google Wallet object ID")
    )
    show_photo_on_wallet = models.BooleanField(
        default=True,
        help_text=_("Show profile photo on wallet card")
    )

    # Referral Rewards
    MEMBERSHIP_TIER_CHOICES = [
        ('basic', _('Basic')),
        ('bronze', _('Bronze')),
        ('silver', _('Silver')),
        ('gold', _('Gold')),
    ]
    referral_points = models.PositiveIntegerField(
        default=0,
        help_text=_("Points earned from referrals")
    )
    membership_tier = models.CharField(
        max_length=20,
        choices=MEMBERSHIP_TIER_CHOICES,
        default='basic',
        help_text=_("Membership tier based on referral activity")
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

    def save(self, *args, **kwargs):
        """
        Override save to enforce phone verification protection at model level.
        Once a phone is verified, it cannot be changed without explicit reset.
        This is the single source of truth for phone protection logic.
        """
        if self.pk:  # Only on update, not create
            try:
                old_instance = CrushProfile.objects.get(pk=self.pk)
                if old_instance.phone_verified:
                    # Preserve verified phone data - cannot be changed
                    self.phone_number = old_instance.phone_number
                    self.phone_verified = old_instance.phone_verified
                    self.phone_verified_at = old_instance.phone_verified_at
                    self.phone_verification_uid = old_instance.phone_verification_uid
            except CrushProfile.DoesNotExist:
                pass
        super().save(*args, **kwargs)

    def reset_phone_verification(self):
        """
        Explicitly reset phone verification (admin/support use only).
        This is the only way to change a verified phone number.
        """
        self.phone_verified = False
        self.phone_verified_at = None
        self.phone_verification_uid = None
        # Use update_fields to bypass the save() protection
        super().save(update_fields=['phone_verified', 'phone_verified_at', 'phone_verification_uid'])


class ProfileSubmission(models.Model):
    """Track profile submissions for coach review"""

    STATUS_CHOICES = [
        ('pending', _('Pending Review')),
        ('approved', _('Approved')),
        ('rejected', _('Rejected')),
        ('revision', _('Needs Revision')),
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
    coach_notes = models.TextField(blank=True, help_text=_("Internal notes from coach"))
    feedback_to_user = models.TextField(
        blank=True,
        help_text=_("Feedback shown to user if revision needed")
    )

    # Screening call during review (required before approval)
    review_call_completed = models.BooleanField(
        default=False,
        help_text=_("Coach must complete screening call before approving profile")
    )
    review_call_date = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_("When coach completed the screening call")
    )
    review_call_notes = models.TextField(
        blank=True,
        help_text=_("Notes from coach's screening call during review")
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
        ('onboarding', _('Onboarding Session')),
        ('feedback', _('Profile Feedback')),
        ('guidance', _('Dating Guidance')),
        ('followup', _('Follow-up')),
    ]

    coach = models.ForeignKey(CrushCoach, on_delete=models.CASCADE)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    session_type = models.CharField(max_length=20, choices=SESSION_TYPE_CHOICES)

    notes = models.TextField(help_text=_("Session notes and key points discussed"))
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
        help_text=_("User being tracked")
    )

    # Activity timestamps
    last_seen = models.DateTimeField(
        help_text=_("Last time user made a request")
    )
    last_pwa_visit = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_("Last time user accessed via PWA (standalone mode)")
    )

    # PWA usage
    is_pwa_user = models.BooleanField(
        default=False,
        help_text=_("Has this user ever used the installed PWA?")
    )

    # Activity stats
    total_visits = models.PositiveIntegerField(
        default=0,
        help_text=_("Total number of visits/requests")
    )

    # Re-engagement tracking
    last_reminder_sent = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_("Last time a reminder email was sent to this user")
    )
    reminders_sent_count = models.PositiveIntegerField(
        default=0,
        help_text=_("Total number of reminder emails sent to this user")
    )

    # Metadata
    first_seen = models.DateTimeField(
        auto_now_add=True,
        help_text=_("First time user was tracked")
    )

    class Meta:
        verbose_name = _("User Activity")
        verbose_name_plural = _("User Activities")
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

    @property
    def days_inactive(self):
        """Days since last activity"""
        if not self.last_seen:
            return None
        return (timezone.now() - self.last_seen).days

    @property
    def needs_reengagement(self):
        """True if user is inactive and hasn't been contacted recently."""
        if not self.days_inactive or self.days_inactive < 7:
            return False
        if self.last_reminder_sent:
            days_since_reminder = (timezone.now() - self.last_reminder_sent).days
            return days_since_reminder >= 7
        return True


class PWADeviceInstallation(models.Model):
    """
    Tracks individual PWA installations across user devices.
    Each user can have multiple installations (phone, tablet, desktop).
    Admin-only visibility - not exposed to end users.
    """

    OS_CHOICES = [
        ('ios', _('iOS')),
        ('android', _('Android')),
        ('windows', _('Windows')),
        ('macos', _('macOS')),
        ('linux', _('Linux')),
        ('chromeos', _('ChromeOS')),
        ('unknown', _('Unknown')),
    ]

    FORM_FACTOR_CHOICES = [
        ('phone', _('Phone')),
        ('tablet', _('Tablet')),
        ('desktop', _('Desktop')),
        ('unknown', _('Unknown')),
    ]

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='pwa_installations',
        help_text=_("User who installed the PWA")
    )

    # Device identification
    device_fingerprint = models.CharField(
        max_length=64,
        db_index=True,
        help_text=_("Stable browser fingerprint for device identification")
    )

    # Device classification
    os_type = models.CharField(
        max_length=20,
        choices=OS_CHOICES,
        default='unknown',
        help_text=_("Operating system type")
    )
    form_factor = models.CharField(
        max_length=20,
        choices=FORM_FACTOR_CHOICES,
        default='unknown',
        help_text=_("Device form factor (phone, tablet, desktop)")
    )
    device_category = models.CharField(
        max_length=50,
        help_text=_("Combined category like 'Android Phone', 'Windows Desktop'")
    )
    browser = models.CharField(
        max_length=50,
        blank=True,
        help_text=_("Browser name (Chrome, Safari, Edge, etc.)")
    )

    # Raw data for debugging
    user_agent = models.TextField(
        blank=True,
        help_text=_("Full user agent string")
    )

    # Timestamps
    installed_at = models.DateTimeField(
        auto_now_add=True,
        help_text=_("When PWA was first installed on this device")
    )
    last_used_at = models.DateTimeField(
        auto_now=True,
        help_text=_("Last time PWA was used on this device")
    )

    class Meta:
        unique_together = ('user', 'device_fingerprint')
        ordering = ['-last_used_at']
        verbose_name = _("PWA Device Installation")
        verbose_name_plural = _("PWA Device Installations")

    def __str__(self):
        return f"{self.user.username} - {self.device_category}"

    @property
    def days_since_last_use(self):
        """Days since last PWA usage on this device."""
        return (timezone.now() - self.last_used_at).days

    @property
    def is_recently_active(self):
        """Active within last 7 days."""
        return self.days_since_last_use < 7


class PushSubscription(models.Model):
    """
    Stores Web Push API subscription data for sending push notifications to PWA users.
    Each user can have multiple subscriptions (different devices/browsers).
    """
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='push_subscriptions',
        help_text=_("User who owns this push subscription")
    )

    # Push subscription data (from browser's PushManager API)
    endpoint = models.URLField(
        max_length=500,
        help_text=_("Push service endpoint URL")
    )
    p256dh_key = models.CharField(
        max_length=255,
        help_text=_("Public key for encryption (p256dh)")
    )
    auth_key = models.CharField(
        max_length=255,
        help_text=_("Authentication secret (auth)")
    )

    # Device/browser information (optional but helpful)
    user_agent = models.TextField(
        blank=True,
        help_text=_("Browser user agent string")
    )
    device_name = models.CharField(
        max_length=100,
        blank=True,
        help_text=_("Friendly device name (e.g., 'Android Chrome', 'iPhone Safari')")
    )
    device_fingerprint = models.CharField(
        max_length=64,
        blank=True,
        db_index=True,
        help_text=_("Browser fingerprint hash for stable device identification across sessions")
    )

    # Notification preferences
    enabled = models.BooleanField(
        default=True,
        help_text=_("User can disable notifications without unsubscribing")
    )
    notify_new_messages = models.BooleanField(
        default=True,
        help_text=_("Notify about new connection messages")
    )
    notify_event_reminders = models.BooleanField(
        default=True,
        help_text=_("Notify about upcoming events")
    )
    notify_new_connections = models.BooleanField(
        default=True,
        help_text=_("Notify about new connection requests")
    )
    notify_profile_updates = models.BooleanField(
        default=True,
        help_text=_("Notify about profile approval status")
    )

    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_used_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_("Last time a notification was successfully sent")
    )
    failure_count = models.PositiveIntegerField(
        default=0,
        help_text=_("Number of consecutive failed deliveries (auto-delete after threshold)")
    )

    class Meta:
        unique_together = ('user', 'endpoint')
        ordering = ['-created_at']
        verbose_name = _("Push Notification Subscription")
        verbose_name_plural = _("Push Notification Subscriptions")

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


class EmailPreference(models.Model):
    """
    User email notification preferences with GDPR-compliant unsubscribe support.

    - All transactional/engagement emails ON by default
    - Marketing emails OFF by default (GDPR compliance)
    - Secure unsubscribe token for one-click unsubscribe without login
    """
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name='email_preference',
        help_text=_("User who owns these email preferences")
    )

    # Secure unsubscribe token (no login required for unsubscribe)
    unsubscribe_token = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        editable=False,
        help_text=_("Secure token for one-click unsubscribe links")
    )

    # Email categories - mirrors PushSubscription preferences
    email_profile_updates = models.BooleanField(
        default=True,
        help_text=_("Emails about profile approval, revision requests")
    )
    email_event_reminders = models.BooleanField(
        default=True,
        help_text=_("Reminders about upcoming events you're registered for")
    )
    email_new_connections = models.BooleanField(
        default=True,
        help_text=_("Notifications about new connection requests")
    )
    email_new_messages = models.BooleanField(
        default=True,
        help_text=_("Notifications about new messages from connections")
    )
    email_marketing = models.BooleanField(
        default=False,  # OFF by default - GDPR compliance
        help_text=_("Marketing emails, newsletters, promotions (requires explicit opt-in)")
    )

    # Master unsubscribe switch
    unsubscribed_all = models.BooleanField(
        default=False,
        help_text=_("User has unsubscribed from ALL emails")
    )

    # Tracking
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Email Preference")
        verbose_name_plural = _("Email Preferences")
        ordering = ['-updated_at']

    def __str__(self):
        status = "Unsubscribed" if self.unsubscribed_all else "Subscribed"
        return f"{self.user.email} - {status}"

    def can_send(self, email_type):
        """
        Check if we can send a specific type of email to this user.

        Args:
            email_type: One of 'profile_updates', 'event_reminders',
                       'new_connections', 'new_messages', 'marketing'

        Returns:
            bool: True if email can be sent, False otherwise
        """
        # Master switch - if unsubscribed from all, never send
        if self.unsubscribed_all:
            return False

        # Check specific category
        category_map = {
            'profile_updates': self.email_profile_updates,
            'event_reminders': self.email_event_reminders,
            'new_connections': self.email_new_connections,
            'new_messages': self.email_new_messages,
            'marketing': self.email_marketing,
        }

        return category_map.get(email_type, True)

    def get_enabled_categories(self):
        """Return list of enabled email categories (for admin display)"""
        categories = []
        if self.email_profile_updates:
            categories.append('profile_updates')
        if self.email_event_reminders:
            categories.append('event_reminders')
        if self.email_new_connections:
            categories.append('new_connections')
        if self.email_new_messages:
            categories.append('new_messages')
        if self.email_marketing:
            categories.append('marketing')
        return categories

    @classmethod
    def get_or_create_for_user(cls, user):
        """
        Get or create email preferences for a user.
        Used as a fallback when sending emails to ensure preferences exist.
        """
        preference, created = cls.objects.get_or_create(user=user)
        return preference


class CoachPushSubscription(models.Model):
    """
    Stores Web Push API subscription data for Crush Coaches.
    Completely separate from user PushSubscription to avoid conflicts.
    Each coach can have multiple subscriptions (different devices/browsers).
    """
    coach = models.ForeignKey(
        CrushCoach,
        on_delete=models.CASCADE,
        related_name='push_subscriptions',
        help_text=_("Coach who owns this push subscription")
    )

    # Push subscription data (from browser's PushManager API)
    endpoint = models.URLField(
        max_length=500,
        help_text=_("Push service endpoint URL")
    )
    p256dh_key = models.CharField(
        max_length=255,
        help_text=_("Public key for encryption (p256dh)")
    )
    auth_key = models.CharField(
        max_length=255,
        help_text=_("Authentication secret (auth)")
    )

    # Device/browser information
    user_agent = models.TextField(
        blank=True,
        help_text=_("Browser user agent string")
    )
    device_name = models.CharField(
        max_length=100,
        blank=True,
        help_text=_("Friendly device name (e.g., 'Android Chrome', 'iPhone Safari')")
    )
    device_fingerprint = models.CharField(
        max_length=64,
        blank=True,
        db_index=True,
        help_text=_("Browser fingerprint hash for stable device identification across sessions")
    )

    # Coach-specific notification preferences
    enabled = models.BooleanField(
        default=True,
        help_text=_("Coach can disable notifications without unsubscribing")
    )
    notify_new_submissions = models.BooleanField(
        default=True,
        help_text=_("Notify when new profile is assigned for review")
    )
    notify_screening_reminders = models.BooleanField(
        default=True,
        help_text=_("Notify about pending screening calls")
    )
    notify_user_responses = models.BooleanField(
        default=True,
        help_text=_("Notify when user submits revision")
    )
    notify_system_alerts = models.BooleanField(
        default=True,
        help_text=_("Notify about system/admin messages")
    )

    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_used_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_("Last time a notification was successfully sent")
    )
    failure_count = models.PositiveIntegerField(
        default=0,
        help_text=_("Number of consecutive failed deliveries (auto-delete after threshold)")
    )

    class Meta:
        unique_together = ('coach', 'endpoint')
        ordering = ['-created_at']
        verbose_name = _("Coach Push Subscription")
        verbose_name_plural = _("Coach Push Subscriptions")

    def __str__(self):
        device = self.device_name or "Unknown Device"
        return f"Coach {self.coach.user.get_full_name()} - {device}"

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


class ProfileReminder(models.Model):
    """
    Tracks profile completion reminder emails sent to users.
    Used to ensure we don't spam users with multiple reminders of the same type.
    """

    REMINDER_TYPE_CHOICES = [
        ('24h', _('24 Hour')),
        ('72h', _('72 Hour')),
        ('7d', _('7 Day Final')),
    ]

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='profile_reminders',
        help_text=_("User who received this reminder")
    )
    reminder_type = models.CharField(
        max_length=10,
        choices=REMINDER_TYPE_CHOICES,
        help_text=_("Type of reminder sent")
    )
    sent_at = models.DateTimeField(
        auto_now_add=True,
        help_text=_("When the reminder was sent")
    )

    class Meta:
        unique_together = ['user', 'reminder_type']  # One of each type per user
        ordering = ['-sent_at']
        verbose_name = _("Profile Reminder")
        verbose_name_plural = _("Profile Reminders")

    def __str__(self):
        return f"{self.user.email} - {self.get_reminder_type_display()} ({self.sent_at.date()})"
