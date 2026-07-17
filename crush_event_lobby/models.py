import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q
from django.utils import timezone


class EventLobbyConsent(models.Model):
    """Versioned, explicit consent for clear-photo Event Lobby sharing."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="event_lobby_consent",
    )
    version = models.PositiveSmallIntegerField(default=1)
    acknowledged_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return f"Event Lobby consent v{self.version} for user {self.user_id}"


class EventLobbyParticipation(models.Model):
    SOURCE_CHECKIN = "checkin"
    SOURCE_ONBOARDING = "onboarding_completed"
    SOURCE_CHOICES = [
        (SOURCE_CHECKIN, "Check-in"),
        (SOURCE_ONBOARDING, "Onboarding completed"),
    ]

    event_registration = models.OneToOneField(
        "crush_lu.EventRegistration",
        on_delete=models.CASCADE,
        related_name="event_lobby_participation",
    )
    event = models.ForeignKey(
        "crush_lu.MeetupEvent",
        on_delete=models.CASCADE,
        related_name="lobby_participations",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="event_lobby_participations",
    )
    opaque_handle = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    joined_at = models.DateTimeField(default=timezone.now, db_index=True)
    eligibility_source = models.CharField(max_length=24, choices=SOURCE_CHOICES)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-joined_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["event", "user"], name="event_lobby_unique_participant"
            )
        ]
        indexes = [
            models.Index(fields=["event", "joined_at"], name="lobby_event_joined_idx")
        ]

    def clean(self):
        errors = {}
        if self.event_registration_id:
            registration = self.event_registration
            if registration.event_id != self.event_id:
                errors["event"] = "Event must match the registration event."
            if registration.user_id != self.user_id:
                errors["user"] = "User must match the registration user."
            if registration.status != "attended":
                errors["event_registration"] = "Registration must be attended."
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"Lobby participant {self.pk} at event {self.event_id}"


class EventMeetSignal(models.Model):
    """An immutable directional live-event meet signal."""

    event = models.ForeignKey(
        "crush_lu.MeetupEvent",
        on_delete=models.CASCADE,
        related_name="event_meet_signals",
    )
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="event_meet_signals_sent",
    )
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="event_meet_signals_received",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    mutual_revealed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["event", "sender", "recipient"],
                name="event_lobby_unique_signal",
            ),
            models.CheckConstraint(
                condition=~Q(sender=F("recipient")),
                name="event_lobby_signal_no_self",
            ),
        ]
        indexes = [
            models.Index(fields=["event", "sender"], name="lobby_signal_sender_idx"),
            models.Index(
                fields=["event", "recipient"], name="lobby_signal_recipient_idx"
            ),
        ]

    def clean(self):
        if self.sender_id == self.recipient_id:
            raise ValidationError("A member cannot signal themselves.")

    def __str__(self):
        return f"Event signal {self.pk} at event {self.event_id}"
