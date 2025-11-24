from django.db import models
from django.contrib.auth.models import User
from .events import MeetupEvent
from .profiles import CrushCoach, CrushProfile, ProfileSubmission

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
