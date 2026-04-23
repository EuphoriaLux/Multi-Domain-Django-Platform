from django.conf import settings
from django.db import models


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
