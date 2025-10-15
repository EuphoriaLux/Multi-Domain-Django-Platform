from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from datetime import timedelta
import os

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
        verbose_name_plural = "Special User Experiences"
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
    needs_screening_call = models.BooleanField(
        default=False,
        help_text="True after Step 1 - user needs coach screening call"
    )
    screening_call_scheduled = models.DateTimeField(null=True, blank=True)
    screening_call_completed = models.BooleanField(default=False)
    screening_notes = models.TextField(blank=True, help_text="Notes from screening call")

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
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')

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
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')

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
