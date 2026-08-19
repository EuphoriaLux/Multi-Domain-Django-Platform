"""
Crush Connect 7-Day Deliberate Connection System Models.

Implements the approved product blueprint (2026-08-17 / Epic 13):
- 7-day Connect cycle with daily 3 cards, private answers, daily expiry.
- 24-hour weekly review ("Deine Connect-Woche") with platform compatibility highlight
  ("Passt besonders gut zu dir") and single "Ich möchte dich kennenlernen" request.
- Temporary Connect chat with structured coffee-date planning (partner venues from hub.Location),
  inactivity timeouts, meeting double-confirmation, and 3-day post-meeting countdown.
- Permanent pair exclusion and 1-click block / report safety mechanisms.
"""

from datetime import timedelta
from django.conf import settings
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class ConnectWeekSession(models.Model):
    """Tracks a member's 7-day deliberate Connect cycle."""

    class Status(models.TextChoices):
        ACTIVE = "active", _("Active (In 7-day cycle)")
        REVIEW_OPEN = "review_open", _("Review Open (24h review window)")
        COMPLETED = "completed", _("Completed")
        EXPIRED = "expired", _("Expired")
        ABANDONED = "abandoned", _("Abandoned")

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="connect_week_sessions",
    )
    started_at = models.DateTimeField(auto_now_add=True, db_index=True)
    current_day_number = models.PositiveSmallIntegerField(
        default=1,
        help_text=_("Current day in the 7-day cycle (1 to 7)"),
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ACTIVE,
        db_index=True,
    )
    is_review_open = models.BooleanField(
        default=False,
        help_text=_("True during the 24h review window on Day 8"),
    )
    review_opened_at = models.DateTimeField(null=True, blank=True)
    review_expires_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_("24 hours after review opened"),
    )
    completed_at = models.DateTimeField(null=True, blank=True)

    compatibility_highlight_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="highlighted_in_sessions",
        help_text=_("Platform-generated compatibility highlight for this cycle"),
    )

    class Meta:
        ordering = ["-started_at"]
        verbose_name = _("Connect Week Session")
        verbose_name_plural = _("Connect Week Sessions")

    def __str__(self):
        return f"{self.user.username} - Cycle #{self.pk} (Day {self.current_day_number}, {self.status})"

    @property
    def is_active(self) -> bool:
        return self.status == self.Status.ACTIVE

    @property
    def is_review_active(self) -> bool:
        if self.status != self.Status.REVIEW_OPEN or not self.review_expires_at:
            return False
        return timezone.now() < self.review_expires_at

    def open_weekly_review(self, highlight_user=None):
        """Transition session to the 24-hour review window."""
        now = timezone.now()
        self.status = self.Status.REVIEW_OPEN
        self.is_review_open = True
        self.review_opened_at = now
        self.review_expires_at = now + timedelta(hours=24)
        if highlight_user:
            self.compatibility_highlight_user = highlight_user
        self.save(
            update_fields=[
                "status",
                "is_review_open",
                "review_opened_at",
                "review_expires_at",
                "compatibility_highlight_user",
            ]
        )


class ConnectCycleCard(models.Model):
    """A profile card presented to the member on a specific day of their cycle."""

    session = models.ForeignKey(
        ConnectWeekSession,
        on_delete=models.CASCADE,
        related_name="cards",
    )
    day_number = models.PositiveSmallIntegerField(
        help_text=_("Cycle day on which this card was generated (1-7)"),
    )
    card_index = models.PositiveSmallIntegerField(
        help_text=_("Index of the card for the day (1, 2, or 3)"),
    )
    target_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="connect_cards_as_target",
    )
    generated_date = models.DateField(db_index=True)
    answers_json = models.JSONField(
        default=dict,
        blank=True,
        help_text=_("Private responses and compatibility signals recorded for this card"),
    )
    is_completed = models.BooleanField(
        default=False,
        db_index=True,
        help_text=_("Whether the user answered the card questions"),
    )
    completed_at = models.DateTimeField(null=True, blank=True)
    is_expired = models.BooleanField(
        default=False,
        help_text=_("True if midnight passed without completion"),
    )

    class Meta:
        ordering = ["day_number", "card_index"]
        constraints = [
            models.UniqueConstraint(
                fields=["session", "day_number", "card_index"],
                name="unique_card_per_day_slot",
            ),
            models.UniqueConstraint(
                fields=["session", "target_user"],
                name="unique_target_per_session",
            ),
        ]
        verbose_name = _("Connect Cycle Card")
        verbose_name_plural = _("Connect Cycle Cards")

    def __str__(self):
        return f"Card Day {self.day_number}#{self.card_index}: {self.session.user.username} -> {self.target_user.username}"


class ConnectWeeklyRequest(models.Model):
    """The single deliberate weekly connection request sent during the review window."""

    class Status(models.TextChoices):
        PENDING = "pending", _("Pending (Awaiting recipient response)")
        ACCEPTED = "accepted", _("Accepted (Chat opened)")
        DECLINED = "declined", _("Declined")
        EXPIRED = "expired", _("Expired (24h timeout)")
        CANCELLED = "cancelled", _("Cancelled")

    session = models.ForeignKey(
        ConnectWeekSession,
        on_delete=models.CASCADE,
        related_name="weekly_requests",
    )
    requester = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="sent_connect_requests",
    )
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="received_connect_requests",
    )
    target_card = models.ForeignKey(
        ConnectCycleCard,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="originating_requests",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    message = models.CharField(
        max_length=255,
        default="Ich möchte dich kennenlernen.",
        help_text=_("Standard connection invitation copy"),
    )
    sent_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(
        help_text=_("24 hours from sent_at for recipient to respond"),
    )
    responded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-sent_at"]
        verbose_name = _("Connect Weekly Request")
        verbose_name_plural = _("Connect Weekly Requests")

    def __str__(self):
        return f"Request: {self.requester.username} -> {self.recipient.username} ({self.status})"

    def save(self, *args, **kwargs):
        if not self.expires_at and not self.pk:
            self.expires_at = timezone.now() + timedelta(hours=24)
        super().save(*args, **kwargs)

    @property
    def is_expired(self) -> bool:
        if self.status == self.Status.PENDING and timezone.now() > self.expires_at:
            return True
        return self.status == self.Status.EXPIRED


class ConnectTemporaryChat(models.Model):
    """Temporary communication channel opened upon mutual request acceptance."""

    class Status(models.TextChoices):
        ACTIVE = "active", _("Active")
        MEETING_SCHEDULED = "meeting_scheduled", _("Meeting Scheduled")
        MEETING_CONFIRMED = "meeting_confirmed", _("Meeting Confirmed (3-day countdown)")
        CLOSED = "closed", _("Closed")
        BLOCKED = "blocked", _("Blocked")

    class CloseReason(models.TextChoices):
        INACTIVITY_TIMEOUT = "inactivity_timeout", _("Inactivity Timeout (7 days)")
        POST_MEETING_RETENTION = "post_meeting_retention", _("Post-meeting retention finished")
        MEMBER_DECLINED = "member_declined", _("Member closed conversation")
        BLOCKED = "blocked", _("Blocked by participant")

    request = models.OneToOneField(
        ConnectWeeklyRequest,
        on_delete=models.CASCADE,
        related_name="chat",
    )
    participant_1 = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="connect_chats_as_p1",
    )
    participant_2 = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="connect_chats_as_p2",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ACTIVE,
        db_index=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(
        help_text=_("Default 7-day expiration if no meeting is scheduled"),
    )
    reminder_sent = models.BooleanField(
        default=False,
        help_text=_("True when 24h gentle reminder was sent"),
    )
    closed_at = models.DateTimeField(null=True, blank=True)
    close_reason = models.CharField(
        max_length=30,
        choices=CloseReason.choices,
        blank=True,
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("Connect Temporary Chat")
        verbose_name_plural = _("Connect Temporary Chats")

    def __str__(self):
        return f"Connect Chat: {self.participant_1.username} & {self.participant_2.username} ({self.status})"

    def save(self, *args, **kwargs):
        if not self.expires_at and not self.pk:
            self.expires_at = timezone.now() + timedelta(days=7)
        super().save(*args, **kwargs)

    def get_other_participant(self, user):
        return self.participant_2 if self.participant_1_id == user.id else self.participant_1


class ConnectChatMessage(models.Model):
    """Direct message within a ConnectTemporaryChat."""

    chat = models.ForeignKey(
        ConnectTemporaryChat,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="sent_connect_chat_messages",
    )
    message = models.TextField(max_length=1000)
    sent_at = models.DateTimeField(auto_now_add=True, db_index=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["sent_at"]
        verbose_name = _("Connect Chat Message")
        verbose_name_plural = _("Connect Chat Messages")

    def __str__(self):
        return f"Msg from {self.sender.username} at {self.sent_at}"


class ConnectCoffeeDate(models.Model):
    """Structured coffee date proposal and confirmation inside a temporary chat."""

    class Status(models.TextChoices):
        PROPOSED = "proposed", _("Proposed")
        ACCEPTED = "accepted", _("Accepted / Scheduled")
        RESCHEDULED = "rescheduled", _("Rescheduled")
        CONFIRMED_BY_BOTH = "confirmed_by_both", _("Confirmed (Both attended)")
        CANCELLED = "cancelled", _("Cancelled")

    chat = models.OneToOneField(
        ConnectTemporaryChat,
        on_delete=models.CASCADE,
        related_name="coffee_date",
    )
    proposer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="proposed_connect_dates",
    )
    venue_location = models.ForeignKey(
        "hub.Location",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="connect_coffee_dates",
        help_text=_("Curated partner café/bar location from the Hub"),
    )
    custom_venue_name = models.CharField(max_length=255, blank=True)
    custom_venue_address = models.CharField(max_length=255, blank=True)

    proposed_date = models.DateField()
    proposed_time_slot = models.CharField(
        max_length=50,
        blank=True,
        help_text=_("e.g. 18:30 or Evening"),
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PROPOSED,
        db_index=True,
    )
    participant_1_confirmed_at = models.DateTimeField(null=True, blank=True)
    participant_2_confirmed_at = models.DateTimeField(null=True, blank=True)
    meeting_confirmed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_("Timestamp when both members confirmed attending"),
    )

    class Meta:
        ordering = ["-proposed_date"]
        verbose_name = _("Connect Coffee Date")
        verbose_name_plural = _("Connect Coffee Dates")

    def __str__(self):
        venue = self.venue_location.name if self.venue_location else (self.custom_venue_name or "Custom Venue")
        return f"Coffee Date: {venue} on {self.proposed_date} ({self.status})"

    def confirm_by_user(self, user):
        """Record meeting confirmation for a participant. When both confirm, schedule chat close in +3 days."""
        now = timezone.now()
        is_p1 = self.chat.participant_1_id == user.id
        is_p2 = self.chat.participant_2_id == user.id

        if is_p1:
            self.participant_1_confirmed_at = now
        elif is_p2:
            self.participant_2_confirmed_at = now

        if self.participant_1_confirmed_at and self.participant_2_confirmed_at:
            self.status = self.Status.CONFIRMED_BY_BOTH
            self.meeting_confirmed_at = now
            # Extend chat lifespan to 3 days after meeting confirmation
            self.chat.status = ConnectTemporaryChat.Status.MEETING_CONFIRMED
            self.chat.expires_at = now + timedelta(days=3)
            self.chat.save(update_fields=["status", "expires_at"])

        self.save()


class ConnectPairExclusion(models.Model):
    """Permanent exclusion preventing a pair from ever being suggested or connected again."""

    class Reason(models.TextChoices):
        CYCLE_COMPLETED = "cycle_completed", _("Completed Connect cycle")
        REQUEST_DECLINED = "request_declined", _("Connection request declined")
        REQUEST_EXPIRED = "request_expired", _("Connection request expired")
        MEMBER_BLOCKED = "member_blocked", _("Blocked by member")
        COACH_EXCLUDED = "coach_excluded", _("Excluded by coach")

    user_a = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="connect_exclusions_as_a",
    )
    user_b = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="connect_exclusions_as_b",
    )
    reason = models.CharField(
        max_length=30,
        choices=Reason.choices,
        db_index=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user_a", "user_b"],
                name="unique_connect_pair_exclusion",
            )
        ]
        verbose_name = _("Connect Pair Exclusion")
        verbose_name_plural = _("Connect Pair Exclusions")

    def __str__(self):
        return f"Exclusion: {self.user_a.username} <-> {self.user_b.username} ({self.reason})"

    @classmethod
    def get_canonical_pair(cls, user_1, user_2):
        u1_id = getattr(user_1, "id", user_1)
        u2_id = getattr(user_2, "id", user_2)
        if u1_id < u2_id:
            return user_1, user_2
        return user_2, user_1

    @classmethod
    def exclude_pair(cls, user_1, user_2, reason):
        u_a, u_b = cls.get_canonical_pair(user_1, user_2)
        return cls.objects.get_or_create(
            user_a=u_a,
            user_b=u_b,
            defaults={"reason": reason},
        )

    @classmethod
    def are_excluded(cls, user_1, user_2) -> bool:
        u_a, u_b = cls.get_canonical_pair(user_1, user_2)
        return cls.objects.filter(user_a=u_a, user_b=u_b).exists()


class ConnectReport(models.Model):
    """Trust & Safety report submitted by a member against another user."""

    class Status(models.TextChoices):
        PENDING = "pending", _("Pending Review")
        INVESTIGATING = "investigating", _("Under Investigation")
        RESOLVED = "resolved", _("Resolved")
        DISMISSED = "dismissed", _("Dismissed")

    reporter = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="connect_reports_filed",
    )
    reported_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="connect_reports_received",
    )
    reason = models.CharField(max_length=50)
    details = models.TextField(blank=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    reviewed_by = models.ForeignKey(
        "crush_lu.CrushCoach",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reviewed_connect_reports",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    admin_notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("Connect Report")
        verbose_name_plural = _("Connect Reports")

    def __str__(self):
        return f"Report #{self.pk}: {self.reporter.username} -> {self.reported_user.username} ({self.status})"
