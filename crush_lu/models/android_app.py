from django.conf import settings
from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class AndroidAppDevice(models.Model):
    """
    Native Android FCM registration for the Play Store app.

    This is intentionally separate from Web Push subscriptions. The PWA uses
    browser VAPID subscriptions; the Android app uses FCM registration tokens.
    """

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="android_app_devices",
        help_text=_("User who owns this Android app installation"),
    )
    registration_token = models.CharField(
        max_length=255,
        unique=True,
        help_text=_("FCM registration token for the native Android app"),
    )
    device_id = models.CharField(
        max_length=128,
        blank=True,
        db_index=True,
        help_text=_("Stable app-generated device identifier"),
    )
    package_name = models.CharField(max_length=128, default="lu.crush.app")
    app_version = models.CharField(max_length=32, blank=True)
    app_build = models.CharField(max_length=32, blank=True)
    device_name = models.CharField(max_length=100, blank=True)
    system_version = models.CharField(max_length=50, blank=True)
    user_agent = models.TextField(blank=True)

    enabled = models.BooleanField(default=True)
    notify_new_messages = models.BooleanField(
        default=True, help_text=_("Notify about new connection messages")
    )
    notify_event_reminders = models.BooleanField(
        default=True, help_text=_("Notify about upcoming events")
    )
    notify_new_connections = models.BooleanField(
        default=True, help_text=_("Notify about new connection requests")
    )
    notify_profile_updates = models.BooleanField(
        default=True, help_text=_("Notify about profile approval status")
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_seen_at = models.DateTimeField(auto_now=True)
    last_push_at = models.DateTimeField(null=True, blank=True)
    failure_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-last_seen_at"]
        verbose_name = _("Android App Device")
        verbose_name_plural = _("Android App Devices")
        indexes = [
            models.Index(fields=["user", "enabled"]),
        ]

    def __str__(self):
        label = self.device_name or self.device_id or self.registration_token[:12]
        return f"{self.user.username} - {label}"

    def mark_success(self):
        self.last_push_at = timezone.now()
        self.failure_count = 0
        self.save(update_fields=["last_push_at", "failure_count"])

    def mark_failure(self):
        self.failure_count += 1
        update_fields = ["failure_count"]
        if self.failure_count >= 5:
            self.enabled = False
            update_fields.append("enabled")
        self.save(update_fields=update_fields)
