from django.db import models
from django.contrib.auth.models import User
from .events import MeetupEvent
from .profiles import CrushCoach, CrushProfile, ProfileSubmission


class EventConnectionQuerySet(models.QuerySet):
    """Custom QuerySet for EventConnection with performance optimizations."""

    def for_user(self, user):
        """Connections where user is requester or recipient."""
        return self.filter(
            models.Q(requester=user) | models.Q(recipient=user)
        )

    def pending_for_user(self, user):
        """Pending connection requests received by user."""
        return self.filter(recipient=user, status='pending')

    def active_for_user(self, user):
        """Active connections (accepted through shared) for user."""
        return self.for_user(user).filter(
            status__in=['accepted', 'coach_reviewing', 'coach_approved', 'shared']
        )

    def for_event(self, event, user):
        """Connections for a specific event involving user."""
        return self.for_user(user).filter(event=event)

    def annotate_is_mutual(self):
        """
        Annotate queryset with is_mutual_annotated field.

        Use this instead of the is_mutual property to avoid N+1 queries:

        # BAD: N+1 queries
        connections = EventConnection.objects.all()
        for conn in connections:
            if conn.is_mutual:  # Triggers query per connection!
                print("Mutual!")

        # GOOD: Single query with annotation
        connections = EventConnection.objects.annotate_is_mutual()
        for conn in connections:
            if conn.is_mutual_annotated:  # No query!
                print("Mutual!")
        """
        from django.db.models import Exists, OuterRef

        mutual_subquery = EventConnection.objects.filter(
            requester=OuterRef('recipient'),
            recipient=OuterRef('requester'),
            event=OuterRef('event')
        )

        return self.annotate(
            is_mutual_annotated=Exists(mutual_subquery)
        )


class EventConnectionManager(models.Manager):
    """Custom manager for EventConnection."""

    def get_queryset(self):
        return EventConnectionQuerySet(self.model, using=self._db)

    def for_user(self, user):
        return self.get_queryset().for_user(user)

    def pending_for_user(self, user):
        return self.get_queryset().pending_for_user(user)

    def active_for_user(self, user):
        return self.get_queryset().active_for_user(user)

    def for_event(self, event, user):
        return self.get_queryset().for_event(event, user)

    def annotate_is_mutual(self):
        return self.get_queryset().annotate_is_mutual()


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

    objects = EventConnectionManager()

    class Meta:
        unique_together = ('requester', 'recipient', 'event')
        ordering = ['-requested_at']
        indexes = [
            models.Index(fields=['event', 'status'], name='crush_lu_conn_event_status_idx'),
            models.Index(fields=['requester', 'status'], name='crush_lu_conn_req_status_idx'),
            models.Index(fields=['recipient', 'status'], name='crush_lu_conn_recip_status_idx'),
        ]

    def __str__(self):
        return f"{self.requester.username} → {self.recipient.username} ({self.event.title})"

    @property
    def is_mutual(self):
        """
        Check if there's a mutual connection request.

        WARNING: This property causes an N+1 query problem when accessed in a loop.
        Use EventConnection.objects.annotate_is_mutual() instead for better performance.

        Example:
            # BAD: N+1 queries
            connections = EventConnection.objects.all()
            for conn in connections:
                if conn.is_mutual:  # Database query per connection!
                    print("Mutual")

            # GOOD: Single query
            connections = EventConnection.objects.annotate_is_mutual()
            for conn in connections:
                if conn.is_mutual_annotated:  # No query!
                    print("Mutual")
        """
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
        """
        Assign a coach to facilitate this connection.

        Optimized to fetch all related data in a single query using select_related.
        """
        requester_profile = CrushProfile.objects.select_related('user').get(user=self.requester)

        # Get the approved submission for the requester with coach pre-fetched
        requester_submission = ProfileSubmission.objects.select_related('coach').filter(
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
