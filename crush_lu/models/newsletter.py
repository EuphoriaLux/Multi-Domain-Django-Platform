from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class Newsletter(models.Model):
    """
    A newsletter to be sent to Crush.lu members.
    Supports configurable audiences and tracks send statistics.
    """

    AUDIENCE_CHOICES = [
        ('all_users', _('All registered users')),
        ('all_profiles', _('Users who created a profile')),
        ('approved_profiles', _('Users with approved profiles')),
        ('segment', _('Specific user segment')),
    ]

    STATUS_CHOICES = [
        ('draft', _('Draft')),
        ('sending', _('Sending')),
        ('sent', _('Sent')),
        ('failed', _('Failed')),
    ]

    # Content
    subject = models.CharField(max_length=200)
    body_html = models.TextField(
        help_text=_("Newsletter body content (HTML)")
    )
    body_text = models.TextField(
        blank=True,
        help_text=_("Plain text fallback (auto-stripped from HTML if blank)")
    )

    # Targeting
    audience = models.CharField(
        max_length=20,
        choices=AUDIENCE_CHOICES,
        default='all_users',
    )
    segment_key = models.CharField(
        max_length=100,
        blank=True,
        help_text=_("Segment key from user_segments.py (only used when audience='segment')")
    )

    # Status
    status = models.CharField(
        max_length=10,
        choices=STATUS_CHOICES,
        default='draft',
    )

    # Metadata
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_newsletters',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    # Statistics
    total_recipients = models.PositiveIntegerField(default=0)
    total_sent = models.PositiveIntegerField(default=0)
    total_failed = models.PositiveIntegerField(default=0)
    total_skipped = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['-created_at']
        verbose_name = _("Newsletter")
        verbose_name_plural = _("Newsletters")

    def __str__(self):
        return f"{self.subject} ({self.get_status_display()})"


class NewsletterRecipient(models.Model):
    """
    Tracks per-user send status for a newsletter.
    Enables resumability: re-running a newsletter skips already-sent users.
    """

    STATUS_CHOICES = [
        ('pending', _('Pending')),
        ('sent', _('Sent')),
        ('failed', _('Failed')),
        ('skipped', _('Skipped')),
    ]

    newsletter = models.ForeignKey(
        Newsletter,
        on_delete=models.CASCADE,
        related_name='recipients',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='newsletter_receipts',
    )
    email = models.EmailField(
        help_text=_("Email snapshot at send time")
    )
    status = models.CharField(
        max_length=10,
        choices=STATUS_CHOICES,
        default='pending',
    )
    sent_at = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(blank=True)

    class Meta:
        unique_together = [('newsletter', 'user')]
        indexes = [
            models.Index(fields=['newsletter', 'status']),
        ]
        verbose_name = _("Newsletter Recipient")
        verbose_name_plural = _("Newsletter Recipients")

    def __str__(self):
        return f"{self.email} - {self.get_status_display()}"
