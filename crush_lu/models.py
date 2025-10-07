from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from datetime import timedelta


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
        default='step1',
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

    # Photos
    photo_1 = models.ImageField(upload_to='crush_profiles/', blank=True, null=True)
    photo_2 = models.ImageField(upload_to='crush_profiles/', blank=True, null=True)
    photo_3 = models.ImageField(upload_to='crush_profiles/', blank=True, null=True)

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
        today = timezone.now().date()
        return today.year - self.date_of_birth.year - (
            (today.month, today.day) < (self.date_of_birth.month, self.date_of_birth.day)
        )

    @property
    def age_range(self):
        age = self.age
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
