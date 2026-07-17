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


class EventMeetingConfirmation(models.Model):
    """An immutable directional assertion made during the event recap."""

    event = models.ForeignKey(
        "crush_lu.MeetupEvent",
        on_delete=models.CASCADE,
        related_name="event_meeting_confirmations",
    )
    confirmer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="event_meeting_confirmations_made",
    )
    other_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="event_meeting_confirmations_received",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["event", "confirmer", "other_user"],
                name="event_lobby_unique_confirmation",
            ),
            models.CheckConstraint(
                condition=~Q(confirmer=F("other_user")),
                name="event_lobby_confirmation_no_self",
            ),
        ]
        indexes = [
            models.Index(
                fields=["event", "confirmer"], name="lobby_confirm_sender_idx"
            ),
            models.Index(fields=["event", "other_user"], name="lobby_confirm_recv_idx"),
        ]

    def __str__(self):
        return f"Event confirmation {self.pk} at event {self.event_id}"


class ConfirmedEncounter(models.Model):
    """One durable, canonically ordered pair for People I've Met."""

    STATUS_ACTIVE = "active"
    STATUS_REMOVAL_PENDING = "removal_pending"
    STATUS_REMOVED = "removed"
    STATUS_CHOICES = [
        (STATUS_ACTIVE, "Active"),
        (STATUS_REMOVAL_PENDING, "Removal pending"),
        (STATUS_REMOVED, "Removed"),
    ]

    user_low = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="confirmed_encounters_as_low",
    )
    user_high = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="confirmed_encounters_as_high",
    )
    opaque_handle = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    created_from_event = models.ForeignKey(
        "crush_lu.MeetupEvent",
        on_delete=models.PROTECT,
        related_name="confirmed_encounters",
    )
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default=STATUS_ACTIVE
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    hidden_at = models.DateTimeField(null=True, blank=True)
    removed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user_low", "user_high"],
                name="event_lobby_unique_encounter_pair",
            ),
            models.CheckConstraint(
                condition=Q(user_low__lt=F("user_high")),
                name="event_lobby_encounter_canonical_pair",
            ),
        ]

    def __str__(self):
        return f"Confirmed encounter {self.user_low_id}:{self.user_high_id}"


class ConfirmedEncounterRemovalRequest(models.Model):
    """Private moderation audit for hiding or removing a confirmed encounter."""

    REASON_SAFETY = "safety"
    REASON_PRIVACY = "privacy"
    REASON_MISTAKEN_IDENTITY = "mistaken_identity"
    REASON_NO_LONGER_VISIBLE = "no_longer_visible"
    REASON_OTHER = "other"
    REASON_CHOICES = [
        (REASON_SAFETY, "I feel unsafe or uncomfortable"),
        (REASON_PRIVACY, "Privacy concern"),
        (REASON_MISTAKEN_IDENTITY, "We did not actually meet"),
        (REASON_NO_LONGER_VISIBLE, "I no longer want this person visible"),
        (REASON_OTHER, "Another reason"),
    ]

    STATUS_PENDING = "pending"
    STATUS_APPROVED = "approved"
    STATUS_KEPT_HIDDEN = "kept_hidden"
    STATUS_RESTORED = "restored"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending review"),
        (STATUS_APPROVED, "Removal approved"),
        (STATUS_KEPT_HIDDEN, "Resolved and kept hidden"),
        (STATUS_RESTORED, "Visibility explicitly restored"),
    ]

    encounter = models.ForeignKey(
        ConfirmedEncounter,
        on_delete=models.CASCADE,
        related_name="removal_requests",
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="confirmed_encounter_removal_requests",
    )
    reason = models.CharField(max_length=32, choices=REASON_CHOICES)
    details = models.TextField(blank=True, max_length=500)
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
    )
    requested_at = models.DateTimeField(auto_now_add=True, db_index=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by_coach = models.ForeignKey(
        "crush_lu.CrushCoach",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="reviewed_encounter_removals",
    )
    reviewed_by_staff = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="staff_reviewed_encounter_removals",
    )
    resolution_notes = models.TextField(blank=True, max_length=1000)

    class Meta:
        ordering = ["-requested_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["encounter"],
                condition=Q(status="pending"),
                name="event_lobby_one_pending_removal",
            ),
        ]

    def clean(self):
        if self.encounter_id and self.requested_by_id not in {
            self.encounter.user_low_id,
            self.encounter.user_high_id,
        }:
            raise ValidationError("Requester must belong to the encounter.")
        if self.reviewed_by_coach_id and self.reviewed_by_staff_id:
            raise ValidationError("A review has one acting reviewer.")

    def __str__(self):
        return f"Encounter removal request {self.pk} ({self.status})"


class EventRecapNotice(models.Model):
    """Idempotency record for persisted recap notifications."""

    participation = models.OneToOneField(
        EventLobbyParticipation,
        on_delete=models.CASCADE,
        related_name="recap_notice",
    )
    opened_notification_at = models.DateTimeField(null=True, blank=True)
    reminder_notification_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Recap notice state for participation {self.participation_id}"
