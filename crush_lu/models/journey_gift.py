"""
Journey Gift model for the Crush.lu shareable journey experience.

Allows users to gift personalized "Wonderland of You" journeys to non-users
via QR codes.
"""
import logging
import os
import secrets
import string
import uuid

from django.conf import settings
from django.db import models, transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .profiles import get_crush_photo_storage

logger = logging.getLogger(__name__)

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
    """
    Path for Chapter 1 photo puzzle image.
    Structure: users/{sender_id}/journey_gifts/{gift_code}/chapter1/{uuid}.{ext}

    Benefits:
    - All user content in one place for GDPR deletion
    - Consistent with CrushProfile photo storage
    - Easy to find all gifts created by a user
    """
    ext = os.path.splitext(filename)[1].lower()
    unique_filename = f"{uuid.uuid4().hex}{ext}"
    return f"users/{instance.sender_id}/journey_gifts/{instance.gift_code}/chapter1/{unique_filename}"


def gift_chapter3_image_path(instance, filename):
    """
    Path for Chapter 3 slideshow images.
    Structure: users/{sender_id}/journey_gifts/{gift_code}/chapter3/{uuid}.{ext}
    """
    ext = os.path.splitext(filename)[1].lower()
    unique_filename = f"{uuid.uuid4().hex}{ext}"
    return f"users/{instance.sender_id}/journey_gifts/{instance.gift_code}/chapter3/{unique_filename}"


def gift_chapter4_audio_path(instance, filename):
    """
    LEGACY: Path for Chapter 4 media (audio/video).
    Kept for backwards compatibility with existing migrations.
    Structure: users/{sender_id}/journey_gifts/{gift_code}/chapter4/{uuid}.{ext}
    """
    ext = os.path.splitext(filename)[1].lower()
    unique_filename = f"{uuid.uuid4().hex}{ext}"
    return f"users/{instance.sender_id}/journey_gifts/{instance.gift_code}/chapter4/{unique_filename}"


# Alias for clearer naming in new code
gift_chapter4_video_path = gift_chapter4_audio_path


def gift_chapter5_audio_path(instance, filename):
    """
    Path for Chapter 5 Future Letter music/audio.
    Structure: users/{sender_id}/journey_gifts/{gift_code}/chapter5/{uuid}.{ext}
    """
    ext = os.path.splitext(filename)[1].lower()
    unique_filename = f"{uuid.uuid4().hex}{ext}"
    return f"users/{instance.sender_id}/journey_gifts/{instance.gift_code}/chapter5/{unique_filename}"


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

    # Chapter 4: Video Message
    chapter4_video = models.FileField(
        upload_to=gift_chapter4_video_path,
        storage=crush_photo_storage,
        max_length=255,
        blank=True,
        null=True,
        help_text=_("Video message file for Chapter 4 (MP4, MOV - max 50MB)")
    )

    # Chapter 5: Future Letter Music - Background music for the letter
    chapter5_letter_music = models.FileField(
        upload_to=gift_chapter5_audio_path,
        storage=crush_photo_storage,
        max_length=255,
        blank=True,
        null=True,
        help_text=_("Background music for Chapter 5 Future Letter (MP3, WAV, M4A - max 10MB)")
    )

    # Legacy field - kept for backwards compatibility with existing gifts
    # New gifts should use chapter5_letter_music instead
    chapter4_audio = models.FileField(
        upload_to=gift_chapter4_video_path,
        storage=crush_photo_storage,
        max_length=255,
        blank=True,
        null=True,
        help_text=_("DEPRECATED: Use chapter5_letter_music instead")
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
            self.chapter4_video or
            self.chapter5_letter_music or
            self.chapter4_audio  # Legacy field
        )

    @property
    def letter_music(self):
        """Get the letter music file, checking both new and legacy fields"""
        return self.chapter5_letter_music or self.chapter4_audio

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

        # 1. Get or create SpecialUserExperience (directly linked to user)
        # A user can only have ONE SpecialUserExperience, but can have multiple journeys
        special_exp = SpecialUserExperience.objects.filter(linked_user=user).first()

        if special_exp:
            # User already has a SpecialUserExperience - update it with new gift info
            special_exp.is_active = True
            special_exp.vip_badge = True
            special_exp.first_name = user.first_name  # Sync name for consistent matching
            special_exp.last_name = user.last_name    # Sync name for consistent matching
            special_exp.save(update_fields=['is_active', 'vip_badge', 'first_name', 'last_name'])
        else:
            # Create new SpecialUserExperience for first-time gift recipient
            special_exp = SpecialUserExperience.objects.create(
                linked_user=user,
                first_name=user.first_name,  # Use actual user name for consistent matching
                last_name=user.last_name,    # Use actual user name for consistent matching
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

        Media mapping:
        - Chapter 1: Photo Puzzle (photo_reveal)
        - Chapter 3: Photo Slideshow (photo_slideshow)
        - Chapter 4: Video Message (voice_message)
        - Chapter 5: Future Letter Music (future_letter)

        Uses atomic transaction to ensure all-or-nothing attachment.
        Logs warnings when expected rewards are not found.
        """
        from .journey import JourneyReward
        from django.core.files.base import ContentFile

        attached = {'chapter1': False, 'chapter3': False, 'chapter4': False, 'chapter5': False}
        errors = []

        # Use atomic transaction to ensure consistency
        with transaction.atomic():
            # Chapter 1: Photo Puzzle
            if self.chapter1_image:
                try:
                    reward = JourneyReward.objects.filter(
                        chapter__journey=journey,
                        chapter__chapter_number=1,
                        reward_type='photo_reveal'
                    ).first()

                    if not reward:
                        logger.warning(
                            f"Gift {self.gift_code}: Chapter 1 photo_reveal reward not found. "
                            f"Image will not be attached."
                        )
                    else:
                        # Seek to start before passing file object (avoids memory spike)
                        self.chapter1_image.seek(0)
                        reward.photo.save(
                            f"puzzle_{self.gift_code}.jpg",
                            self.chapter1_image,  # Pass file object directly
                            save=True
                        )
                        attached['chapter1'] = True
                        logger.info(f"Attached Chapter 1 image to reward {reward.id}")
                except Exception as e:
                    errors.append(f"Chapter 1 image: {e}")
                    logger.error(f"Failed to attach Chapter 1 image: {e}", exc_info=True)

            # Chapter 3: Photo Slideshow
            if self.chapter3_images:
                try:
                    reward = JourneyReward.objects.filter(
                        chapter__journey=journey,
                        chapter__chapter_number=3,
                        reward_type='photo_slideshow'
                    ).first()

                    if not reward:
                        logger.warning(
                            f"Gift {self.gift_code}: Chapter 3 photo_slideshow reward not found. "
                            f"Slideshow images will not be attached."
                        )
                    else:
                        slideshow_photos = []
                        images = self.chapter3_images
                        images_attached = 0

                        for idx, image in enumerate(images):
                            try:
                                # Always seek to start before reading
                                image.seek(0)
                                image_content = image.read()
                                image.seek(0)  # Reset for potential re-read

                                if idx == 0:
                                    # First image: save to legacy photo field
                                    reward.photo.save(
                                        f"slideshow_{self.gift_code}_1.jpg",
                                        ContentFile(image_content),
                                        save=False
                                    )
                                    images_attached += 1
                                    logger.info(f"Attached slideshow image 1 to reward {reward.id}")
                                else:
                                    # Additional images: save to storage
                                    from .journey import journey_reward_photo_path

                                    storage = get_crush_photo_storage()
                                    filename = f"slideshow_{self.gift_code}_{idx + 1}.jpg"
                                    file_path = journey_reward_photo_path(reward, filename)
                                    saved_path = storage.save(file_path, ContentFile(image_content))
                                    slideshow_photos.append({
                                        'path': saved_path,
                                        'order': idx
                                    })
                                    images_attached += 1
                                    logger.info(f"Attached slideshow image {idx + 1} to storage")

                            except Exception as img_err:
                                logger.error(f"Failed to attach slideshow image {idx + 1}: {img_err}")
                                continue

                        reward.slideshow_photos = slideshow_photos
                        reward.save()
                        attached['chapter3'] = images_attached > 0
                        logger.info(f"Attached {images_attached} slideshow images to reward {reward.id}")

                except Exception as e:
                    errors.append(f"Chapter 3 slideshow: {e}")
                    logger.error(f"Failed to attach Chapter 3 images: {e}", exc_info=True)

            # Chapter 4: Video Message
            if self.chapter4_video:
                try:
                    reward = JourneyReward.objects.filter(
                        chapter__journey=journey,
                        chapter__chapter_number=4,
                        reward_type='voice_message'
                    ).first()

                    if not reward:
                        logger.warning(
                            f"Gift {self.gift_code}: Chapter 4 voice_message reward not found. "
                            f"Video will not be attached."
                        )
                    else:
                        # Seek to start before passing file object
                        self.chapter4_video.seek(0)
                        reward.video_file.save(
                            f"message_{self.gift_code}.mp4",
                            self.chapter4_video,  # Pass file object directly (no memory spike)
                            save=True
                        )
                        attached['chapter4'] = True
                        logger.info(f"Attached Chapter 4 video to reward {reward.id}")
                except Exception as e:
                    errors.append(f"Chapter 4 video: {e}")
                    logger.error(f"Failed to attach Chapter 4 video: {e}", exc_info=True)

            # Chapter 5: Future Letter Music
            # Check both new field (chapter5_letter_music) and legacy field (chapter4_audio)
            letter_music = self.chapter5_letter_music or self.chapter4_audio
            if letter_music:
                try:
                    reward = JourneyReward.objects.filter(
                        chapter__journey=journey,
                        chapter__chapter_number=5,
                        reward_type='future_letter'
                    ).first()

                    if not reward:
                        logger.warning(
                            f"Gift {self.gift_code}: Chapter 5 future_letter reward not found. "
                            f"Letter music will not be attached."
                        )
                    else:
                        # Seek to start before passing file object
                        letter_music.seek(0)
                        reward.audio_file.save(
                            f"letter_music_{self.gift_code}.mp3",
                            letter_music,  # Pass file object directly
                            save=True
                        )
                        attached['chapter5'] = True
                        logger.info(f"Attached Chapter 5 letter music to reward {reward.id}")
                except Exception as e:
                    errors.append(f"Chapter 5 letter music: {e}")
                    logger.error(f"Failed to attach Chapter 5 letter music: {e}", exc_info=True)

        # Log summary
        attached_count = sum(1 for v in attached.values() if v)
        total_media = sum(1 for v in [
            self.chapter1_image, self.chapter3_images, self.chapter4_video,
            self.chapter5_letter_music or self.chapter4_audio
        ] if v)

        if errors:
            logger.warning(
                f"Gift {self.gift_code}: Media attachment completed with errors. "
                f"Attached {attached_count}/{total_media} media items. Errors: {errors}"
            )
        else:
            logger.info(
                f"Gift {self.gift_code}: Successfully attached {attached_count} media items to rewards."
            )
