from django.db import models
from django.contrib.auth.models import User
from django.core.validators import RegexValidator
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
        from django.core.files.storage.base import Storage
        storage = storages["crush_private"]
        # Validate the backend is a proper Storage instance (guards against
        # broken native extensions where AzureStorage falls back to object)
        if not isinstance(storage, Storage):
            return default_storage
        return storage
    except Exception:
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
    spoken_languages = models.JSONField(
        default=list,
        blank=True,
        help_text=_("Languages the coach can speak for profile reviews")
    )
    phone_number = models.CharField(
        max_length=20,
        blank=True,
        validators=[RegexValidator(
            regex=r'^\+?[\d\s\-().]{7,20}$',
            message=_("Enter a valid phone number (e.g., +352 621 123 456)."),
        )],
        help_text=_("Coach's direct phone number for WhatsApp and calls")
    )
    is_active = models.BooleanField(default=True)
    max_active_reviews = models.PositiveIntegerField(
        default=10,
        help_text=_("Maximum number of profiles this coach can review simultaneously")
    )

    # Hybrid Coach Review System (see plan: crush-lu-hybrid-cached-catmull.md)
    WORKING_MODE_CHOICES = [
        ('spontaneous', _('Spontaneous — I call users on my own schedule')),
        ('hybrid', _('Hybrid — I call users but also accept bookings')),
        ('booking', _('Booking-first — users pick a slot from my calendar')),
    ]
    working_mode = models.CharField(
        max_length=20,
        choices=WORKING_MODE_CHOICES,
        default='spontaneous',
        help_text=_("How this coach prefers to conduct screening calls")
    )
    availability_windows = models.JSONField(
        default=list,
        blank=True,
        help_text=_(
            "List of weekly availability windows, each {'day': 'tuesday', "
            "'start': '18:00', 'end': '21:00', 'label': 'Tuesday evenings'}"
        ),
    )
    is_away = models.BooleanField(
        default=False,
        help_text=_("Temporarily exclude this coach from new assignments")
    )
    away_until = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_("Optional end date for the away period. NULL = indefinite")
    )
    hybrid_features_enabled = models.BooleanField(
        default=False,
        help_text=_(
            "Per-coach opt-in flag for the Hybrid Coach Review System. "
            "Gated in addition to settings.HYBRID_COACH_SYSTEM_ENABLED."
        ),
    )

    created_at = models.DateTimeField(auto_now_add=True)

    LANGUAGE_DISPLAY = {
        "en": {"name": _("English"), "flag": "\U0001f1ec\U0001f1e7"},
        "de": {"name": _("Deutsch"), "flag": "\U0001f1e9\U0001f1ea"},
        "fr": {"name": _("Français"), "flag": "\U0001f1eb\U0001f1f7"},
        "lu": {"name": _("Lëtzebuergesch"), "flag": "\U0001f1f1\U0001f1fa"},
    }

    def __str__(self):
        return f"Coach: {self.user.get_full_name() or self.user.username}"

    @property
    def get_spoken_languages_display(self):
        """Return list of dicts with code/name/flag for each spoken language."""
        if not self.spoken_languages:
            return []
        return [
            {
                "code": code,
                "name": str(self.LANGUAGE_DISPLAY.get(code, {}).get("name", code)),
                "flag": self.LANGUAGE_DISPLAY.get(code, {}).get("flag", ""),
            }
            for code in self.spoken_languages
            if code in self.LANGUAGE_DISPLAY
        ]

    @property
    def whatsapp_number(self):
        """Return phone_number formatted for wa.me links (digits only, no +)."""
        if not self.phone_number:
            return ""
        import re
        return re.sub(r'[^\d]', '', self.phone_number)

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

    COMPLETION_STATUS_CHOICES = [
        ('not_started', _('Not Started')),
        ('step1', _('Step 1: Basic Info Saved')),
        ('step2', _('Step 2: About You Saved')),
        ('step3', _('Step 3: Photos Saved')),
        ('step4', _('Step 4: Coach Selected')),
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

    # Draft storage for profile creation
    draft_data = models.JSONField(
        default=dict,
        blank=True,
        help_text=_("Temporary storage for incomplete/invalid step data")
    )
    last_draft_saved = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_("Timestamp of last auto-save")
    )
    draft_expires_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_("Auto-delete inactive drafts after 30 days")
    )

    # Basic Info (Step 1 - REQUIRED for initial save)
    date_of_birth = models.DateField(null=True, blank=True)
    gender = models.CharField(max_length=2, choices=GENDER_CHOICES, blank=True)
    phone_number = models.CharField(
        max_length=20, blank=True, db_index=True,
        validators=[RegexValidator(
            regex=r'^\+[\d\s\-().]{7,20}$',
            message=_("Enter a valid phone number (e.g., +352 621 123 456)."),
        )],
    )  # Required in form, not model
    phone_verified = models.BooleanField(default=False, help_text=_("Whether phone was verified via SMS OTP"))
    phone_verified_at = models.DateTimeField(null=True, blank=True, help_text=_("When phone was verified"))
    phone_verification_uid = models.CharField(
        max_length=128,
        null=True,
        blank=True,
        db_index=True,
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
    # Ideal Crush Preferences (optional)
    preferred_age_min = models.PositiveSmallIntegerField(
        default=18,
        help_text=_("Minimum preferred age")
    )
    preferred_age_max = models.PositiveSmallIntegerField(
        default=99,
        help_text=_("Maximum preferred age")
    )
    preferred_genders = models.JSONField(
        default=list,
        blank=True,
        help_text=_("Gender codes the user is interested in")
    )

    FIRST_STEP_CHOICES = [
        ('i_initiate', _('I prefer to make the first step')),
        ('they_initiate', _('I prefer the other person to make the first step')),
        ('no_preference', _('No preference')),
    ]

    first_step_preference = models.CharField(
        max_length=20,
        choices=FIRST_STEP_CHOICES,
        blank=True,
        default='',
        help_text=_("Who should make the first step?")
    )

    # Matching: Qualities, Defects, and Sought Qualities
    qualities = models.ManyToManyField(
        "crush_lu.Trait",
        blank=True,
        related_name="profiles_as_quality",
        limit_choices_to={"trait_type": "quality"},
        help_text=_("Your top 5 qualities (max 5)"),
    )
    defects = models.ManyToManyField(
        "crush_lu.Trait",
        blank=True,
        related_name="profiles_as_defect",
        limit_choices_to={"trait_type": "defect"},
        help_text=_("Your top 5 defects (max 5)"),
    )
    sought_qualities = models.ManyToManyField(
        "crush_lu.Trait",
        blank=True,
        related_name="profiles_seeking",
        limit_choices_to={"trait_type": "quality"},
        help_text=_("Top 5 qualities you seek in a partner (max 5)"),
    )
    astro_enabled = models.BooleanField(
        default=True,
        help_text=_("Include zodiac compatibility in matching score"),
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

    # Outlook contact sync
    outlook_contact_id = models.CharField(
        max_length=255,
        blank=True,
        help_text=_("Microsoft Graph contact ID for Outlook sync")
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

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['phone_number'],
                condition=~models.Q(phone_number=''),
                name='unique_non_empty_phone_number',
            ),
        ]

    def __str__(self):
        return f"{self.user.get_full_name() or self.user.username}'s Crush Profile"

    @property
    def age(self):
        if not self.date_of_birth:
            return None
        # Defensive check: date_of_birth should be a date object but may be corrupted
        if not hasattr(self.date_of_birth, 'year'):
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
    def age_display(self):
        """Returns age string respecting show_exact_age privacy setting."""
        if self.age is None:
            return ""
        if self.show_exact_age:
            return str(self.age)
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

    def get_missing_fields(self):
        """
        Returns a list of missing required fields for profile completion.
        Used to show users what they need to fill in.
        """
        missing = []

        # Step 1: Basic Info (Required)
        if not self.date_of_birth:
            missing.append({
                'field': 'date_of_birth',
                'label': _('Date of Birth'),
                'step': 1
            })
        if not self.gender:
            missing.append({
                'field': 'gender',
                'label': _('Gender'),
                'step': 1
            })
        if not self.phone_number:
            missing.append({
                'field': 'phone_number',
                'label': _('Phone Number'),
                'step': 1
            })
        if not self.phone_verified:
            missing.append({
                'field': 'phone_verified',
                'label': _('Phone Verification'),
                'step': 1
            })
        if not self.location:
            missing.append({
                'field': 'location',
                'label': _('Location'),
                'step': 1
            })

        # Step 2: About You (bio and interests are optional)

        # Step 3: Photos (At least one required)
        if not self.photo_1:
            missing.append({
                'field': 'photo_1',
                'label': _('Profile Photo'),
                'step': 3
            })

        # Step 3: Event Languages (At least one required)
        if not self.event_languages:
            missing.append({
                'field': 'event_languages',
                'label': _('Event Languages'),
                'step': 3
            })

        return missing

    @property
    def is_profile_complete(self):
        """Check if all required fields are filled"""
        return len(self.get_missing_fields()) == 0

    def save(self, *args, **kwargs):
        """
        Override save to:
        1. Enforce phone verification protection at model level.
        2. Delete old photo blobs when photos are replaced to prevent orphans.
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

                # Clean up old photo blobs when replaced or cleared
                for field_name in ("photo_1", "photo_2", "photo_3"):
                    old_photo = getattr(old_instance, field_name)
                    new_photo = getattr(self, field_name)
                    if old_photo and old_photo.name != getattr(new_photo, "name", None):
                        try:
                            old_photo.storage.delete(old_photo.name)
                        except Exception:
                            pass  # Don't block save if cleanup fails
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
        ('recontact_coach', _('Recontact Coach Required')),
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
    review_call_checklist = models.JSONField(
        default=dict,
        blank=True,
        help_text=_("Structured checklist data from screening call")
    )
    screening_call_mode = models.CharField(
        max_length=20,
        choices=[
            ('legacy', _('Legacy 5-section')),
            ('calibration', _('Calibration 3-section')),
        ],
        default='legacy',
        help_text=_("Which call-checklist shape applies to this submission"),
    )

    # Pre-screening questionnaire (user-submitted, optional, fills in before call)
    pre_screening_responses = models.JSONField(
        default=dict,
        blank=True,
        help_text=_("User-submitted answers to pre-screening questionnaire"),
    )
    pre_screening_submitted_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        help_text=_("When the user finalized their pre-screening answers"),
    )
    pre_screening_version = models.PositiveIntegerField(
        default=0,
        help_text=_("Schema version the user answered (0 = not offered yet)"),
    )
    pre_screening_readiness_score = models.IntegerField(
        null=True,
        blank=True,
        help_text=_("Rule-based 0–10 readiness score, null if no pre-screening"),
    )
    pre_screening_flags = models.JSONField(
        default=list,
        blank=True,
        help_text=_("Flag identifiers surfaced to the Coach for attention"),
    )

    # Candidate-to-coach note (write-once, submitted during review wait)
    candidate_note = models.TextField(
        blank=True,
        help_text=_("Optional note from candidate to coach during review wait")
    )
    candidate_note_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_("When the candidate submitted their note")
    )

    # Timestamps
    submitted_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)

    # --- Hybrid Coach Review System fields (see plan) ---
    assigned_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        help_text=_("When a coach was assigned. Used as the SLA anchor."),
    )
    sla_deadline = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        help_text=_("When this submission must be reviewed by (assigned_at + 48h)."),
    )
    fallback_offered_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_("When the user was shown the self-booking fallback."),
    )
    escalated_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_("When this submission was auto-reassigned after SLA breach."),
    )
    recontact_started_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_("When status became 'recontact_coach' (used for 14-day expiry)."),
    )
    nudge_sent_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_("When the 36h coach nudge was sent (sentinel to prevent repeats)."),
    )

    # Paused state — orthogonal to `status` so we preserve recontact history.
    is_paused = models.BooleanField(
        default=False,
        help_text=_("User action required before the submission can proceed."),
    )
    paused_at = models.DateTimeField(null=True, blank=True)
    paused_reason = models.CharField(
        max_length=64,
        blank=True,
        help_text=_("Short machine-readable reason, e.g. 'recontact_timeout'."),
    )

    # Audit log for automated actions (task runs, nudges, escalations, …).
    # Schema: [{'type': str, 'at': iso, 'actor': 'system|coach:<id>|user:<id>',
    # 'details': {...}}, ...].
    system_actions = models.JSONField(default=list, blank=True)

    # User-facing self-booking link (issued when fallback_offered_at is set).
    booking_token = models.UUIDField(
        null=True,
        blank=True,
        unique=True,
        db_index=True,
        help_text=_("Opaque token used in booking URLs; 30-day expiry."),
    )
    booking_token_expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-submitted_at']
        indexes = [
            # Composite index for coach workload queries
            # Used by: assign_coach() and coach performance queries
            models.Index(fields=['coach', 'status'], name='crush_lu_prof_coach_status_idx'),
            # Pending submissions sorted by date (coach dashboard queue)
            models.Index(fields=['status', 'submitted_at'], name='crush_lu_prof_status_date_idx'),
            # Coach-assigned submissions filtered by status
            models.Index(fields=['status', 'coach'], name='crush_lu_prof_status_coach_idx'),
        ]

    def __str__(self):
        return f"{self.profile.user.username} - {self.get_status_display()}"

    @property
    def sla_state(self):
        """Current SLA bucket: ok / warning / urgent / breach / escalated.

        Drives coach-side urgency coloring. Users see `hybrid_user_state`.
        Returns 'ok' when no SLA deadline is set yet.
        """
        if self.escalated_at:
            return "escalated"
        if not self.sla_deadline:
            return "ok"
        now = timezone.now()
        if now >= self.sla_deadline:
            return "breach"
        remaining_hours = (self.sla_deadline - now).total_seconds() / 3600
        if remaining_hours <= 12:
            return "urgent"
        if remaining_hours <= 24:
            return "warning"
        return "ok"

    @property
    def hybrid_user_state(self):
        """User-facing hybrid-review state bucket.

        One of: just_submitted, coach_working, fallback_offered,
        escalated, in_recontact, paused. Used by profile_submitted.html
        to render a reassuring, actionable status banner. Does not
        replace the existing pending timeline — it augments it.
        """
        if self.is_paused:
            return "paused"
        if self.status == "recontact_coach":
            return "in_recontact"
        if self.escalated_at:
            return "escalated"
        if self.fallback_offered_at:
            return "fallback_offered"
        # Default pending branch: has a coach done anything yet?
        try:
            has_activity = self.call_attempts.exists()
        except Exception:
            has_activity = False
        hours_since = (timezone.now() - self.submitted_at).total_seconds() / 3600
        if has_activity or hours_since >= 24:
            return "coach_working"
        return "just_submitted"

    @property
    def recontact_days_remaining(self):
        """Days left before auto-pause when status is recontact_coach.

        Returns None if not in recontact, negative/zero if already past
        the 14-day window (Phase 6 will auto-pause on the next run).
        """
        if self.status != "recontact_coach" or not self.recontact_started_at:
            return None
        elapsed = (timezone.now() - self.recontact_started_at).days
        return 14 - elapsed

    def log_system_action(self, type_: str, actor: str = "system", **details):
        """Append an audit entry to system_actions.

        Uses a write-back pattern because JSONField mutations in place
        aren't persisted by Django. Caller is responsible for saving
        the parent with update_fields=['system_actions'] when batching.
        """
        entry = {
            "type": type_,
            "at": timezone.now().isoformat(),
            "actor": actor,
        }
        if details:
            entry["details"] = details
        actions = list(self.system_actions or [])
        actions.append(entry)
        self.system_actions = actions
        return entry

    def assign_coach(self):
        """Auto-assign to an available coach, preferring language matches"""
        available_coaches = CrushCoach.objects.filter(
            is_active=True
        ).annotate(
            active_reviews=models.Count('profilesubmission', filter=models.Q(profilesubmission__status='pending'))
        ).filter(
            active_reviews__lt=models.F('max_active_reviews')
        ).order_by('active_reviews')

        # Try language-matching coach first
        user_languages = getattr(self.profile, 'event_languages', None) or []
        if user_languages:
            for coach in available_coaches:
                coach_langs = coach.spoken_languages or []
                if set(coach_langs) & set(user_languages):
                    self.coach = coach
                    self.save()
                    return True

        # Fallback: any available coach (original behavior)
        available_coach = available_coaches.first()
        if available_coach:
            self.coach = available_coach
            self.save()
            return True
        return False


class CallAttempt(models.Model):
    """Track all call attempts (both successful and failed) for audit trail"""

    RESULT_CHOICES = [
        ('success', _('Call Completed')),
        ('failed', _('Call Failed')),
        ('sms_sent', _('SMS Sent')),
        ('event_invite_sms', _('Event Invite SMS')),
    ]

    FAILURE_REASON_CHOICES = [
        ('no_answer', _('No answer')),
        ('voicemail', _('Voicemail left')),
        ('wrong_number', _('Wrong number')),
        ('user_busy', _('User busy')),
        ('scheduled_callback', _('Scheduled callback')),
    ]

    submission = models.ForeignKey(
        'ProfileSubmission',
        on_delete=models.CASCADE,
        related_name='call_attempts',
        null=True,
        blank=True,
        help_text=_("The profile submission this call attempt is for")
    )
    profile = models.ForeignKey(
        'CrushProfile',
        on_delete=models.CASCADE,
        related_name='call_attempts',
        null=True,
        blank=True,
        help_text=_("Direct profile link (for profiles without a submission)")
    )
    attempt_date = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
        help_text=_("When the call attempt was made")
    )
    result = models.CharField(
        max_length=20,
        choices=RESULT_CHOICES,
        help_text=_("Whether the call succeeded or failed")
    )
    failure_reason = models.CharField(
        max_length=50,
        choices=FAILURE_REASON_CHOICES,
        null=True,
        blank=True,
        help_text=_("Reason why call failed (if applicable)")
    )
    notes = models.TextField(
        blank=True,
        help_text=_("Additional notes about the call attempt")
    )
    coach = models.ForeignKey(
        'CrushCoach',
        on_delete=models.SET_NULL,
        null=True,
        help_text=_("Coach who made the call attempt")
    )
    event = models.ForeignKey(
        'crush_lu.MeetupEvent',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text=_("Related event (for event invite SMS)")
    )

    class Meta:
        ordering = ['-attempt_date']
        verbose_name = _('Call Attempt')
        verbose_name_plural = _('Call Attempts')
        indexes = [
            models.Index(fields=['submission', '-attempt_date']),
        ]

    def __str__(self):
        return f"Call attempt for {self.submission.profile.user.get_full_name()} - {self.result} - {self.attempt_date.strftime('%Y-%m-%d %H:%M')}"

    @property
    def is_failed(self):
        return self.result == 'failed'


class ScreeningSlot(models.Model):
    """A screening-call time slot offered by a coach (Hybrid Review System).

    Slots can be pre-created by booking-first coaches or materialised on the
    fly from a hybrid coach's availability_windows when a user books. The
    user-facing booking token lives on ProfileSubmission; slots are addressed
    by PK once the user is authenticated via that token.
    """

    STATUS_CHOICES = [
        ('available', _('Available')),
        ('booked', _('Booked')),
        ('completed', _('Completed')),
        ('cancelled', _('Cancelled')),
        ('no_show', _('No-show')),
    ]

    MIN_DURATION = timedelta(minutes=10)
    MAX_DURATION = timedelta(minutes=60)

    coach = models.ForeignKey(
        'CrushCoach',
        on_delete=models.CASCADE,
        related_name='screening_slots',
    )
    submission = models.ForeignKey(
        'ProfileSubmission',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='booked_slots',
    )
    start_at = models.DateTimeField(db_index=True)
    end_at = models.DateTimeField()
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='available',
        db_index=True,
    )
    cancelled_reason = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['start_at']
        indexes = [
            models.Index(fields=['coach', 'status', 'start_at']),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(end_at__gt=models.F('start_at')),
                name='screening_slot_end_after_start',
            ),
        ]

    def __str__(self):
        return f"ScreeningSlot({self.coach_id}, {self.start_at:%Y-%m-%d %H:%M}, {self.status})"

    def clean(self):
        """Enforce duration bounds and no overlap with sibling slots."""
        from django.core.exceptions import ValidationError

        if self.start_at and self.end_at:
            duration = self.end_at - self.start_at
            if duration < self.MIN_DURATION:
                raise ValidationError(_("Slot must be at least 10 minutes long."))
            if duration > self.MAX_DURATION:
                raise ValidationError(_("Slot must be at most 60 minutes long."))

        if self.coach_id and self.start_at and self.end_at:
            overlap = ScreeningSlot.objects.filter(
                coach_id=self.coach_id,
                start_at__lt=self.end_at,
                end_at__gt=self.start_at,
            ).exclude(status__in=('cancelled', 'no_show'))
            if self.pk:
                overlap = overlap.exclude(pk=self.pk)
            if overlap.exists():
                raise ValidationError(_("This slot overlaps another for the same coach."))

    @classmethod
    def claim_for_submission(cls, *, coach_id, start_at, end_at, submission_token):
        """Atomically bind a slot (real or virtual) to a submission.

        Race-safe: takes the submission row lock first, then either locks
        the pre-existing slot or creates a fresh one. If the submission's
        currently-assigned coach differs from the booked coach, reassigns
        and logs the reassignment in system_actions.

        Returns (slot, submission). Raises ProfileSubmission.DoesNotExist
        on bad token, ValidationError on overlap or expiry issues.
        """
        from django.core.exceptions import ValidationError
        from django.db import transaction

        with transaction.atomic():
            submission = ProfileSubmission.objects.select_for_update().get(
                booking_token=submission_token
            )
            if (
                submission.booking_token_expires_at
                and submission.booking_token_expires_at < timezone.now()
            ):
                raise ValidationError(_("This booking link has expired."))

            # Defense in depth against tokens used after the submission moved
            # on (approved / rejected / call completed / paused). The view-level
            # `_resolve_token` already rejects the same states, but a
            # concurrent admin action between resolve and claim could flip them
            # — only this post-lock check sees the final state.
            if submission.status != "pending":
                raise ValidationError(_("This booking link is no longer valid."))
            if submission.review_call_completed or submission.is_paused:
                raise ValidationError(_("This booking link is no longer valid."))

            # One active booking per submission. Without this, a valid token
            # can claim multiple different slots and `book_screening` /
            # `cancel_booking` — which both read the first booked row — end up
            # with orphans. Users who want to change their slot must cancel
            # first via `cancel_booking`.
            if cls.objects.filter(
                submission=submission, status="booked"
            ).exists():
                raise ValidationError(
                    _(
                        "You already have a booked slot for this submission. "
                        "Please cancel it before picking a new time."
                    )
                )

            # Try to lock an existing 'available' slot at this exact time.
            existing = (
                cls.objects.select_for_update()
                .filter(
                    coach_id=coach_id,
                    start_at=start_at,
                    end_at=end_at,
                    status="available",
                )
                .first()
            )
            if existing:
                slot = existing
                slot.status = "booked"
                slot.submission = submission
                slot.save(update_fields=["status", "submission", "updated_at"])
            else:
                # Virtual slot — the (coach_id, start_at, end_at) tuple comes
                # from a hidden form field so we MUST re-verify it was actually
                # offered by `bookable_slots()`. Otherwise a tampered POST can
                # book any time on any coach, bypassing opt-in, availability
                # windows, past-time filter, and the 14-day horizon.
                coach = CrushCoach.objects.filter(
                    pk=coach_id, is_active=True
                ).first()
                if coach is None:
                    raise ValidationError(
                        _("That coach is not available for booking.")
                    )

                from crush_lu.services.slot_generator import bookable_slots

                offered = any(
                    candidate["coach_id"] == coach_id
                    and candidate["start_at"] == start_at
                    and candidate["end_at"] == end_at
                    for candidate in bookable_slots(coach)
                )
                if not offered:
                    raise ValidationError(
                        _(
                            "That time is no longer available. "
                            "Please pick another slot."
                        )
                    )

                slot = cls(
                    coach_id=coach_id,
                    submission=submission,
                    start_at=start_at,
                    end_at=end_at,
                    status="booked",
                )
                slot.full_clean()
                slot.save()

            # Reassign on-the-fly if user booked with a different coach.
            if submission.coach_id != coach_id:
                prev_coach = submission.coach_id
                submission.coach_id = coach_id
                submission.log_system_action(
                    "reassigned_via_booking",
                    actor=f"user:{submission.profile.user_id}",
                    from_coach=prev_coach,
                    to_coach=coach_id,
                )
                submission.save(update_fields=["coach", "system_actions"])

            return slot, submission


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
        db_index=True,
        help_text=_("Last time user made a request")
    )
    last_pwa_visit = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
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
    email_newsletter = models.BooleanField(
        default=True,  # ON by default - service-related announcements about events/community
        help_text=_("Newsletter emails about upcoming events and community news")
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
            'newsletter': self.email_newsletter,
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
        if self.email_newsletter:
            categories.append('newsletter')
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


class UserDataConsent(models.Model):
    """
    Tracks user consent for data processing across PowerUp (identity layer)
    and Crush.lu (profile layer).

    Two-tier consent architecture:
    1. PowerUp consent (identity): Implicit during account creation (User + Allauth)
    2. Crush.lu consent (profile): Explicit during profile creation
    """
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='data_consent')

    # PowerUp Layer Consent (User + Allauth)
    powerup_consent_given = models.BooleanField(
        default=False,
        help_text=_("User consents to PowerUp identity layer (User model + OAuth data)")
    )
    powerup_consent_date = models.DateTimeField(null=True, blank=True)
    powerup_consent_ip = models.GenericIPAddressField(null=True, blank=True)
    powerup_terms_version = models.CharField(max_length=10, default='1.0')

    # Crush.lu Layer Consent (CrushProfile + related data)
    crushlu_consent_given = models.BooleanField(
        default=False,
        help_text=_("User consents to Crush.lu profile layer (dating profile + photos)")
    )
    crushlu_consent_date = models.DateTimeField(null=True, blank=True)
    crushlu_consent_ip = models.GenericIPAddressField(null=True, blank=True)
    crushlu_terms_version = models.CharField(max_length=10, default='1.0')

    # Permanent ban from Crush.lu
    crushlu_banned = models.BooleanField(
        default=False,
        help_text=_("User is permanently banned from creating new Crush.lu profiles")
    )
    crushlu_ban_date = models.DateTimeField(null=True, blank=True)
    crushlu_ban_reason = models.CharField(
        max_length=50,
        choices=[
            ('user_deletion', _('User deleted profile')),
            ('admin_action', _('Admin action')),
            ('terms_violation', _('Terms violation')),
        ],
        null=True,
        blank=True
    )

    # Marketing consent (optional)
    marketing_consent = models.BooleanField(
        default=False,
        help_text=_("User consents to marketing communications")
    )
    marketing_consent_date = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("User Data Consent")
        verbose_name_plural = _("User Data Consents")

    def __str__(self):
        return f"Consent for {self.user.username}"

    def has_powerup_consent(self):
        """Check if user has given PowerUp consent"""
        return self.powerup_consent_given

    def has_crushlu_consent(self):
        """Check if user has given Crush.lu consent"""
        return self.crushlu_consent_given


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
