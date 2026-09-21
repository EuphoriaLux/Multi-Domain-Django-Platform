"""Enquiries and follow-up, independently of appointment/prepayment status."""

import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .storage import private_storage


def lead_photo_path(instance, filename):
    return f"leads/{instance.lead_id}/{uuid.uuid4().hex}.jpg"


class ArboristLead(models.Model):
    class Status(models.TextChoices):
        NEW = "new", _("New enquiry")
        REVIEW = "review", _("Under review")
        WAITING = "waiting", _("Waiting for customer")
        QUALIFIED = "qualified", _("Qualified")
        CLOSED = "closed", _("Closed")

    class Delivery(models.TextChoices):
        PENDING = "pending", _("Pending")
        SENT = "sent", _("Sent")
        FAILED = "failed", _("Failed — retry available")
        SKIPPED = "skipped", _("No email supplied")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    submission_id = models.UUIDField(unique=True, default=uuid.uuid4, editable=False)
    name = models.CharField(_("Name"), max_length=100)
    email = models.EmailField(_("Email"), blank=True)
    phone = models.CharField(_("Phone"), max_length=50, blank=True)
    postal_code = models.CharField(_("Postal code"), max_length=4, blank=True)
    service = models.CharField(_("Service"), max_length=30, blank=True)
    message = models.TextField(_("Your Message"), max_length=5000)
    is_urgent = models.BooleanField(_("Urgent situation"), default=False)
    language = models.CharField(max_length=2, default="en")
    status = models.CharField(
        _("Status"), max_length=15, choices=Status, default=Status.NEW, db_index=True
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="arborist_leads",
    )
    next_action = models.CharField(_("Next action"), max_length=250, blank=True)
    follow_up_at = models.DateTimeField(
        _("Follow up at"), null=True, blank=True, db_index=True
    )
    review_notes = models.TextField(_("Internal review notes"), blank=True)
    photo_notes = models.TextField(
        _("Missing photos / access notes"), blank=True, max_length=2000
    )
    source = models.CharField(_("Source"), max_length=30, default="website")
    first_attribution = models.JSONField(default=dict, blank=True)
    last_attribution = models.JSONField(default=dict, blank=True)
    staff_delivery = models.CharField(
        max_length=10, choices=Delivery, default=Delivery.PENDING
    )
    customer_delivery = models.CharField(
        max_length=10, choices=Delivery, default=Delivery.PENDING
    )
    notification_attempted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("Arborist enquiry")
        verbose_name_plural = _("Arborist enquiries")

    def __str__(self):
        return f"{self.name} — {str(self.pk)[:8]}"


class LeadPhoto(models.Model):
    class Category(models.TextChoices):
        ROOT = "root", _("Trunk–ground junction")
        WHOLE = "whole", _("Whole tree and surroundings")
        LEAF = "leaf", _("Leaves and twig")
        SYMPTOM = "symptom", _("Visible symptoms")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    lead = models.ForeignKey(
        ArboristLead, on_delete=models.CASCADE, related_name="photos"
    )
    upload_id = models.UUIDField(unique=True, editable=False)
    tree_label = models.CharField(_("Tree label"), max_length=50, default="1")
    category = models.CharField(_("Photo category"), max_length=10, choices=Category)
    image = models.ImageField(storage=private_storage, upload_to=lead_photo_path)
    created_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return f"{self.tree_label}: {self.get_category_display()}"


class LeadEvent(models.Model):
    lead = models.ForeignKey(
        ArboristLead, on_delete=models.CASCADE, related_name="events"
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL
    )
    description = models.CharField(max_length=500)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-created_at"]
