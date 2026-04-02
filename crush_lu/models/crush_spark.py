"""
Crush Spark model for post-event anonymous admirer journeys.

After attending a Crush.lu event, a user can submit a "Crush Spark" describing
someone they liked. A coach identifies and assigns the recipient, then the
sender creates an anonymous Wonderland Journey. The sender's identity is only
revealed when the recipient completes Chapter 6.
"""
import os
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .profiles import get_crush_photo_storage
from crush_lu.storage import crush_media_storage

crush_photo_storage = get_crush_photo_storage


def spark_chapter1_image_path(instance, filename):
    ext = os.path.splitext(filename)[1].lower()
    unique_filename = f"{uuid.uuid4().hex}{ext}"
    return f"users/{instance.sender_id}/sparks/{instance.pk}/chapter1/{unique_filename}"


def spark_chapter3_image_path(instance, filename):
    ext = os.path.splitext(filename)[1].lower()
    unique_filename = f"{uuid.uuid4().hex}{ext}"
    return f"users/{instance.sender_id}/sparks/{instance.pk}/chapter3/{unique_filename}"


def spark_chapter4_video_path(instance, filename):
    ext = os.path.splitext(filename)[1].lower()
    unique_filename = f"{uuid.uuid4().hex}{ext}"
    return f"users/{instance.sender_id}/sparks/{instance.pk}/chapter4/{unique_filename}"


def spark_chapter5_audio_path(instance, filename):
    ext = os.path.splitext(filename)[1].lower()
    unique_filename = f"{uuid.uuid4().hex}{ext}"
    return f"users/{instance.sender_id}/sparks/{instance.pk}/chapter5/{unique_filename}"


class CrushSpark(models.Model):
    """
    A post-event anonymous admirer request.

    Flow:
    1. REQUESTED - sender describes the person they liked
    2. COACH_ASSIGNED - coach identifies recipient from attendee list
    3. JOURNEY_CREATED - sender builds a Wonderland journey with media
    4. DELIVERED - recipient notified, journey available
    5. COMPLETED - recipient finishes Chapter 6, sender revealed
    """

    class Status(models.TextChoices):
        REQUESTED = "requested", _("Requested")
        PENDING_REVIEW = "pending_review", _("Pending Review")
        COACH_APPROVED = "coach_approved", _("Coach Approved")
        COACH_ASSIGNED = "coach_assigned", _("Coach Assigned")
        JOURNEY_CREATED = "journey_created", _("Journey Created")
        DELIVERED = "delivered", _("Delivered")
        COMPLETED = "completed", _("Completed")
        CANCELLED = "cancelled", _("Cancelled")
        EXPIRED = "expired", _("Expired")

    # Core relationships
    event = models.ForeignKey(
        "crush_lu.MeetupEvent",
        on_delete=models.CASCADE,
        related_name="sparks",
    )
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="sent_sparks",
    )
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="received_sparks",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.REQUESTED,
        db_index=True,
    )

    # Coach mediation
    assigned_coach = models.ForeignKey(
        "crush_lu.CrushCoach",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_sparks",
    )
    sender_description = models.TextField(
        blank=True,
        help_text=_(
            "Describe the person you liked (e.g. 'person in red dress who talked about hiking')"
        ),
    )
    coach_notes = models.TextField(
        blank=True,
        help_text=_("Internal notes from coach about this assignment"),
    )

    # Journey link
    journey = models.ForeignKey(
        "crush_lu.JourneyConfiguration",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="source_spark",
    )
    special_experience = models.ForeignKey(
        "crush_lu.SpecialUserExperience",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="source_spark",
    )

    # Anonymity
    is_sender_revealed = models.BooleanField(default=False)
    revealed_at = models.DateTimeField(null=True, blank=True)

    # Personalization
    sender_message = models.TextField(
        blank=True,
        help_text=_("Personal message revealed in Chapter 6"),
    )

    # =====================================================================
    # MEDIA UPLOADS - same pattern as JourneyGift
    # =====================================================================

    chapter1_image = models.ImageField(
        upload_to=spark_chapter1_image_path,
        storage=crush_photo_storage,
        max_length=255,
        blank=True,
        null=True,
        help_text=_("Photo for Chapter 1 puzzle reveal (recommended: 800x800px)"),
    )

    chapter3_image_1 = models.ImageField(
        upload_to=spark_chapter3_image_path,
        storage=crush_photo_storage,
        max_length=255,
        blank=True,
        null=True,
        help_text=_("First slideshow photo"),
    )
    chapter3_image_2 = models.ImageField(
        upload_to=spark_chapter3_image_path,
        storage=crush_photo_storage,
        max_length=255,
        blank=True,
        null=True,
        help_text=_("Second slideshow photo"),
    )
    chapter3_image_3 = models.ImageField(
        upload_to=spark_chapter3_image_path,
        storage=crush_photo_storage,
        max_length=255,
        blank=True,
        null=True,
        help_text=_("Third slideshow photo"),
    )
    chapter3_image_4 = models.ImageField(
        upload_to=spark_chapter3_image_path,
        storage=crush_photo_storage,
        max_length=255,
        blank=True,
        null=True,
        help_text=_("Fourth slideshow photo"),
    )
    chapter3_image_5 = models.ImageField(
        upload_to=spark_chapter3_image_path,
        storage=crush_photo_storage,
        max_length=255,
        blank=True,
        null=True,
        help_text=_("Fifth slideshow photo"),
    )

    chapter4_video = models.FileField(
        upload_to=spark_chapter4_video_path,
        storage=crush_photo_storage,
        max_length=255,
        blank=True,
        null=True,
        help_text=_("Video message for Chapter 4 (MP4, MOV - max 50MB)"),
    )

    chapter5_letter_music = models.FileField(
        upload_to=spark_chapter5_audio_path,
        storage=crush_photo_storage,
        max_length=255,
        blank=True,
        null=True,
        help_text=_("Background music for Chapter 5 letter (MP3, WAV, M4A - max 10MB)"),
    )

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    coach_assigned_at = models.DateTimeField(null=True, blank=True)
    journey_created_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_("Auto-set to created_at + 14 days"),
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("Crush Spark")
        verbose_name_plural = _("Crush Sparks")
        indexes = [
            models.Index(fields=["event", "status"], name="crushspark_event_status"),
            models.Index(fields=["sender", "event"], name="crushspark_sender_event"),
        ]

    def __str__(self):
        recipient_str = (
            self.recipient.username if self.recipient else "unassigned"
        )
        return (
            f"Spark: {self.sender.username} → {recipient_str} "
            f"({self.get_status_display()}) @ {self.event.title}"
        )

    def save(self, *args, **kwargs):
        # Auto-set expiration on first save
        if not self.pk and not self.expires_at:
            from datetime import timedelta

            self.expires_at = timezone.now() + timedelta(days=14)
        super().save(*args, **kwargs)

    @property
    def is_expired(self):
        return self.expires_at and timezone.now() > self.expires_at

    @property
    def sender_display_name(self):
        """Get sender's display name respecting privacy settings."""
        try:
            return self.sender.crushprofile.display_name
        except Exception:
            return self.sender.first_name or self.sender.username

    @property
    def recipient_display_name(self):
        """Get recipient's display name respecting privacy settings."""
        if not self.recipient:
            return _("Unassigned")
        try:
            return self.recipient.crushprofile.display_name
        except Exception:
            return self.recipient.first_name or self.recipient.username

    @property
    def chapter3_images(self):
        """Return list of non-empty Chapter 3 slideshow images."""
        images = []
        for i in range(1, 6):
            img = getattr(self, f"chapter3_image_{i}", None)
            if img:
                images.append(img)
        return images

    @property
    def has_media(self):
        """Check if any media has been uploaded."""
        return bool(
            self.chapter1_image
            or any(self.chapter3_images)
            or self.chapter4_video
            or self.chapter5_letter_music
        )
