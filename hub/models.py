from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q


class HubProfile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="hub_profile",
    )
    organization = models.CharField(max_length=255, blank=True, default="")
    primary_contact = models.CharField(max_length=255, blank=True, default="")
    phone = models.CharField(max_length=50, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.organization or self.user.get_username()


class HubRequest(models.Model):
    class Status(models.TextChoices):
        OPEN = "Open", "Open"
        IN_REVIEW = "In Review", "In Review"
        WAITING_FOR_CLIENT = "Waiting for Client", "Waiting for Client"
        CLOSED = "Closed", "Closed"

    class Priority(models.TextChoices):
        LOW = "Low", "Low"
        MEDIUM = "Medium", "Medium"
        HIGH = "High", "High"

    class Category(models.TextChoices):
        PROJECT = "Project", "Project"
        TECHNICAL = "Technical", "Technical"
        BILLING = "Billing", "Billing"
        GENERAL = "General", "General"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="hub_requests",
    )
    subject = models.CharField(max_length=255)
    summary = models.TextField(blank=True, default="")
    category = models.CharField(
        max_length=20, choices=Category.choices, default=Category.GENERAL
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.OPEN
    )
    priority = models.CharField(
        max_length=20, choices=Priority.choices, default=Priority.MEDIUM
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.subject} ({self.get_status_display()})"


class HubResource(models.Model):
    class Type(models.TextChoices):
        GUIDE = "Guide", "Guide"
        REPORT = "Report", "Report"
        ASSET = "Asset", "Asset"
        INVOICE = "Invoice", "Invoice"

    title = models.CharField(max_length=255)
    summary = models.TextField(blank=True, default="")
    type = models.CharField(max_length=20, choices=Type.choices, default=Type.GUIDE)
    url = models.URLField(blank=True, default="")
    is_public = models.BooleanField(default=True)
    audience = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        blank=True,
        related_name="hub_resources",
        help_text="Leave empty + is_public=True to expose to every authenticated user.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return self.title


class HubTimelineEvent(models.Model):
    class Kind(models.TextChoices):
        SYSTEM = "system", "System"
        REQUEST = "request", "Request"
        NOTE = "note", "Note"
        MILESTONE = "milestone", "Milestone"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="hub_timeline",
    )
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.SYSTEM)
    title = models.CharField(max_length=255)
    body = models.TextField(blank=True, default="")
    occurred_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-occurred_at"]

    def __str__(self):
        return f"{self.title} @ {self.occurred_at:%Y-%m-%d}"


class Location(models.Model):
    class EventType(models.TextChoices):
        COOKING_WORKSHOP = "Cooking workshop", "Cooking workshop"
        WINE_TASTING = "Wine tasting", "Wine tasting"
        SPEED_DATING = "Speed dating", "Speed dating"
        OUTDOOR_ACTIVITY = "Outdoor activity", "Outdoor activity"
        QUIZ_NIGHT = "Quiz night", "Quiz night"

    class PartnershipStage(models.TextChoices):
        PROSPECT = "Prospect", "Prospect"
        NEGOTIATING = "Negotiating", "Negotiating"
        ACTIVE = "Active", "Active"
        PAUSED = "Paused", "Paused"
        ARCHIVED = "Archived", "Archived"

    name = models.CharField(max_length=255)
    address = models.TextField()
    city = models.CharField(max_length=255)
    country = models.CharField(max_length=100, default="Luxembourg")

    max_capacity = models.PositiveIntegerField()
    seated_capacity = models.PositiveIntegerField(blank=True, null=True)
    has_outdoor_space = models.BooleanField(default=False)
    has_kitchen = models.BooleanField(default=False)
    has_private_room = models.BooleanField(default=False)
    has_sound_system = models.BooleanField(default=False)
    compatible_event_types = models.JSONField(default=list, blank=True)

    partnership_stage = models.CharField(
        max_length=20,
        choices=PartnershipStage.choices,
        default=PartnershipStage.PROSPECT,
    )
    account_manager = models.CharField(max_length=255, blank=True, default="")
    commercial_terms = models.TextField(blank=True, default="")
    partner_since = models.DateField(blank=True, null=True)

    last_contact_date = models.DateField(blank=True, null=True)
    next_action = models.CharField(max_length=255, blank=True, default="")
    next_action_date = models.DateField(blank=True, null=True)
    notes = models.TextField(blank=True, default="")
    tags = models.JSONField(default=list, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.CheckConstraint(
                condition=Q(max_capacity__gte=1),
                name="hub_location_max_capacity_positive",
            ),
            models.CheckConstraint(
                condition=(
                    Q(seated_capacity__isnull=True)
                    | Q(
                        seated_capacity__gte=1,
                        seated_capacity__lte=F("max_capacity"),
                    )
                ),
                name="hub_location_valid_seated_capacity",
            ),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.max_capacity is not None and self.max_capacity < 1:
            errors["max_capacity"] = "Maximum capacity must be at least 1."
        if self.seated_capacity is not None and (
            self.seated_capacity < 1
            or self.max_capacity is None
            or self.seated_capacity > self.max_capacity
        ):
            errors["seated_capacity"] = (
                "Seated capacity must be between 1 and maximum capacity."
            )
        allowed_event_types = set(self.EventType.values)
        if not isinstance(self.compatible_event_types, list) or any(
            not isinstance(event_type, str) or event_type not in allowed_event_types
            for event_type in self.compatible_event_types
        ):
            errors["compatible_event_types"] = (
                "Use a list containing only supported event types."
            )
        if not isinstance(self.tags, list) or any(
            not isinstance(tag, str) or not tag.strip() for tag in self.tags
        ):
            errors["tags"] = "Use a list containing only non-empty tag names."
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"{self.name} ({self.city})"


class LocationContact(models.Model):
    location = models.OneToOneField(
        Location,
        on_delete=models.CASCADE,
        related_name="primary_contact",
    )
    name = models.CharField(max_length=255)
    role = models.CharField(max_length=255, blank=True, default="")
    email = models.EmailField(blank=True, default="")
    phone = models.CharField(max_length=50, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} — {self.location.name}"


class WhatsAppMessage(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        SENT = "sent", "Sent"
        DELIVERED = "delivered", "Delivered"
        READ = "read", "Read"
        FAILED = "failed", "Failed"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="hub_whatsapp_messages",
    )
    wa_message_id = models.CharField(
        max_length=255, blank=True, default="", db_index=True
    )
    recipient = models.CharField(max_length=32)
    template_name = models.CharField(max_length=255)
    language = models.CharField(max_length=16)
    parameters = models.JSONField(default=dict, blank=True)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.QUEUED
    )
    status_history = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.template_name} → {self.recipient} [{self.status}]"


class WhatsAppInboundMessage(models.Model):
    """A message a user sent TO our WhatsApp number (Cloud API inbound).

    A Cloud API number has no phone-app inbox, so an inbound reply is otherwise
    only a transient webhook event that the status-only webhook used to drop.
    Persisting each one lets the hub CRM show a support inbox.

    ``wa_message_id`` is unique so Meta's webhook retries (it re-POSTs until it
    gets a 200) are idempotent — the second delivery is a no-op.
    """

    wa_message_id = models.CharField(max_length=255, unique=True)
    from_number = models.CharField(max_length=32, db_index=True)
    contact_name = models.CharField(max_length=255, blank=True, default="")
    message_type = models.CharField(max_length=32, default="text")
    # Body for text messages; non-text types (image, audio, …) keep the full
    # message object in ``payload`` and leave this blank.
    text = models.TextField(blank=True, default="")
    payload = models.JSONField(default=dict, blank=True)
    received_at = models.DateTimeField()
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-received_at"]
        indexes = [models.Index(fields=["is_read", "-received_at"])]

    def __str__(self):
        return f"{self.from_number} ({self.message_type}) @ {self.received_at:%Y-%m-%d %H:%M}"


class SocialPost(models.Model):
    class Pillar(models.TextChoices):
        EVENT_RECAP = "event_recap", "Event Recap"
        DATING_TIP = "dating_tip", "Dating Tip"
        MILESTONE = "milestone", "Milestone"
        COMMUNITY = "community", "Community"
        PROMO = "promo", "Promotion"

    class Language(models.TextChoices):
        FR = "fr", "French"
        EN = "en", "English"
        DE = "de", "German"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PENDING_REVIEW = "pending_review", "Pending Review"
        APPROVED = "approved", "Approved"
        SCHEDULED = "scheduled", "Scheduled"
        PUBLISHED = "published", "Published"
        FAILED = "failed", "Failed"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="hub_social_posts",
    )
    featured_profile = models.ForeignKey(
        "crush_lu.CrushProfile",
        on_delete=models.CASCADE,
        related_name="hub_social_posts",
        blank=True,
        null=True,
    )
    source_event = models.ForeignKey(
        "crush_lu.MeetupEvent",
        on_delete=models.SET_NULL,
        related_name="social_promotion_posts",
        blank=True,
        null=True,
        help_text="Published Crush event whose existing copy was reused for this post.",
    )
    pillar = models.CharField(
        max_length=32, choices=Pillar.choices, default=Pillar.EVENT_RECAP
    )
    language = models.CharField(
        max_length=8, choices=Language.choices, default=Language.FR
    )
    platforms = models.JSONField(default=list, blank=True)
    buffer_profile_ids = models.JSONField(default=list, blank=True)
    buffer_profile_platforms = models.JSONField(default=dict, blank=True)
    dispatched_platforms = models.JSONField(default=list, blank=True)
    hook = models.CharField(max_length=255, blank=True, default="")
    content = models.TextField(blank=True, default="")
    media_url = models.URLField(blank=True, null=True, default=None)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.DRAFT
    )
    scheduled_for = models.DateTimeField(blank=True, null=True, default=None)
    buffer_id = models.CharField(max_length=255, blank=True, default="")
    article_id = models.CharField(max_length=255, blank=True, default="")
    status_history = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"[{self.language.upper()}] {self.hook or self.content[:30]} ({self.status})"
