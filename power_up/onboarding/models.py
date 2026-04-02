import uuid

from django.conf import settings
from django.db import models


class OnboardingSession(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        EMAIL_GENERATED = "email_generated", "Email Generated"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    class Language(models.TextChoices):
        EN = "en", "English"
        DE = "de", "German"
        FR = "fr", "French"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    group = models.ForeignKey(
        "crm.CustomerGroup",
        on_delete=models.CASCADE,
        related_name="onboarding_sessions",
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.DRAFT
    )
    language = models.CharField(
        max_length=5, choices=Language.choices, default=Language.EN
    )
    recipient = models.ForeignKey(
        "crm.AuthorizedContact",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="onboarding_sessions",
        help_text="Authorized contact who receives the onboarding email (typically Admin role)",
    )
    # Snapshot fields — populated from recipient on save for historical record
    contact_name = models.CharField(max_length=255, blank=True, default="")
    contact_email = models.EmailField(blank=True, default="")
    tenants = models.ManyToManyField(
        "crm.Tenant",
        blank=True,
        related_name="onboarding_sessions",
        help_text="Tenants included in this onboarding (GDAP, RBAC, etc.)",
    )
    meeting_slots = models.JSONField(
        default=list, blank=True, help_text="List of selected slot datetimes (ISO format)"
    )
    include_gdap = models.BooleanField(default=True)
    include_rbac = models.BooleanField(default=True)
    include_conditional_access = models.BooleanField(default=False)
    additional_notes = models.TextField(blank=True, default="")
    sender = models.ForeignKey(
        "crm.ServiceExpert",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="onboarding_sessions",
        help_text="Service expert who sends the onboarding email",
    )
    # Snapshot fields — populated from sender on save for historical record
    sender_name = models.CharField(max_length=255, blank=True, default="")
    sender_title = models.CharField(max_length=255, blank=True, default="")
    sender_email = models.EmailField(blank=True, default="")
    sender_phone = models.CharField(max_length=50, blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Onboarding Session"

    def __str__(self):
        return f"Onboarding: {self.group.name} ({self.get_status_display()})"


class OnboardingEmail(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(
        OnboardingSession, on_delete=models.CASCADE, related_name="emails"
    )
    subject = models.CharField(max_length=500)
    html_content = models.TextField(help_text="Generated HTML email")
    plain_content = models.TextField(help_text="Generated plain text email")
    recipient_email = models.EmailField()
    downloaded_at = models.DateTimeField(
        null=True, blank=True, help_text="When .eml was downloaded"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Onboarding Email"

    def __str__(self):
        return f"Email to {self.recipient_email} ({self.session.group.name})"
