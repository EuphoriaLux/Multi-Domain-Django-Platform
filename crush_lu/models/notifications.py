from django.contrib.auth.models import User
from django.db import models
from django.utils.translation import gettext_lazy as _


class Notification(models.Model):
    """In-app notification record.

    Written by NotificationService alongside push/email so users have a
    historical surface (the bell) even if they miss live channels.
    """

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="crush_notifications",
    )
    notification_type = models.CharField(
        max_length=64,
        db_index=True,
        help_text=_("Matches NotificationType.value (e.g. 'profile_approved')"),
    )
    title = models.CharField(max_length=200)
    body = models.TextField(blank=True)
    link_url = models.CharField(
        max_length=500,
        blank=True,
        help_text=_("Where the notification deep-links to when clicked"),
    )

    metadata = models.JSONField(default=dict, blank=True)

    dedupe_key = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        help_text=_(
            "Optional once-only claim token. When set, the database refuses a "
            "second row with the same (user, notification_type, dedupe_key), "
            "which is how concurrent senders agree on who delivers."
        ),
    )

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["user", "-created_at"],
                name="crush_notif_user_created_idx",
            ),
            models.Index(
                fields=["user", "read_at"],
                name="crush_notif_user_read_idx",
            ),
        ]
        constraints = [
            # Deliberately conditional. Most notification types are allowed to
            # repeat (every new message writes a row), and even the deduped
            # ones repeat legitimately across verification lifecycles — a
            # member rejected at the door and later approved in person MUST be
            # told twice. So this cannot be a constraint on
            # (user, notification_type): the key carries the lifecycle, and
            # rows that opt out by leaving it NULL are unconstrained.
            models.UniqueConstraint(
                fields=["user", "notification_type", "dedupe_key"],
                condition=models.Q(dedupe_key__isnull=False),
                name="crush_notif_unique_dedupe_key",
            ),
        ]

    def __str__(self):
        return f"{self.user.username} · {self.notification_type} · {self.title[:50]}"

    @property
    def is_unread(self):
        return self.read_at is None
