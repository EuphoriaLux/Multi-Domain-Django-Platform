from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from datetime import timedelta
import uuid
from .profiles import SpecialUserExperience

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
        help_text=_("Event fee in EUR")
    )

    # Status
    is_published = models.BooleanField(default=False)
    is_cancelled = models.BooleanField(default=False)

    # Private Invitation Event Settings
    is_private_invitation = models.BooleanField(
        default=False,
        help_text=_("Private invitation-only event (visible only to invited guests)")
    )
    invitation_code = models.CharField(
        max_length=100,
        unique=True,
        blank=True,
        null=True,
        help_text=_("Unique code for this private event")
    )
    invitation_expires_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_("When invitations for this event expire")
    )
    max_invited_guests = models.PositiveIntegerField(
        default=20,
        help_text=_("Maximum invited guests for private event")
    )

    # Invited Existing Users (for private events)
    invited_users = models.ManyToManyField(
        User,
        blank=True,
        related_name='invited_to_events',
        help_text=_("Existing users invited to this private event (no external invitation needed)")
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

    @property
    def end_time(self):
        """Calculate event end time based on start time and duration."""
        return self.date_time + timedelta(minutes=self.duration_minutes)

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
        indexes = [
            # Optimize COUNT queries for get_confirmed_count() and get_waitlist_count()
            models.Index(fields=['event', 'status'], name='eventregistration_event_status'),
        ]

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
    guest_email = models.EmailField(help_text=_("Guest's email address"))
    guest_first_name = models.CharField(max_length=100, help_text=_("Guest's first name"))
    guest_last_name = models.CharField(max_length=100, help_text=_("Guest's last name"))

    # Link to Special User Experience (optional - for VIP treatment)
    special_user = models.ForeignKey(
        SpecialUserExperience,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='event_invitations',
        help_text=_("Link this invitation to a Special User for VIP treatment (auto-fills from name/email match)")
    )

    # Invitation details
    invitation_code = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        editable=False,
        help_text=_("Unique invitation code (UUID)")
    )
    invited_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='invitations_sent',
        help_text=_("Coach/admin who sent the invitation")
    )

    # Status tracking
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='pending',
        help_text=_("Invitation status")
    )
    approval_status = models.CharField(
        max_length=20,
        choices=APPROVAL_CHOICES,
        default='pending_approval',
        help_text=_("Approval status (coach must approve before attendance)")
    )

    # Created user after acceptance
    created_user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='received_invitation',
        help_text=_("User account created when invitation was accepted")
    )

    # Timestamps
    invitation_sent_at = models.DateTimeField(auto_now_add=True)
    accepted_at = models.DateTimeField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)

    # Admin notes
    approval_notes = models.TextField(
        blank=True,
        help_text=_("Internal notes about approval/rejection")
    )
    coach_notes = models.TextField(
        blank=True,
        help_text=_("Coach notes about the guest")
    )

    class Meta:
        ordering = ['-invitation_sent_at']
        verbose_name = _("Event Invitation")
        verbose_name_plural = _("Event Invitations")

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
        help_text=_("Unique identifier (e.g., 'music', 'spicy_questions')")
    )
    display_name = models.CharField(max_length=200)
    description = models.TextField()
    is_active = models.BooleanField(
        default=True,
        help_text=_("Inactive options won't appear in voting")
    )
    sort_order = models.PositiveIntegerField(
        default=0,
        help_text=_("Display order in voting UI")
    )

    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['activity_type', 'sort_order', 'display_name']
        verbose_name = _('Global Activity Option')
        verbose_name_plural = _('Global Activity Options')

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
        help_text=_("Sub-option for the activity")
    )
    display_name = models.CharField(
        max_length=200,
        help_text=_("e.g., 'Speed Dating - Random Order'")
    )
    description = models.TextField(help_text=_("Explanation of what this activity entails"))
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
    presentation_order = models.PositiveIntegerField(help_text=_("Order in queue (1, 2, 3...)"))
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


class EventVotingSession(models.Model):
    """Manages voting session state for each event"""

    event = models.OneToOneField(MeetupEvent, on_delete=models.CASCADE, related_name='voting_session')
    voting_start_time = models.DateTimeField(help_text=_("Event start time + 15 minutes"))
    voting_end_time = models.DateTimeField(help_text=_("Voting start time + 30 minutes"))
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


class PresentationRating(models.Model):
    """Anonymous 1-5 star ratings for presentations"""

    event = models.ForeignKey(MeetupEvent, on_delete=models.CASCADE, related_name='presentation_ratings')
    presenter = models.ForeignKey(User, on_delete=models.CASCADE, related_name='presentations_received')
    rater = models.ForeignKey(User, on_delete=models.CASCADE, related_name='presentations_given')
    rating = models.PositiveSmallIntegerField(
        help_text=_("Rating from 1-5 stars"),
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
    round_number = models.PositiveIntegerField(help_text=_("Which speed dating round (1, 2, 3...)"))
    mutual_rating_score = models.FloatField(
        help_text=_("Combined rating score from Phase 2"),
        default=0
    )
    is_top_match = models.BooleanField(
        default=False,
        help_text=_("True if this is user's #1 rated match (gets extended time)")
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
