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

# PROTOTYPE-STUB: the recap-phase models (`EventMeetingConfirmation`,
# `ConfirmedEncounter`, `ConfirmedEncounterRemovalRequest`, §9.3–§9.5) are
# outside this prototype slice; the live surfaces treat "already met" as
# always-false until they exist.
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
