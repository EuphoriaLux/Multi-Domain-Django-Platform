"""
Crush Connect waitlist model.

Tracks users who have expressed interest in the upcoming
Crush Connect online discovery portal.
"""

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class CrushConnectWaitlist(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="crush_connect_waitlist",
    )
    joined_at = models.DateTimeField(auto_now_add=True)
    notification_preference = models.BooleanField(
        default=True,
        help_text=_("Wants to be notified when Crush Connect launches"),
    )

    class Meta:
        ordering = ["joined_at"]
        verbose_name = _("Crush Connect Waitlist Entry")
        verbose_name_plural = _("Crush Connect Waitlist")

    def __str__(self):
        return f"{self.user.get_full_name() or self.user.username} - #{self.waitlist_position}"

    @property
    def is_eligible(self):
        """Approved profile + at least 1 attended event."""
        from .events import EventRegistration

        has_approved_profile = (
            hasattr(self.user, "crushprofile") and self.user.crushprofile.is_approved
        )
        has_attended_event = EventRegistration.objects.filter(
            user=self.user, status="attended"
        ).exists()
        return has_approved_profile and has_attended_event

    @property
    def waitlist_position(self):
        return (
            CrushConnectWaitlist.objects.filter(joined_at__lt=self.joined_at).count()
            + 1
        )
