"""
Post-event swiping: every attendee swipes through the other attendees of the
same event, and a mutual like opens a connection plus chat.

Replaces the "My Crush!" coach lead as the post-event action (decision
2026-11-20). One row per directional decision; the decision is final, so the
deck never shows the same person twice and a "pass" stays private forever.
A mutual like creates (or upgrades) the pair's ``EventConnection`` with
``flow='swipe'`` and status ``shared`` — the existing connection page then
carries the chat. Coaches see the resulting matches read-only.
"""

from django.contrib.auth.models import User
from django.db import models
from django.utils.translation import gettext_lazy as _

from .events import MeetupEvent


class EventSwipe(models.Model):
    """One attendee's like or pass on another attendee of the same event."""

    event = models.ForeignKey(
        MeetupEvent, on_delete=models.CASCADE, related_name="swipes"
    )
    swiper = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="event_swipes_sent"
    )
    target = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="event_swipes_received"
    )
    liked = models.BooleanField(help_text=_("True = like, False = pass"))
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Event swipe")
        verbose_name_plural = _("Event swipes")
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["event", "swiper", "target"],
                name="crush_lu_eventswipe_unique_pair",
            ),
            models.CheckConstraint(
                condition=~models.Q(swiper=models.F("target")),
                name="crush_lu_eventswipe_not_self",
            ),
        ]
        indexes = [
            models.Index(
                fields=["event", "target", "liked"],
                name="crush_lu_swipe_evt_tgt_idx",
            ),
        ]

    def __str__(self):
        verb = "likes" if self.liked else "passes"
        return f"{self.swiper_id} {verb} {self.target_id} (event {self.event_id})"


class SwipeCampaign(models.Model):
    """A period in which past events can be swiped again (December 2026).

    While a campaign runs, every event that ended before ``starts_at`` is
    swipeable for its verified attendees, on top of each event's own
    post-event window. The message sequence (start, two reminders, last
    call) is sent by the hourly dispatcher at the times set here.
    """

    name = models.CharField(max_length=120)
    starts_at = models.DateTimeField(
        help_text=_("Swiping opens and the start message goes out")
    )
    ends_at = models.DateTimeField(help_text=_("Swiping closes"))
    reminder_1_at = models.DateTimeField(
        null=True, blank=True, help_text=_("Push to members who have not swiped yet")
    )
    reminder_2_at = models.DateTimeField(
        null=True, blank=True, help_text=_("Push to members with profiles left")
    )
    last_call_at = models.DateTimeField(
        null=True, blank=True, help_text=_("Email to members with profiles left")
    )
    is_active = models.BooleanField(
        default=False,
        help_text=_(
            "Off: swiping stays closed and nothing is sent, whatever the dates"
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Swipe campaign")
        verbose_name_plural = _("Swipe campaigns")
        ordering = ["-starts_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(ends_at__gt=models.F("starts_at")),
                name="crush_lu_swipecampaign_ends_after_start",
            ),
        ]

    def __str__(self):
        return self.name


class SwipeCampaignMessage(models.Model):
    """One campaign message to one member: the send log and its idempotency key."""

    STEP_START = "start"
    STEP_REMINDER_1 = "reminder_1"
    STEP_REMINDER_2 = "reminder_2"
    STEP_LAST_CALL = "last_call"
    STEP_CHOICES = [
        (STEP_START, _("Start")),
        (STEP_REMINDER_1, _("Reminder 1")),
        (STEP_REMINDER_2, _("Reminder 2")),
        (STEP_LAST_CALL, _("Last call")),
    ]
    CHANNEL_EMAIL = "email"
    CHANNEL_PUSH = "push"
    CHANNEL_CHOICES = [(CHANNEL_EMAIL, _("Email")), (CHANNEL_PUSH, _("Push"))]
    STATUS_SENT = "sent"
    STATUS_SKIPPED = "skipped"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = [
        (STATUS_SENT, _("Sent")),
        (STATUS_SKIPPED, _("Skipped")),
        (STATUS_FAILED, _("Failed")),
    ]

    campaign = models.ForeignKey(
        SwipeCampaign, on_delete=models.CASCADE, related_name="messages"
    )
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="swipe_campaign_messages"
    )
    step = models.CharField(max_length=20, choices=STEP_CHOICES)
    channel = models.CharField(max_length=10, choices=CHANNEL_CHOICES)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Swipe campaign message")
        verbose_name_plural = _("Swipe campaign messages")
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["campaign", "user", "step", "channel"],
                name="crush_lu_swipecampaignmsg_unique",
            ),
        ]


class PastEventDeckOptOut(models.Model):
    """The member asked not to appear in past-event decks of a campaign.

    Hides them from every campaign deck at once. Their own swiping in a
    campaign is closed too, so nobody swipes while staying invisible. The
    normal post-event window right after an event is not affected.
    """

    user = models.OneToOneField(
        User, on_delete=models.CASCADE, related_name="past_event_deck_opt_out"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Past-event deck opt-out")
        verbose_name_plural = _("Past-event deck opt-outs")
