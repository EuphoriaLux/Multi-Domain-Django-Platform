"""
Event Lobby models — the live, event-scoped "I'd like to meet you" surface.

Spec: docs/superpowers/specs/2026-07-17-crush-connect-event-lobby-design.md

- ``EventLobbyParticipation``: one row per eligible checked-in Crush Connect
  member who joined the lobby before the exact scheduled event end. The row
  freezes recap membership (§9.1) while every access check stays dynamic —
  it deliberately snapshots NO photo, name, or profile data.
- ``EventMeetSignal``: one immutable, anonymous, directional "I'd like to
  meet you" per event pair (§9.2). Three distinct recipients per sender per
  event, enforced in ``services.event_lobby.send_meet_signal`` inside a
  locking transaction — never only in the UI.

Pre-mutual anonymity contract (§13): members are addressed by the
participation's opaque, event-scoped ``handle`` on every client-visible
surface (roster JSON, signal POSTs, photo URLs). Durable user ids and first
names never leave the server before an authorized mutual reveal.

Recap phase (§9.3–§9.4):

- ``EventMeetingConfirmation``: one immutable, anonymous, directional
  "Yes, we met" during the 48-hour recap window. Unlimited per user.
- ``ConfirmedEncounter``: one durable unordered pair for "People I've Met",
  created only from reciprocal confirmations for the same event and never
  reordered or updated by later shared events.

Removal review (§9.5): ``ConfirmedEncounterRemovalRequest`` (ported from the
codex bake-off entry) records a member's private removal request. Submitting
immediately hides the encounter for both parties (``removal_pending``); a
staff reviewer resolves it to approved (permanently ``removed``), kept
hidden, or restored. The queue is deliberately staff-only until requests can
be scoped to an assigned coach — see ``services.event_lobby``.
"""

import uuid

from django.contrib.auth.models import User
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .events import EventRegistration, MeetupEvent


def _new_lobby_handle():
    """Opaque event-scoped participant handle (§13): random, meaningless,
    unique per participation row — so a handle can never be replayed across
    events and never encodes a durable user id."""
    return uuid.uuid4().hex


class EventLobbyParticipation(models.Model):
    """One eligible attendee's membership in one event's lobby (§9.1)."""

    ELIGIBILITY_SOURCE_CHOICES = [
        ("checkin", _("Eligible at QR check-in")),
        ("onboarding_completed", _("Completed Crush Connect onboarding mid-event")),
    ]

    event_registration = models.OneToOneField(
        EventRegistration,
        on_delete=models.CASCADE,
        related_name="lobby_participation",
        help_text=_("Authoritative attendance link — participation never exists without it"),
    )
    # Denormalized for safe, indexed roster queries; must always match the
    # registration (enforced in the service layer, asserted in tests).
    event = models.ForeignKey(
        MeetupEvent, on_delete=models.CASCADE, related_name="lobby_participations"
    )
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="lobby_participations"
    )
    handle = models.CharField(
        max_length=32,
        unique=True,
        default=_new_lobby_handle,
        editable=False,
        help_text=_("Opaque event-scoped participant handle used on all pre-mutual surfaces"),
    )
    joined_at = models.DateTimeField(
        default=timezone.now,
        editable=False,
        help_text=_("First eligible lobby time — immutable, orders the roster newest-first"),
    )
    eligibility_source = models.CharField(
        max_length=24,
        choices=ELIGIBILITY_SOURCE_CHOICES,
        default="checkin",
        help_text=_("Audit: how the member became eligible (§9.1)"),
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-joined_at"]
        verbose_name = _("Event Lobby Participation")
        verbose_name_plural = _("Event Lobby Participations")
        constraints = [
            models.UniqueConstraint(
                fields=["event", "user"], name="eventlobby_participation_unique"
            ),
        ]
        indexes = [
            models.Index(
                fields=["event", "-joined_at"], name="eventlobby_roster_idx"
            ),
        ]

    def __str__(self):
        return f"lobby:{self.event_id} user:{self.user_id} ({self.eligibility_source})"


class EventMeetSignal(models.Model):
    """One immutable directional live meet signal (§9.2).

    Irrevocable by design: rows are only ever created, never updated (except
    the one-time ``mutual_revealed_at`` stamp) and never deleted by members.
    The recipient learns only an exact anonymous count until the signal is
    mutual.
    """

    MAX_SIGNALS_PER_EVENT = 3

    event = models.ForeignKey(
        MeetupEvent, on_delete=models.CASCADE, related_name="lobby_meet_signals"
    )
    sender = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="lobby_signals_sent"
    )
    recipient = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="lobby_signals_received"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    mutual_revealed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_("Set on both rows of a pair when the reverse signal arrives (audit + idempotent UX)"),
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("Event Meet Signal")
        verbose_name_plural = _("Event Meet Signals")
        constraints = [
            models.UniqueConstraint(
                fields=["event", "sender", "recipient"],
                name="eventlobby_signal_unique",
            ),
            models.CheckConstraint(
                condition=~Q(sender=models.F("recipient")),
                name="eventlobby_signal_no_self",
            ),
        ]
        indexes = [
            # Serves the recipient's exact anonymous counter.
            models.Index(
                fields=["event", "recipient"], name="eventlobby_signal_rcpt_idx"
            ),
        ]

    def __str__(self):
        state = "mutual" if self.mutual_revealed_at else "one-sided"
        return f"signal:{self.event_id} {self.sender_id} → {self.recipient_id} ({state})"

    @property
    def is_mutual(self) -> bool:
        return self.mutual_revealed_at is not None


class EventMeetingConfirmation(models.Model):
    """One immutable directional post-event "Yes, we met" assertion (§9.3).

    Unlimited during the 48-hour recap window, anonymous until reciprocal,
    and irrevocable — rows are only ever created. The recipient never learns
    who confirmed them unless they independently confirm the same person.
    """

    event = models.ForeignKey(
        MeetupEvent, on_delete=models.CASCADE, related_name="lobby_meeting_confirmations"
    )
    confirmer = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="lobby_confirmations_made"
    )
    other_user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="lobby_confirmations_received"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("Event Meeting Confirmation")
        verbose_name_plural = _("Event Meeting Confirmations")
        constraints = [
            models.UniqueConstraint(
                fields=["event", "confirmer", "other_user"],
                name="eventlobby_confirmation_unique",
            ),
            models.CheckConstraint(
                condition=~Q(confirmer=models.F("other_user")),
                name="eventlobby_confirmation_no_self",
            ),
        ]
        indexes = [
            # Serves the recap's exact anonymous incoming counter.
            models.Index(
                fields=["event", "other_user"], name="eventlobby_confirm_other_idx"
            ),
        ]

    def __str__(self):
        return f"confirm:{self.event_id} {self.confirmer_id} → {self.other_user_id}"


class ConfirmedEncounter(models.Model):
    """One durable unordered pair for "People I've Met" (§9.4).

    Canonical ordering (``user_low.pk < user_high.pk``) makes the pair unique
    regardless of who confirmed first. ``created_at`` is set once and NEVER
    touched by later shared events (§7.8 — repeated meetings don't reorder).
    ``created_from_event`` is internal audit provenance only and must never be
    rendered to members (§13).
    """

    STATUS_CHOICES = [
        ("active", _("Active")),
        ("removal_pending", _("Removal requested — hidden")),
        ("removed", _("Removed")),
    ]

    user_low = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="encounters_as_low"
    )
    user_high = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="encounters_as_high"
    )
    created_from_event = models.ForeignKey(
        MeetupEvent,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="encounters_created",
        help_text=_("Internal audit provenance — never rendered in the collection"),
    )
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default="active", db_index=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    hidden_at = models.DateTimeField(null=True, blank=True)
    removed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("Confirmed Encounter")
        verbose_name_plural = _("Confirmed Encounters")
        constraints = [
            models.UniqueConstraint(
                fields=["user_low", "user_high"], name="eventlobby_encounter_unique"
            ),
            models.CheckConstraint(
                condition=Q(user_low__lt=models.F("user_high")),
                name="eventlobby_encounter_canonical_order",
            ),
        ]

    def __str__(self):
        return f"encounter:{self.user_low_id}+{self.user_high_id} ({self.status})"

    @staticmethod
    def canonical_pair(user_a, user_b):
        """(low, high) ordering for the unordered pair."""
        return (user_a, user_b) if user_a.pk < user_b.pk else (user_b, user_a)

    def counterpart_of(self, user):
        return self.user_high if self.user_low_id == user.pk else self.user_low


class ConfirmedEncounterRemovalRequest(models.Model):
    """Private moderation audit for hiding or removing a confirmed encounter.

    Ported from PR #633 (Codex) — full removal-review workflow.
    """

    REASON_SAFETY = "safety"
    REASON_PRIVACY = "privacy"
    REASON_MISTAKEN_IDENTITY = "mistaken_identity"
    REASON_NO_LONGER_VISIBLE = "no_longer_visible"
    REASON_OTHER = "other"
    REASON_CHOICES = [
        (REASON_SAFETY, _("I feel unsafe or uncomfortable")),
        (REASON_PRIVACY, _("Privacy concern")),
        (REASON_MISTAKEN_IDENTITY, _("We did not actually meet")),
        (REASON_NO_LONGER_VISIBLE, _("I no longer want this person visible")),
        (REASON_OTHER, _("Another reason")),
    ]

    STATUS_PENDING = "pending"
    STATUS_APPROVED = "approved"
    STATUS_KEPT_HIDDEN = "kept_hidden"
    STATUS_RESTORED = "restored"
    STATUS_CHOICES = [
        (STATUS_PENDING, _("Pending review")),
        (STATUS_APPROVED, _("Removal approved")),
        (STATUS_KEPT_HIDDEN, _("Resolved and kept hidden")),
        (STATUS_RESTORED, _("Visibility explicitly restored")),
    ]

    encounter = models.ForeignKey(
        ConfirmedEncounter,
        on_delete=models.CASCADE,
        related_name="removal_requests",
    )
    requested_by = models.ForeignKey(
        User,
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
        User,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="staff_reviewed_encounter_removals",
    )
    resolution_notes = models.TextField(blank=True, max_length=1000)

    class Meta:
        ordering = ["-requested_at"]
        verbose_name = _("Encounter Removal Request")
        verbose_name_plural = _("Encounter Removal Requests")
        constraints = [
            models.UniqueConstraint(
                fields=["encounter"],
                condition=Q(status="pending"),
                name="eventlobby_one_pending_removal",
            ),
        ]

    def clean(self):
        from django.core.exceptions import ValidationError
        if self.encounter_id and self.requested_by_id not in {
            self.encounter.user_low_id,
            self.encounter.user_high_id,
        }:
            raise ValidationError(_("Requester must belong to the encounter."))
        if self.reviewed_by_coach_id and self.reviewed_by_staff_id:
            raise ValidationError(_("A review has one acting reviewer."))

    def __str__(self):
        return f"removal:{self.encounter_id} by {self.requested_by_id} ({self.status})"
