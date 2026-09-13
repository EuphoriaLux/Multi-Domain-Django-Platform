"""Delivery-health records for outbound Crush.lu email."""

from django.db import models
from django.utils import timezone


class EmailSuppression(models.Model):
    """An address that outbound Crush.lu email must not contact."""

    SOURCE_CHOICES = [
        ("graph_ndr", "Microsoft Graph NDR"),
        ("manual", "Manual"),
    ]

    email = models.EmailField(unique=True)
    is_active = models.BooleanField(default=True, db_index=True)
    reason = models.CharField(max_length=120, default="hard_bounce")
    source = models.CharField(
        max_length=20, choices=SOURCE_CHOICES, default="graph_ndr"
    )
    diagnostic = models.TextField(blank=True)
    suppressed_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-suppressed_at"]

    def save(self, *args, **kwargs):
        self.email = self.email.strip().lower()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.email


class EmailBounceEvent(models.Model):
    """Auditable, deduplicated result of classifying one delivery report."""

    CLASSIFICATION_CHOICES = [
        ("hard", "Hard bounce"),
        ("soft", "Soft bounce"),
        ("unknown", "Unknown"),
    ]

    source_message_id = models.CharField(max_length=512, unique=True)
    recipient = models.EmailField(blank=True)
    classification = models.CharField(
        max_length=12,
        choices=CLASSIFICATION_CHOICES,
        default="unknown",
        db_index=True,
    )
    subject = models.CharField(max_length=998, blank=True)
    diagnostic = models.TextField(blank=True)
    received_at = models.DateTimeField(null=True, blank=True, db_index=True)
    processed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-received_at", "-processed_at"]

    def __str__(self):
        return f"{self.classification}: {self.recipient or self.source_message_id}"
