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
