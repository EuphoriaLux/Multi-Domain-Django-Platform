from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from datetime import timedelta
import os
import uuid

# Conditional import of private storage for production
if os.getenv('AZURE_ACCOUNT_NAME'):
    from .storage import CrushProfilePhotoStorage
    crush_photo_storage = CrushProfilePhotoStorage()
else:
    # In development, use default storage
    crush_photo_storage = None


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


class CrushCoach(models.Model):
    """Crush coaches who review and approve profiles"""
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    bio = models.TextField(max_length=500, blank=True)
    specializations = models.CharField(
        max_length=200,
        blank=True,
        help_text="e.g., Young professionals, Students, 30+, etc."
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
    photo_1 = models.ImageField(
        upload_to='crush_profiles/',
        blank=True,
        null=True,
        storage=crush_photo_storage
    )
    photo_2 = models.ImageField(
        upload_to='crush_profiles/',
        blank=True,
        null=True,
        storage=crush_photo_storage
    )
    photo_3 = models.ImageField(
        upload_to='crush_profiles/',
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


class MeetupEvent(models.Model):
    """Speed dating and social meetup events"""

    EVENT_TYPE_CHOICES = [
        ('speed_dating', 'Speed Dating'),
        ('mixer', 'Social Mixer'),
        ('activity', 'Activity Meetup'),
        ('themed', 'Themed Event'),
    ]

    title = models.CharField(max_length=200)
    description = models.TextField()
    event_type = models.CharField(max_length=20, choices=EVENT_TYPE_CHOICES)

    # Event Details
    location = models.CharField(max_length=200)
    address = models.TextField()
    date_time = models.DateTimeField()
    duration_minutes = models.PositiveIntegerField(default=120)

    # Capacity
    max_participants = models.PositiveIntegerField(default=20)
    min_age = models.PositiveIntegerField(default=18)
    max_age = models.PositiveIntegerField(default=99)

    # Registration
    registration_deadline = models.DateTimeField()
    registration_fee = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=0.00,
        help_text="Event fee in EUR"
    )

    # Status
    is_published = models.BooleanField(default=False)
    is_cancelled = models.BooleanField(default=False)

    # Private Invitation Event Settings
    is_private_invitation = models.BooleanField(
        default=False,
        help_text="Private invitation-only event (visible only to invited guests)"
    )
    invitation_code = models.CharField(
        max_length=100,
        unique=True,
        blank=True,
        null=True,
        help_text="Unique code for this private event"
    )
    invitation_expires_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When invitations for this event expire"
    )
    max_invited_guests = models.PositiveIntegerField(
        default=20,
        help_text="Maximum invited guests for private event"
    )

    # Invited Existing Users (for private events)
    invited_users = models.ManyToManyField(
        User,
        blank=True,
        related_name='invited_to_events',
        help_text="Existing users invited to this private event (no external invitation needed)"
    )

    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['date_time']

    def __str__(self):
        return f"{self.title} - {self.date_time.strftime('%Y-%m-%d %H:%M')}"

    @property
    def is_registration_open(self):
        now = timezone.now()
        return (
            self.is_published
            and not self.is_cancelled
            and now < self.registration_deadline
            and self.get_confirmed_count() < self.max_participants
        )

    @property
    def is_full(self):
        return self.get_confirmed_count() >= self.max_participants

    @property
    def spots_remaining(self):
        return max(0, self.max_participants - self.get_confirmed_count())

    def get_confirmed_count(self):
        return self.eventregistration_set.filter(
            status__in=['confirmed', 'attended']
        ).count()

    def get_waitlist_count(self):
        return self.eventregistration_set.filter(status='waitlist').count()


class EventRegistration(models.Model):
    """User registration for meetup events"""

    STATUS_CHOICES = [
        ('pending', 'Pending Payment'),
        ('confirmed', 'Confirmed'),
        ('waitlist', 'Waitlist'),
        ('cancelled', 'Cancelled'),
        ('attended', 'Attended'),
        ('no_show', 'No Show'),
    ]

    event = models.ForeignKey(MeetupEvent, on_delete=models.CASCADE)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending', db_index=True)

    # Additional info
    dietary_restrictions = models.CharField(max_length=200, blank=True)
    special_requests = models.TextField(blank=True)

    # Payment (if applicable)
    payment_confirmed = models.BooleanField(default=False)
    payment_date = models.DateTimeField(null=True, blank=True)

    # Timestamps
    registered_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('event', 'user')
        ordering = ['registered_at']

    def __str__(self):
        return f"{self.user.username} - {self.event.title} ({self.get_status_display()})"

    @property
    def can_make_connections(self):
        """Only attendees can make post-event connections"""
        return self.status == 'attended'


class EventInvitation(models.Model):
    """
    Private invitation for exclusive events.
    Tracks invitations sent to guests for invitation-only events.
    """

    STATUS_CHOICES = [
        ('pending', 'Invitation Sent'),
        ('accepted', 'Accepted'),
        ('declined', 'Declined'),
        ('attended', 'Attended'),
        ('expired', 'Expired'),
    ]

    APPROVAL_CHOICES = [
        ('pending_approval', 'Awaiting Approval'),
        ('approved', 'Approved to Attend'),
        ('rejected', 'Rejected'),
    ]

    event = models.ForeignKey(MeetupEvent, on_delete=models.CASCADE, related_name='invitations')
    guest_email = models.EmailField(help_text="Guest's email address")
    guest_first_name = models.CharField(max_length=100, help_text="Guest's first name")
    guest_last_name = models.CharField(max_length=100, help_text="Guest's last name")

    # Link to Special User Experience (optional - for VIP treatment)
    special_user = models.ForeignKey(
        SpecialUserExperience,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='event_invitations',
        help_text="Link this invitation to a Special User for VIP treatment (auto-fills from name/email match)"
    )

    # Invitation details
    invitation_code = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        editable=False,
        help_text="Unique invitation code (UUID)"
    )
    invited_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='invitations_sent',
        help_text="Coach/admin who sent the invitation"
    )

    # Status tracking
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='pending',
        help_text="Invitation status"
    )
    approval_status = models.CharField(
        max_length=20,
        choices=APPROVAL_CHOICES,
        default='pending_approval',
        help_text="Approval status (coach must approve before attendance)"
    )

    # Created user after acceptance
    created_user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='received_invitation',
        help_text="User account created when invitation was accepted"
    )

    # Timestamps
    invitation_sent_at = models.DateTimeField(auto_now_add=True)
    accepted_at = models.DateTimeField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)

    # Admin notes
    approval_notes = models.TextField(
        blank=True,
        help_text="Internal notes about approval/rejection"
    )
    coach_notes = models.TextField(
        blank=True,
        help_text="Coach notes about the guest"
    )

    class Meta:
        ordering = ['-invitation_sent_at']
        verbose_name = "Event Invitation"
        verbose_name_plural = "Event Invitations"

    def __str__(self):
        return f"{self.guest_first_name} {self.guest_last_name} → {self.event.title} ({self.get_status_display()})"

    def save(self, *args, **kwargs):
        """Generate invitation code on first save (handled by default now)"""
        super().save(*args, **kwargs)

    @property
    def is_expired(self):
        """Check if invitation has expired"""
        if self.event.invitation_expires_at and timezone.now() > self.event.invitation_expires_at:
            return True
        return False

    @property
    def invitation_url(self):
        """Generate the full invitation URL"""
        from django.urls import reverse
        return reverse('crush_lu:invitation_landing', kwargs={'code': self.invitation_code})


class EventConnection(models.Model):
    """Post-event connection requests between attendees"""

    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('accepted', 'Accepted'),
        ('declined', 'Declined'),
        ('coach_reviewing', 'Coach Reviewing'),
        ('coach_approved', 'Coach Approved - Ready to Share'),
        ('shared', 'Contact Info Shared'),
    ]

    # Who wants to connect
    requester = models.ForeignKey(User, on_delete=models.CASCADE, related_name='connection_requests_sent')
    # Who they want to connect with
    recipient = models.ForeignKey(User, on_delete=models.CASCADE, related_name='connection_requests_received')
    # Which event brought them together
    event = models.ForeignKey(MeetupEvent, on_delete=models.CASCADE)

    # Connection details
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    requester_note = models.TextField(
        max_length=300,
        blank=True,
        help_text="Optional note: What did you talk about? What interested you?"
    )

    # Coach facilitation
    assigned_coach = models.ForeignKey(
        CrushCoach,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="Coach who will facilitate this connection"
    )
    coach_notes = models.TextField(blank=True, help_text="Coach's guidance for the introduction")
    coach_introduction = models.TextField(
        blank=True,
        help_text="Personalized introduction message from coach"
    )

    # Mutual consent tracking
    requester_consents_to_share = models.BooleanField(
        default=False,
        help_text="Requester agrees to share contact info"
    )
    recipient_consents_to_share = models.BooleanField(
        default=False,
        help_text="Recipient agrees to share contact info"
    )

    # Timestamps
    requested_at = models.DateTimeField(auto_now_add=True)
    responded_at = models.DateTimeField(null=True, blank=True)
    coach_approved_at = models.DateTimeField(null=True, blank=True)
    shared_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ('requester', 'recipient', 'event')
        ordering = ['-requested_at']

    def __str__(self):
        return f"{self.requester.username} → {self.recipient.username} ({self.event.title})"

    @property
    def is_mutual(self):
        """Check if there's a mutual connection request"""
        return EventConnection.objects.filter(
            requester=self.recipient,
            recipient=self.requester,
            event=self.event
        ).exists()

    @property
    def can_share_contacts(self):
        """Both must consent and coach must approve"""
        return (
            self.requester_consents_to_share
            and self.recipient_consents_to_share
            and self.status == 'coach_approved'
        )

    def assign_coach(self):
        """Assign a coach to facilitate this connection"""
        # Try to get the coach who approved either profile
        requester_profile = CrushProfile.objects.get(user=self.requester)
        recipient_profile = CrushProfile.objects.get(user=self.recipient)

        # Prefer the coach who approved the requester
        requester_submission = ProfileSubmission.objects.filter(
            profile=requester_profile,
            status='approved'
        ).first()

        if requester_submission and requester_submission.coach:
            self.assigned_coach = requester_submission.coach
        else:
            # Fall back to any active coach
            self.assigned_coach = CrushCoach.objects.filter(is_active=True).first()

        self.save()


class ConnectionMessage(models.Model):
    """Coach-facilitated messages between connections"""

    connection = models.ForeignKey(EventConnection, on_delete=models.CASCADE, related_name='messages')
    sender = models.ForeignKey(User, on_delete=models.CASCADE)
    message = models.TextField(max_length=500)

    # Coach moderation
    is_coach_message = models.BooleanField(
        default=False,
        help_text="True if sent by the coach"
    )
    coach_approved = models.BooleanField(
        default=True,
        help_text="Coach can moderate messages if needed"
    )

    sent_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['sent_at']

    def __str__(self):
        return f"Message from {self.sender.username} at {self.sent_at}"


# ============================================================================
# INTERACTIVE JOURNEY SYSTEM - "The Wonderland of You"
# ============================================================================

class JourneyConfiguration(models.Model):
    """
    Main configuration for an interactive journey experience.
    Links to SpecialUserExperience to create personalized multi-chapter journeys.
    """
    special_experience = models.OneToOneField(
        SpecialUserExperience,
        on_delete=models.CASCADE,
        related_name='journey'
    )
    is_active = models.BooleanField(default=True)
    journey_name = models.CharField(
        max_length=200,
        default="The Wonderland of You",
        help_text="Name of this journey"
    )

    # Metadata
    total_chapters = models.IntegerField(
        default=6,
        help_text="Total number of chapters in this journey"
    )
    estimated_duration_minutes = models.IntegerField(
        default=90,
        help_text="Estimated total time to complete"
    )

    # Personalization data (for riddles/challenges)
    date_first_met = models.DateField(
        null=True,
        blank=True,
        help_text="Date you first met (for Chapter 1 riddle)"
    )
    location_first_met = models.CharField(
        max_length=200,
        blank=True,
        help_text="Where you first met"
    )

    # Journey completion
    certificate_enabled = models.BooleanField(
        default=True,
        help_text="Generate completion certificate"
    )
    final_message = models.TextField(
        help_text="The big reveal message shown in final chapter",
        default="You've completed every challenge and discovered every secret..."
    )

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Journey Configuration"
        verbose_name_plural = "🗺️ 2. Journey Configurations"

    def __str__(self):
        return f"{self.journey_name} (for {self.special_experience})"


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
    chapter_number = models.IntegerField(help_text="1, 2, 3, etc.")

    # Chapter metadata
    title = models.CharField(
        max_length=200,
        help_text='e.g., "Down the Rabbit Hole"'
    )
    theme = models.CharField(
        max_length=100,
        help_text='e.g., "Mystery & Curiosity"'
    )
    story_introduction = models.TextField(
        help_text="The story/narrative shown at chapter start"
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
        help_text="Estimated minutes to complete"
    )
    difficulty = models.CharField(
        max_length=20,
        choices=DIFFICULTY_CHOICES,
        default='easy'
    )

    # Unlock logic
    requires_previous_completion = models.BooleanField(
        default=True,
        help_text="Must complete previous chapter first"
    )

    # Completion message
    completion_message = models.TextField(
        help_text="Personal message shown after completing all challenges"
    )

    class Meta:
        ordering = ['chapter_number']
        unique_together = ('journey', 'chapter_number')
        verbose_name = "Journey Chapter"
        verbose_name_plural = "📖 3. Journey Chapters"

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
        help_text="Order within chapter (1, 2, 3...)"
    )
    challenge_type = models.CharField(
        max_length=30,
        choices=CHALLENGE_TYPES
    )

    # Challenge content
    question = models.TextField(
        help_text="The question/prompt/instructions"
    )

    # Flexible data storage for different challenge types
    options = models.JSONField(
        default=dict,
        blank=True,
        help_text='JSON data for options, choices, etc. ({"A": "option1", "B": "option2"})'
    )

    correct_answer = models.TextField(
        blank=True,
        help_text=(
            "The correct answer for QUIZ mode. "
            "**LEAVE BLANK for QUESTIONNAIRE mode** (all answers accepted & saved for review). "
            "Chapters 2/4/5 and types 'open_text'/'would_you_rather' auto-detect questionnaire mode."
        )
    )
    alternative_answers = models.JSONField(
        default=list,
        blank=True,
        help_text='Alternative acceptable answers ["answer1", "answer2"]'
    )

    # Hints system
    hint_1 = models.TextField(blank=True)
    hint_1_cost = models.IntegerField(default=20, help_text="Points deducted for hint 1")
    hint_2 = models.TextField(blank=True)
    hint_2_cost = models.IntegerField(default=50, help_text="Points deducted for hint 2")
    hint_3 = models.TextField(blank=True)
    hint_3_cost = models.IntegerField(default=80, help_text="Points deducted for hint 3")

    # Scoring
    points_awarded = models.IntegerField(
        default=100,
        help_text="Points for correct answer (before hint deductions)"
    )

    # Feedback
    success_message = models.TextField(
        help_text="Personal message shown when user answers correctly"
    )

    class Meta:
        ordering = ['challenge_order']
        verbose_name = "Journey Challenge"
        verbose_name_plural = "🎯 4. Journey Challenges"

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
        help_text="Text content (poem, letter, caption, etc.)"
    )

    # Media uploads (use existing Crush.lu private storage)
    photo = models.ImageField(
        upload_to='journey_rewards/',
        blank=True,
        null=True,
        storage=crush_photo_storage if crush_photo_storage else None
    )
    audio_file = models.FileField(
        upload_to='journey_rewards/audio/',
        blank=True,
        null=True,
        storage=crush_photo_storage if crush_photo_storage else None
    )
    video_file = models.FileField(
        upload_to='journey_rewards/video/',
        blank=True,
        null=True,
        storage=crush_photo_storage if crush_photo_storage else None
    )

    # For puzzles
    puzzle_pieces = models.IntegerField(
        default=16,
        help_text="Number of jigsaw pieces (4x4=16, 5x4=20, 6x5=30)"
    )

    class Meta:
        verbose_name = "Journey Reward"
        verbose_name_plural = "🎁 5. Journey Rewards"

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
        help_text="Total time spent in journey"
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
        verbose_name = "Journey Progress"
        verbose_name_plural = "📊 6. Journey Progress (User Tracking)"

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
        verbose_name = "Chapter Progress"
        verbose_name_plural = "📈 7. Chapter Progress (User Tracking)"

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
        help_text='List of hint numbers used [1, 2, 3]'
    )
    points_earned = models.IntegerField(default=0)

    # Timing
    attempted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-attempted_at']
        verbose_name = "Challenge Attempt"
        verbose_name_plural = "🎮 8. Challenge Attempts (User Answers)"

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
        help_text='List of unlocked piece indices [0, 1, 5, 7, ...]'
    )
    points_spent = models.IntegerField(default=0)
    is_completed = models.BooleanField(default=False)

    # Timestamps
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ('journey_progress', 'reward')
        verbose_name = "Reward Progress"
        verbose_name_plural = "🏆 9. Reward Progress (Puzzle Tracking)"

    def __str__(self):
        completion = "✅" if self.is_completed else f"{len(self.unlocked_pieces)}/16"
        return f"{self.journey_progress.user.username} - {self.reward.title} ({completion})"


# ============================================================================
# END INTERACTIVE JOURNEY SYSTEM
# ============================================================================


class GlobalActivityOption(models.Model):
    """
    Global activity options used across all Crush events.
    These are defined once and reused for all events - no need to recreate per event.
    """

    ACTIVITY_TYPE_CHOICES = [
        ('presentation_style', 'Presentation Style (Phase 2)'),
        ('speed_dating_twist', 'Speed Dating Twist (Phase 3)'),
    ]

    activity_type = models.CharField(max_length=20, choices=ACTIVITY_TYPE_CHOICES)
    activity_variant = models.CharField(
        max_length=20,
        unique=True,
        help_text="Unique identifier (e.g., 'music', 'spicy_questions')"
    )
    display_name = models.CharField(max_length=200)
    description = models.TextField()
    is_active = models.BooleanField(
        default=True,
        help_text="Inactive options won't appear in voting"
    )
    sort_order = models.PositiveIntegerField(
        default=0,
        help_text="Display order in voting UI"
    )

    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['activity_type', 'sort_order', 'display_name']
        verbose_name = 'Global Activity Option'
        verbose_name_plural = 'Global Activity Options'

    def __str__(self):
        return f"{self.get_activity_type_display()}: {self.display_name}"


class EventActivityOption(models.Model):
    """Activity options available for event voting - Two categories"""

    ACTIVITY_TYPE_CHOICES = [
        ('presentation_style', 'Presentation Style (Phase 2)'),
        ('speed_dating_twist', 'Speed Dating Twist (Phase 3)'),
    ]

    ACTIVITY_VARIANT_CHOICES = [
        # Presentation Style variants (Phase 2)
        ('music', 'With Favorite Music'),
        ('questions', '5 Predefined Questions'),
        ('picture_story', 'Share Favorite Picture & Story'),
        # Speed Dating Twist variants (Phase 3)
        ('spicy_questions', 'Spicy Questions First'),
        ('forbidden_word', 'Forbidden Word Challenge'),
        ('algorithm_extended', 'Algorithm\'s Choice Extended Time'),
    ]

    event = models.ForeignKey(MeetupEvent, on_delete=models.CASCADE, related_name='activity_options')
    activity_type = models.CharField(max_length=20, choices=ACTIVITY_TYPE_CHOICES)
    activity_variant = models.CharField(
        max_length=20,
        choices=ACTIVITY_VARIANT_CHOICES,
        blank=True,
        help_text="Sub-option for the activity"
    )
    display_name = models.CharField(
        max_length=200,
        help_text="e.g., 'Speed Dating - Random Order'"
    )
    description = models.TextField(help_text="Explanation of what this activity entails")
    vote_count = models.PositiveIntegerField(default=0)
    is_winner = models.BooleanField(default=False)

    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['activity_type', 'activity_variant']
        unique_together = ('event', 'activity_type', 'activity_variant')

    def __str__(self):
        return f"{self.event.title} - {self.display_name}"


class EventActivityVote(models.Model):
    """Individual votes from attendees for event activities (one vote per category)"""

    event = models.ForeignKey(MeetupEvent, on_delete=models.CASCADE, related_name='activity_votes')
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    selected_option = models.ForeignKey(GlobalActivityOption, on_delete=models.CASCADE)
    voted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        # Each user can vote once PER CATEGORY (presentation_style AND speed_dating_twist)
        unique_together = ('event', 'user', 'selected_option')
        ordering = ['-voted_at']

    def __str__(self):
        return f"{self.user.username} voted for {self.selected_option.display_name}"


class EventVotingSession(models.Model):
    """Manages voting session state for each event"""

    event = models.OneToOneField(MeetupEvent, on_delete=models.CASCADE, related_name='voting_session')
    voting_start_time = models.DateTimeField(help_text="Event start time + 15 minutes")
    voting_end_time = models.DateTimeField(help_text="Voting start time + 30 minutes")
    is_active = models.BooleanField(default=False)
    total_votes = models.PositiveIntegerField(default=0)

    # Track winners for both categories
    winning_presentation_style = models.ForeignKey(
        GlobalActivityOption,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='won_presentation_events',
        limit_choices_to={'activity_type': 'presentation_style'}
    )
    winning_speed_dating_twist = models.ForeignKey(
        GlobalActivityOption,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='won_speed_dating_events',
        limit_choices_to={'activity_type': 'speed_dating_twist'}
    )

    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Voting Session for {self.event.title}"

    def save(self, *args, **kwargs):
        # Auto-calculate voting times if not set
        if not self.voting_start_time:
            self.voting_start_time = self.event.date_time + timedelta(minutes=15)
        if not self.voting_end_time:
            self.voting_end_time = self.voting_start_time + timedelta(minutes=30)
        super().save(*args, **kwargs)

    @property
    def is_voting_open(self):
        """Check if voting window is currently open"""
        now = timezone.now()
        return (
            self.is_active
            and self.voting_start_time <= now <= self.voting_end_time
        )

    @property
    def time_until_start(self):
        """Seconds until voting starts (negative if already started)"""
        return (self.voting_start_time - timezone.now()).total_seconds()

    @property
    def time_remaining(self):
        """Seconds remaining in voting window (0 if not started or ended)"""
        now = timezone.now()
        if now < self.voting_start_time:
            return 0
        if now > self.voting_end_time:
            return 0
        return (self.voting_end_time - now).total_seconds()

    def start_voting(self):
        """Activate voting session"""
        self.is_active = True
        self.save()

    def end_voting(self):
        """End voting and calculate winner"""
        self.is_active = False
        self.calculate_winner()
        self.initialize_presentation_queue()
        self.save()

    def calculate_winner(self):
        """Determine winning global activity option for each category"""
        from django.db.models import Count

        # Count votes for each GlobalActivityOption for presentation style
        presentation_votes = EventActivityVote.objects.filter(
            event=self.event,
            selected_option__activity_type='presentation_style'
        ).values('selected_option').annotate(
            vote_count=Count('id')
        ).order_by('-vote_count')

        if presentation_votes:
            winner_id = presentation_votes[0]['selected_option']
            self.winning_presentation_style = GlobalActivityOption.objects.get(id=winner_id)

        # Count votes for each GlobalActivityOption for speed dating twist
        twist_votes = EventActivityVote.objects.filter(
            event=self.event,
            selected_option__activity_type='speed_dating_twist'
        ).values('selected_option').annotate(
            vote_count=Count('id')
        ).order_by('-vote_count')

        if twist_votes:
            winner_id = twist_votes[0]['selected_option']
            self.winning_speed_dating_twist = GlobalActivityOption.objects.get(id=winner_id)

    def initialize_presentation_queue(self):
        """Initialize presentation queue with all confirmed/attended users in random order"""
        from django.contrib.auth.models import User
        import random

        # Get all confirmed/attended users for this event
        attendees = User.objects.filter(
            eventregistration__event=self.event,
            eventregistration__status__in=['confirmed', 'attended']
        ).distinct()

        # Create a shuffled list of attendees
        attendee_list = list(attendees)
        random.shuffle(attendee_list)

        # Create presentation queue entries
        for order, user in enumerate(attendee_list, start=1):
            PresentationQueue.objects.get_or_create(
                event=self.event,
                user=user,
                defaults={'presentation_order': order}
            )


class PresentationQueue(models.Model):
    """Manages the order and status of presentations during Phase 2"""

    STATUS_CHOICES = [
        ('waiting', 'Waiting to Present'),
        ('presenting', 'Currently Presenting'),
        ('completed', 'Presentation Completed'),
        ('skipped', 'Skipped'),
    ]

    event = models.ForeignKey(MeetupEvent, on_delete=models.CASCADE, related_name='presentation_queue')
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    presentation_order = models.PositiveIntegerField(help_text="Order in queue (1, 2, 3...)")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='waiting')

    # Timing
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['event', 'presentation_order']
        unique_together = ('event', 'user')

    def __str__(self):
        return f"{self.event.title} - #{self.presentation_order}: {self.user.username}"

    @property
    def duration_seconds(self):
        """Calculate how long the presentation took"""
        if self.started_at and self.completed_at:
            return (self.completed_at - self.started_at).total_seconds()
        return None


class PresentationRating(models.Model):
    """Anonymous 1-5 star ratings for presentations"""

    event = models.ForeignKey(MeetupEvent, on_delete=models.CASCADE, related_name='presentation_ratings')
    presenter = models.ForeignKey(User, on_delete=models.CASCADE, related_name='presentations_received')
    rater = models.ForeignKey(User, on_delete=models.CASCADE, related_name='presentations_given')
    rating = models.PositiveSmallIntegerField(
        help_text="Rating from 1-5 stars",
        choices=[(i, f"{i} Star{'s' if i > 1 else ''}") for i in range(1, 6)]
    )

    # Metadata
    rated_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('event', 'presenter', 'rater')
        ordering = ['-rated_at']

    def __str__(self):
        return f"{self.rater.username} rated {self.presenter.username}: {self.rating}★"

    @staticmethod
    def get_average_rating(event, presenter):
        """Calculate average rating for a presenter"""
        ratings = PresentationRating.objects.filter(
            event=event,
            presenter=presenter
        ).aggregate(avg=models.Avg('rating'))
        return ratings['avg'] or 0

    @staticmethod
    def get_mutual_rating_score(event, user1, user2):
        """Get mutual rating score between two users (for pairing algorithm)"""
        try:
            rating1 = PresentationRating.objects.get(event=event, presenter=user2, rater=user1).rating
        except PresentationRating.DoesNotExist:
            rating1 = 0

        try:
            rating2 = PresentationRating.objects.get(event=event, presenter=user1, rater=user2).rating
        except PresentationRating.DoesNotExist:
            rating2 = 0

        # Return average of mutual ratings (higher score = better match)
        if rating1 > 0 and rating2 > 0:
            return (rating1 + rating2) / 2
        return 0


class SpeedDatingPair(models.Model):
    """Speed dating pairs generated from Phase 2 ratings"""

    event = models.ForeignKey(MeetupEvent, on_delete=models.CASCADE, related_name='speed_dating_pairs')
    user1 = models.ForeignKey(User, on_delete=models.CASCADE, related_name='speed_dating_as_user1')
    user2 = models.ForeignKey(User, on_delete=models.CASCADE, related_name='speed_dating_as_user2')
    round_number = models.PositiveIntegerField(help_text="Which speed dating round (1, 2, 3...)")
    mutual_rating_score = models.FloatField(
        help_text="Combined rating score from Phase 2",
        default=0
    )
    is_top_match = models.BooleanField(
        default=False,
        help_text="True if this is user's #1 rated match (gets extended time)"
    )

    # Timing
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['event', 'round_number']
        unique_together = ('event', 'user1', 'user2', 'round_number')

    def __str__(self):
        return f"{self.event.title} - Round {self.round_number}: {self.user1.username} ↔ {self.user2.username}"

    @property
    def duration_minutes(self):
        """Standard duration is 5 minutes, extended matches get more"""
        if self.is_top_match:
            # Check if "Algorithm's Choice Extended" won voting
            twist = self.event.activity_options.filter(
                activity_type='speed_dating_twist',
                activity_variant='algorithm_extended',
                is_winner=True
            ).exists()
            return 8 if twist else 5  # 8 min for top matches, 5 min standard
        return 5


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
