"""
Unified multi-channel campaign models for the Coach Panel campaign dashboard.

A Campaign is composed once (audience + per-channel content) and fanned out
across one or more channels (email / WhatsApp / web push) by the channel
adapters in ``crush_lu/services/campaigns.py``.

Channel state:
- The email leg reuses the proven ``Newsletter`` + ``NewsletterRecipient``
  engine (a campaign-owned Newsletter is linked via ``Newsletter.campaign``).
- WhatsApp and push track per-recipient state in ``CampaignRecipient`` —
  ``hub.WhatsAppMessage.user`` is the *sending admin* (not the recipient), so
  WhatsApp state cannot live on that model; each CampaignRecipient instead
  links to the WhatsAppMessage it produced so webhook-driven delivered/read
  transitions flow into campaign stats without touching the hub app.
"""
from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

from .newsletter import Newsletter


class Campaign(models.Model):
    """A single outreach campaign fanned out across one or more channels."""

    STATUS_CHOICES = [
        ('draft', _('Draft')),
        ('scheduled', _('Scheduled')),
        ('sending', _('Sending')),
        ('sent', _('Sent')),
        ('partial', _('Partially sent')),
        ('failed', _('Failed')),
        ('cancelled', _('Cancelled')),
    ]

    CHANNEL_EMAIL = 'email'
    CHANNEL_WHATSAPP = 'whatsapp'
    CHANNEL_PUSH = 'push'
    CHANNEL_KEYS = (CHANNEL_EMAIL, CHANNEL_WHATSAPP, CHANNEL_PUSH)

    name = models.CharField(max_length=200)
    slug = models.SlugField(
        max_length=80,
        unique=True,
        help_text=_("Used as utm_campaign in tracked links"),
    )
    channels = models.JSONField(
        default=list,
        help_text=_('Enabled channels, e.g. ["email", "push"]'),
    )

    # Targeting — mirrors Newsletter semantics so the newsletter audience
    # resolution and segment shortcuts work unchanged.
    audience = models.CharField(
        max_length=20,
        choices=Newsletter.AUDIENCE_CHOICES,
        default='segment',
    )
    segment_key = models.CharField(
        max_length=100,
        blank=True,
        help_text=_("Segment key from user_segments.py (when audience='segment')"),
    )
    language = models.CharField(
        max_length=5,
        choices=[
            ('all', _('All languages')),
            ('en', _('English')),
            ('de', _('German')),
            ('fr', _('French')),
        ],
        default='all',
    )
    audience_snapshot = models.JSONField(
        default=dict,
        blank=True,
        help_text=_("Per-channel eligible counts captured at creation time"),
    )

    # WhatsApp content. A Meta template exists once per language under a single
    # name (same convention as the OTP templates), so the recipient's language
    # selects the localized variant at send time.
    whatsapp_template_name = models.CharField(max_length=255, blank=True)
    whatsapp_parameters = models.JSONField(
        default=dict,
        blank=True,
        help_text=_(
            'Template body parameters keyed by placeholder index, e.g. '
            '{"1": "Hi {first_name}"}. Supports {first_name}, {last_name} '
            'and {email} merge tokens.'
        ),
    )

    # Push content (title/body are translated via modeltranslation en/de/fr).
    push_title = models.CharField(max_length=120, blank=True)
    push_body = models.CharField(max_length=300, blank=True)
    push_url = models.CharField(max_length=500, blank=True, default='/')

    status = models.CharField(
        max_length=12,
        choices=STATUS_CHOICES,
        default='draft',
    )
    scheduled_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    # Set when a dispatch tick claims this campaign; a tick skips campaigns
    # with a fresh heartbeat so overlapping timer invocations never double-send.
    dispatch_heartbeat_at = models.DateTimeField(null=True, blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='created_campaigns',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status', 'scheduled_at']),
        ]
        verbose_name = _("Campaign")
        verbose_name_plural = _("Campaigns")

    def __str__(self):
        return f"{self.name} ({self.get_status_display()})"

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = self.generate_unique_slug(self.name)
        super().save(*args, **kwargs)

    @classmethod
    def generate_unique_slug(cls, name):
        base = slugify(name)[:70] or 'campaign'
        slug = base
        suffix = 2
        while cls.objects.filter(slug=slug).exists():
            slug = f"{base}-{suffix}"
            suffix += 1
        return slug

    @property
    def is_active(self):
        return self.status in ('scheduled', 'sending')

    def can_cancel(self):
        return self.status in ('draft', 'scheduled', 'sending')

    def cancel(self):
        if not self.can_cancel():
            return False
        self.status = 'cancelled'
        self.completed_at = timezone.now()
        self.dispatch_heartbeat_at = None
        self.save(update_fields=[
            'status', 'completed_at', 'dispatch_heartbeat_at', 'updated_at',
        ])
        return True

    @property
    def stats(self):
        """Aggregate per-channel send statistics into one dict.

        Email comes from the linked Newsletter's counters; WhatsApp and push
        from CampaignRecipient rows; WhatsApp delivered/read from the linked
        WhatsAppMessage rows (kept current by the Meta status webhook).
        """
        stats = {}

        if self.CHANNEL_EMAIL in self.channels:
            newsletter = getattr(self, 'email_newsletter', None)
            stats['email'] = {
                'sent': newsletter.total_sent if newsletter else 0,
                'failed': newsletter.total_failed if newsletter else 0,
                'skipped': newsletter.total_skipped if newsletter else 0,
            }

        for channel in (self.CHANNEL_WHATSAPP, self.CHANNEL_PUSH):
            if channel not in self.channels:
                continue
            counts = dict(
                self.recipients.filter(channel=channel)
                .values_list('status')
                .annotate(n=models.Count('id'))
                .values_list('status', 'n')
            )
            stats[channel] = {
                'sent': counts.get('sent', 0),
                'failed': counts.get('failed', 0),
                'skipped': counts.get('skipped', 0),
            }

        if self.CHANNEL_WHATSAPP in stats:
            wa_rows = self.recipients.filter(channel=self.CHANNEL_WHATSAPP)
            delivery = dict(
                wa_rows.filter(whatsapp_message__isnull=False)
                .values_list('whatsapp_message__status')
                .annotate(n=models.Count('id'))
                .values_list('whatsapp_message__status', 'n')
            )
            stats[self.CHANNEL_WHATSAPP]['delivered'] = (
                delivery.get('delivered', 0) + delivery.get('read', 0)
            )
            stats[self.CHANNEL_WHATSAPP]['read'] = delivery.get('read', 0)
            # A send Meta accepted but later reported failed via the status
            # webhook is a failure — the recipient row still says 'sent'
            # (recorded at accept time), so reclassify from the message.
            delivery_failed = wa_rows.filter(
                status='sent', whatsapp_message__status='failed',
            ).count()
            stats[self.CHANNEL_WHATSAPP]['sent'] -= delivery_failed
            stats[self.CHANNEL_WHATSAPP]['failed'] += delivery_failed

        totals = {
            'sent': sum(s.get('sent', 0) for s in stats.values()),
            'failed': sum(s.get('failed', 0) for s in stats.values()),
            'skipped': sum(s.get('skipped', 0) for s in stats.values()),
        }
        stats['totals'] = totals

        click_counts = self.links.aggregate(
            total=models.Count('clicks'),
            unique_users=models.Count('clicks__user', distinct=True),
        )
        stats['clicks'] = {
            'total': click_counts['total'] or 0,
            'unique_users': click_counts['unique_users'] or 0,
        }
        return stats


class CampaignRecipient(models.Model):
    """Per-recipient send state for non-email channels.

    The email leg reuses ``NewsletterRecipient`` via the campaign-linked
    Newsletter; every other channel records one row per (campaign, channel,
    user) here, which is what makes bounded-batch dispatch resumable.
    """

    STATUS_CHOICES = [
        ('pending', _('Pending')),
        ('sent', _('Sent')),
        ('failed', _('Failed')),
        ('skipped', _('Skipped')),
    ]

    CHANNEL_CHOICES = [
        ('whatsapp', _('WhatsApp')),
        ('push', _('Web Push')),
    ]

    campaign = models.ForeignKey(
        Campaign,
        on_delete=models.CASCADE,
        related_name='recipients',
    )
    channel = models.CharField(max_length=20, choices=CHANNEL_CHOICES)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='campaign_receipts',
    )
    status = models.CharField(
        max_length=10,
        choices=STATUS_CHOICES,
        default='pending',
    )
    sent_at = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(blank=True)
    whatsapp_message = models.ForeignKey(
        'hub.WhatsAppMessage',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='campaign_recipients',
    )

    class Meta:
        unique_together = [('campaign', 'channel', 'user')]
        indexes = [
            models.Index(fields=['campaign', 'channel', 'status']),
        ]
        verbose_name = _("Campaign Recipient")
        verbose_name_plural = _("Campaign Recipients")

    def __str__(self):
        return f"{self.user_id} via {self.channel} - {self.get_status_display()}"


class CampaignLink(models.Model):
    """One tracked destination URL per (campaign, channel).

    Outbound campaign links are rewritten to ``/c/<token>/`` which records a
    ``CampaignClick`` and 302s to ``tracked_url`` (the original destination
    with UTM parameters merged in). Recipient attribution travels in a signed
    ``?r=`` query parameter — no per-recipient link rows are needed.
    """

    campaign = models.ForeignKey(
        Campaign,
        on_delete=models.CASCADE,
        related_name='links',
    )
    channel = models.CharField(max_length=20)
    # CharField (not URLField): push links may be site-relative ('/events/').
    original_url = models.CharField(max_length=1000)
    tracked_url = models.CharField(
        max_length=1200,
        help_text=_("original_url with utm_source/medium/campaign merged in"),
    )
    token = models.CharField(max_length=16, unique=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [('campaign', 'channel', 'original_url')]
        verbose_name = _("Campaign Link")
        verbose_name_plural = _("Campaign Links")

    def __str__(self):
        return f"{self.token} → {self.original_url}"


class CampaignClick(models.Model):
    """A single click on a tracked campaign link.

    Data minimization (GDPR): only the link, the attributed user (when the
    signed recipient parameter verifies) and the timestamp are stored — no IP
    address and no user agent, not even hashed.
    """

    link = models.ForeignKey(
        CampaignLink,
        on_delete=models.CASCADE,
        related_name='clicks',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='campaign_clicks',
    )
    clicked_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['link', 'clicked_at']),
        ]
        verbose_name = _("Campaign Click")
        verbose_name_plural = _("Campaign Clicks")

    def __str__(self):
        return f"{self.link.token} @ {self.clicked_at:%Y-%m-%d %H:%M}"
