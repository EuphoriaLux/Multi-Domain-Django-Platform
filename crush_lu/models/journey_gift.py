"""
Journey Gift model for the Crush.lu shareable journey experience.

Allows users to gift personalized "Wonderland of You" journeys to non-users
via QR codes.
"""
import os
import secrets
import string
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .profiles import get_crush_photo_storage

# Callable for storage - prevents migration drift
crush_photo_storage = get_crush_photo_storage


def generate_gift_code():
    """Generate a unique gift code in format WOY-XXXXXXXX"""
    # 8 random alphanumeric characters (uppercase + digits)
    chars = string.ascii_uppercase + string.digits
    random_part = ''.join(secrets.choice(chars) for _ in range(8))
    return f"WOY-{random_part}"


def gift_qr_code_path(instance, filename):
    """Generate path for QR code images"""
    return f"journey_gifts/qr/{instance.gift_code}.png"


def gift_chapter1_image_path(instance, filename):
    """Path for Chapter 1 photo puzzle image"""
    ext = os.path.splitext(filename)[1].lower()
    unique_filename = f"{uuid.uuid4().hex}{ext}"
    return f"journey_gifts/{instance.gift_code}/chapter1/{unique_filename}"


def gift_chapter3_image_path(instance, filename):
    """Path for Chapter 3 slideshow images"""
    ext = os.path.splitext(filename)[1].lower()
    unique_filename = f"{uuid.uuid4().hex}{ext}"
    return f"journey_gifts/{instance.gift_code}/chapter3/{unique_filename}"


def gift_chapter4_audio_path(instance, filename):
    """Path for Chapter 4 voice message audio"""
    ext = os.path.splitext(filename)[1].lower()
    unique_filename = f"{uuid.uuid4().hex}{ext}"
    return f"journey_gifts/{instance.gift_code}/chapter4/{unique_filename}"


class JourneyGift(models.Model):
    """
    A gifted Wonderland journey from a sender to a recipient.

    The sender (existing Crush.lu user) creates a gift with personalization details.
    A QR code is generated that the recipient can scan to claim the journey.
    When claimed, a full Wonderland journey is created for the recipient.
    """

    class Status(models.TextChoices):
        PENDING = 'pending', _('Pending')  # Gift created, not yet claimed
        CLAIMED = 'claimed', _('Claimed')  # Recipient signed up and claimed
        COMPLETED = 'completed', _('Completed')  # Journey finished
        EXPIRED = 'expired', _('Expired')  # Gift expired before being claimed

    # Gift identification
    gift_code = models.CharField(
        max_length=16,
        unique=True,
        default=generate_gift_code,
        help_text=_("Unique gift code (e.g., WOY-A1B2C3D4)")
    )

    # Sender (existing Crush.lu user who created the gift)
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='sent_journey_gifts',
        help_text=_("User who created this gift")
    )
    sender_message = models.TextField(
        blank=True,
        help_text=_("Optional personal message from sender")
    )

    # Personalization details (used in journey content, NOT for validation)
    recipient_name = models.CharField(
        max_length=100,
        help_text=_("Name/nickname for the journey story (e.g., 'My Crush', 'Marie')")
    )
    recipient_email = models.EmailField(
        blank=True,
        help_text=_("Optional email for notifications")
    )

    # Journey personalization
    date_first_met = models.DateField(
        help_text=_("Date the sender and recipient first met")
    )
    location_first_met = models.CharField(
        max_length=200,
        help_text=_("Location where they first met")
    )

    # Gift lifecycle
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        help_text=_("Current status of the gift")
    )

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_("Optional expiration date for the gift")
    )
    claimed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_("When the gift was claimed")
    )

    # Recipient (set when gift is claimed)
    claimed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='claimed_journey_gifts',
        help_text=_("User who claimed this gift")
    )

    # Generated journey (created when claimed)
    journey = models.ForeignKey(
        'JourneyConfiguration',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='source_gift',
        help_text=_("Journey created from this gift")
    )
    special_experience = models.ForeignKey(
        'SpecialUserExperience',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='source_gift',
        help_text=_("Special experience created from this gift")
    )

    # QR code image
    qr_code_image = models.ImageField(
        upload_to=gift_qr_code_path,
        blank=True,
        help_text=_("Generated QR code image")
    )

    # =====================================================================
    # MEDIA UPLOADS - Content for journey rewards
    # =====================================================================

    # Chapter 1: Photo Puzzle - Single image revealed as puzzle pieces
    chapter1_image = models.ImageField(
        upload_to=gift_chapter1_image_path,
        storage=crush_photo_storage,
        max_length=255,
        blank=True,
        null=True,
        help_text=_("Photo for the Chapter 1 puzzle reveal (recommended: 800x800px)")
    )

    # Chapter 3: Photo Slideshow - Multiple images (up to 5)
    chapter3_image_1 = models.ImageField(
        upload_to=gift_chapter3_image_path,
        storage=crush_photo_storage,
        max_length=255,
        blank=True,
        null=True,
        help_text=_("First slideshow photo")
    )
    chapter3_image_2 = models.ImageField(
        upload_to=gift_chapter3_image_path,
        storage=crush_photo_storage,
        max_length=255,
        blank=True,
        null=True,
        help_text=_("Second slideshow photo")
    )
    chapter3_image_3 = models.ImageField(
        upload_to=gift_chapter3_image_path,
        storage=crush_photo_storage,
        max_length=255,
        blank=True,
        null=True,
        help_text=_("Third slideshow photo")
    )
    chapter3_image_4 = models.ImageField(
        upload_to=gift_chapter3_image_path,
        storage=crush_photo_storage,
        max_length=255,
        blank=True,
        null=True,
        help_text=_("Fourth slideshow photo")
    )
    chapter3_image_5 = models.ImageField(
        upload_to=gift_chapter3_image_path,
        storage=crush_photo_storage,
        max_length=255,
        blank=True,
        null=True,
        help_text=_("Fifth slideshow photo")
    )

    # Chapter 4: Voice Message - Audio recording
    chapter4_audio = models.FileField(
        upload_to=gift_chapter4_audio_path,
        storage=crush_photo_storage,
        max_length=255,
        blank=True,
        null=True,
        help_text=_("Voice message audio file (MP3, WAV, M4A - max 10MB)")
    )

    # Chapter 4: Video Message - Optional video instead of audio
    chapter4_video = models.FileField(
        upload_to=gift_chapter4_audio_path,  # Same path function works
        storage=crush_photo_storage,
        max_length=255,
        blank=True,
        null=True,
        help_text=_("Video message file (MP4, MOV - max 50MB)")
    )

    class Meta:
        verbose_name = _("Journey Gift")
        verbose_name_plural = _("Journey Gifts")
        ordering = ['-created_at']

    def __str__(self):
        return f"Gift {self.gift_code} from {self.sender} to '{self.recipient_name}'"

    @property
    def is_expired(self):
        """Check if the gift has expired"""
        if self.expires_at and timezone.now() > self.expires_at:
            return True
        return self.status == self.Status.EXPIRED

    @property
    def is_claimable(self):
        """Check if the gift can still be claimed"""
        return (
            self.status == self.Status.PENDING and
            not self.is_expired
        )

    def get_absolute_url(self):
        """URL for the gift landing page"""
        from django.urls import reverse
        return reverse('crush_lu:gift_landing', kwargs={'gift_code': self.gift_code})

    def get_qr_url(self):
        """Full URL for QR code (used in QR generation)"""
        from django.contrib.sites.models import Site
        try:
            # Prefer exact match 'crush.lu' over subdomains like 'test.crush.lu'
            site = Site.objects.filter(domain='crush.lu').first()
            if not site:
                # Fall back to any crush.lu domain
                site = Site.objects.filter(domain__endswith='crush.lu').first()
            domain = site.domain if site else 'crush.lu'
        except Exception:
            domain = 'crush.lu'

        return f"https://{domain}/journey/gift/{self.gift_code}/"

    def mark_expired(self):
        """Mark the gift as expired"""
        self.status = self.Status.EXPIRED
        self.save(update_fields=['status'])

    @property
    def chapter3_images(self):
        """Get list of all uploaded Chapter 3 slideshow images"""
        images = []
        for i in range(1, 6):
            img = getattr(self, f'chapter3_image_{i}', None)
            if img:
                images.append(img)
        return images

    @property
    def has_media(self):
        """Check if any media files have been uploaded"""
        return bool(
            self.chapter1_image or
            self.chapter3_images or
            self.chapter4_audio or
            self.chapter4_video
        )

    def claim(self, user):
        """
        Claim this gift for a user.
        Creates the SpecialUserExperience and JourneyConfiguration.

        Args:
            user: The User who is claiming the gift

        Returns:
            JourneyConfiguration: The created journey

        Raises:
            ValueError: If gift is not claimable
        """
        if not self.is_claimable:
            raise ValueError("This gift cannot be claimed")

        from .profiles import SpecialUserExperience
        from .journey import JourneyConfiguration
        from crush_lu.utils.journey_creator import create_wonderland_chapters

        # 1. Create SpecialUserExperience (directly linked to user)
        special_exp = SpecialUserExperience.objects.create(
            linked_user=user,
            first_name=self.recipient_name,  # For display/personalization only
            last_name="",  # Not needed when using direct link
            is_active=True,
            auto_approve_profile=True,
            vip_badge=True,
            custom_welcome_title=f"Welcome to Your Wonderland, {self.recipient_name}!",
            custom_welcome_message=f"A journey created just for you by {self.sender.first_name}",
        )

        # 2. Create JourneyConfiguration
        journey = JourneyConfiguration.objects.create(
            special_experience=special_exp,
            journey_type='wonderland',
            journey_name="The Wonderland of You",
            total_chapters=6,
            date_first_met=self.date_first_met,
            location_first_met=self.location_first_met,
            is_active=True,
        )

        # 3. Create all 6 chapters with challenges
        create_wonderland_chapters(journey, self.recipient_name, self.date_first_met, self.location_first_met)

        # 4. Attach media to rewards if provided
        self._attach_media_to_rewards(journey)

        # 5. Update gift record
        self.journey = journey
        self.special_experience = special_exp
        self.status = self.Status.CLAIMED
        self.claimed_by = user
        self.claimed_at = timezone.now()
        self.save()

        return journey

    def _attach_media_to_rewards(self, journey):
        """
        Attach uploaded media files to the corresponding JourneyReward objects.

        Chapter 1: Photo Puzzle (photo_reveal)
        Chapter 3: Photo Slideshow (photo_slideshow)
        Chapter 4: Voice/Video Message (voice_message)
        """
        from .journey import JourneyReward
        from django.core.files.base import ContentFile
        import logging

        logger = logging.getLogger(__name__)

        # Chapter 1: Photo Puzzle
        if self.chapter1_image:
            try:
                reward = JourneyReward.objects.filter(
                    chapter__journey=journey,
                    chapter__chapter_number=1,
                    reward_type='photo_reveal'
                ).first()
                if reward:
                    # Copy file to reward's storage path
                    reward.photo.save(
                        f"puzzle_{self.gift_code}.jpg",
                        ContentFile(self.chapter1_image.read()),
                        save=True
                    )
                    self.chapter1_image.seek(0)  # Reset file pointer
                    logger.info(f"Attached Chapter 1 image to reward {reward.id}")
            except Exception as e:
                logger.error(f"Failed to attach Chapter 1 image: {e}")

        # Chapter 3: Photo Slideshow - Store images in JSON metadata
        if self.chapter3_images:
            try:
                reward = JourneyReward.objects.filter(
                    chapter__journey=journey,
                    chapter__chapter_number=3,
                    reward_type='photo_slideshow'
                ).first()
                if reward:
                    # For slideshow, use the first image as the main photo
                    # Additional images would need a separate model or JSON storage
                    first_image = self.chapter3_images[0]
                    reward.photo.save(
                        f"slideshow_{self.gift_code}_1.jpg",
                        ContentFile(first_image.read()),
                        save=True
                    )
                    first_image.seek(0)
                    logger.info(f"Attached Chapter 3 slideshow image to reward {reward.id}")
            except Exception as e:
                logger.error(f"Failed to attach Chapter 3 images: {e}")

        # Chapter 4: Voice/Video Message
        if self.chapter4_audio or self.chapter4_video:
            try:
                reward = JourneyReward.objects.filter(
                    chapter__journey=journey,
                    chapter__chapter_number=4,
                    reward_type='voice_message'
                ).first()
                if reward:
                    if self.chapter4_video:
                        reward.video_file.save(
                            f"message_{self.gift_code}.mp4",
                            ContentFile(self.chapter4_video.read()),
                            save=True
                        )
                        self.chapter4_video.seek(0)
                        logger.info(f"Attached Chapter 4 video to reward {reward.id}")
                    elif self.chapter4_audio:
                        reward.audio_file.save(
                            f"message_{self.gift_code}.mp3",
                            ContentFile(self.chapter4_audio.read()),
                            save=True
                        )
                        self.chapter4_audio.seek(0)
                        logger.info(f"Attached Chapter 4 audio to reward {reward.id}")
            except Exception as e:
                logger.error(f"Failed to attach Chapter 4 media: {e}")
