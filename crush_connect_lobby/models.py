from django.db import models
from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from crush_lu.models.events import EventRegistration, MeetupEvent


class EventLobbyParticipation(models.Model):
    """
    Tracks a member's eligible participation in a specific event lobby.
    Created when checked-in AND fully onboarded / accepted consent.
    """
    event_registration = models.OneToOneField(
        EventRegistration,
        on_delete=models.CASCADE,
        related_name="lobby_participation"
    )
    event = models.ForeignKey(
        MeetupEvent,
        on_delete=models.CASCADE,
        related_name="lobby_participations",
        db_index=True
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="lobby_participations",
        db_index=True
    )
    joined_at = models.DateTimeField(auto_now_add=True)
    eligibility_source = models.CharField(
        max_length=30,
        choices=[
            ("checkin", _("Check-in")),
            ("onboarding_completed", _("Onboarding Completed")),
        ]
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("event", "user")
        ordering = ["-joined_at"]

    def clean(self):
        if self.event_registration.user != self.user:
            raise ValidationError(_("User must match registration user."))
        if self.event_registration.event != self.event:
            raise ValidationError(_("Event must match registration event."))

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.user.username} in {self.event.title} Lobby"


class EventMeetSignal(models.Model):
    """
    One directional live anonymous meet signal.
    """
    event = models.ForeignKey(
        MeetupEvent,
        on_delete=models.CASCADE,
        related_name="meet_signals"
    )
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="sent_lobby_signals"
    )
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="received_lobby_signals"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    mutual_revealed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ("event", "sender", "recipient")

    def clean(self):
        if self.sender == self.recipient:
            raise ValidationError(_("Cannot send signal to yourself."))
        # Verify both have lobby participation
        sender_participates = EventLobbyParticipation.objects.filter(
            event=self.event, user=self.sender
        ).exists()
        recipient_participates = EventLobbyParticipation.objects.filter(
            event=self.event, user=self.recipient
        ).exists()
        if not sender_participates:
            raise ValidationError(_("Sender must have lobby participation for this event."))
        if not recipient_participates:
            raise ValidationError(_("Recipient must have lobby participation for this event."))

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Signal: {self.sender.username} -> {self.recipient.username} ({self.event.title})"


class EventMeetingConfirmation(models.Model):
    """
    One directional post-event confirmation that the users met.
    """
    event = models.ForeignKey(
        MeetupEvent,
        on_delete=models.CASCADE,
        related_name="meeting_confirmations"
    )
    confirmer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="sent_meeting_confirmations"
    )
    other_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="received_meeting_confirmations"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("event", "confirmer", "other_user")

    def clean(self):
        if self.confirmer == self.other_user:
            raise ValidationError(_("Cannot confirm meeting with yourself."))
        # Verify both have lobby participation
        confirmer_participates = EventLobbyParticipation.objects.filter(
            event=self.event, user=self.confirmer
        ).exists()
        other_participates = EventLobbyParticipation.objects.filter(
            event=self.event, user=self.other_user
        ).exists()
        if not confirmer_participates or not other_participates:
            raise ValidationError(_("Both users must have participated in the lobby."))

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Confirmation: {self.confirmer.username} met {self.other_user.username} ({self.event.title})"


class ConfirmedEncounter(models.Model):
    """
    Durable confirmed real-world encounter inside People I've Met.
    Created when reciprocal EventMeetingConfirmations are registered.
    """
    user_low = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="confirmed_encounters_low"
    )
    user_high = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="confirmed_encounters_high"
    )
    created_from_event = models.ForeignKey(
        MeetupEvent,
        on_delete=models.CASCADE,
        related_name="confirmed_encounters"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(
        max_length=20,
        default="active",
        choices=[
            ("active", _("Active")),
            ("removal_pending", _("Removal Pending")),
            ("removed", _("Removed")),
        ]
    )
    hidden_at = models.DateTimeField(null=True, blank=True)
    removed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ("user_low", "user_high")

    def clean(self):
        if self.user_low == self.user_high:
            raise ValidationError(_("Cannot encounter yourself."))
        if self.user_low.pk >= self.user_high.pk:
            raise ValidationError(_("user_low PK must be strictly less than user_high PK."))

    def save(self, *args, **kwargs):
        # Enforce user ordering automatically
        if self.user_low and self.user_high and self.user_low.pk > self.user_high.pk:
            self.user_low, self.user_high = self.user_high, self.user_low
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Encounter: {self.user_low.username} - {self.user_high.username}"


class ConfirmedEncounterRemovalRequest(models.Model):
    """
    Encounter removal request submitted by a user, reviewed by a Coach/Support.
    """
    encounter = models.ForeignKey(
        ConfirmedEncounter,
        on_delete=models.CASCADE,
        related_name="removal_requests"
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="encounter_removal_requests"
    )
    reason = models.CharField(
        max_length=50,
        choices=[
            ("safety", _("Safety Concerns")),
            ("privacy", _("Privacy / Personal Choice")),
            ("behavior", _("Inappropriate Behavior")),
            ("other", _("Other")),
        ]
    )
    details = models.TextField(blank=True)
    status = models.CharField(
        max_length=20,
        default="pending",
        choices=[
            ("pending", _("Pending Review")),
            ("approved", _("Approved")),
            ("rejected", _("Rejected")),
        ]
    )
    requested_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by_coach = models.ForeignKey(
        "crush_lu.CrushCoach",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="encounter_removals_reviewed"
    )
    resolution_notes = models.TextField(blank=True)

    def __str__(self):
        return f"Removal Request for {self.encounter} by {self.requested_by.username}"
