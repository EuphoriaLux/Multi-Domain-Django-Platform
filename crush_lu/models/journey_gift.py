"""
Journey Gift model for the Crush.lu shareable journey experience.

Allows users to gift personalized "Wonderland of You" journeys to non-users
via QR codes.
"""
import logging
import os
import secrets
import string
import time
import uuid
from dataclasses import dataclass, field
from typing import List, Optional

from django.conf import settings
from django.db import models, transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .profiles import get_crush_photo_storage
from crush_lu.storage import crush_media_storage

logger = logging.getLogger(__name__)


@dataclass
class MediaAttachmentResult:
    """Result of attaching media files to journey rewards."""
    chapter: str
    success: bool
    error_message: Optional[str] = None
    files_attached: int = 0
    files_total: int = 0
    is_critical: bool = False  # Critical failures should rollback entire claim

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
        CLAIM_FAILED = 'claim_failed', _('Claim Failed')  # Gift claim failed (retryable)

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
        storage=crush_media_storage,  # Public container for QR scanning
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

    # Error tracking for failed claims
    claim_error_message = models.TextField(
        blank=True,
        help_text=_("Error message if claim failed")
    )
    claim_attempts = models.IntegerField(
        default=0,
        help_text=_("Number of claim attempts made")
    )

    # Class constant for maximum claim attempts
    MAX_CLAIM_ATTEMPTS = 3

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
        """
        Check if the gift can still be claimed.

        A gift is claimable if:
        - Status is PENDING or CLAIM_FAILED (allows retries)
        - Not expired
        - Claim attempts haven't exceeded the maximum
        """
        return (
            self.status in [self.Status.PENDING, self.Status.CLAIM_FAILED] and
            not self.is_expired and
            self.claim_attempts < self.MAX_CLAIM_ATTEMPTS
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
            ValueError: If gift is not claimable or claim fails
        """
        if not self.is_claimable:
            if self.claim_attempts >= self.MAX_CLAIM_ATTEMPTS:
                raise ValueError(
                    f"Maximum claim attempts ({self.MAX_CLAIM_ATTEMPTS}) exceeded. "
                    "Please contact support for assistance."
                )
            raise ValueError("This gift cannot be claimed")

        from .profiles import SpecialUserExperience
        from .journey import JourneyConfiguration
        from crush_lu.utils.journey_creator import create_wonderland_chapters

        # Increment claim attempts at the start
        self.claim_attempts += 1
        self.save(update_fields=['claim_attempts'])

        # Use atomic transaction with savepoints for rollback control
        try:
            with transaction.atomic():
                # SAVEPOINT 1: User link
                sid_user = transaction.savepoint()

                # 1. Get or create SpecialUserExperience (directly linked to user)
                # A user can only have ONE SpecialUserExperience, but can have multiple journeys
                try:
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
                except Exception as e:
                    transaction.savepoint_rollback(sid_user)
                    error_msg = f"Failed to create/update SpecialUserExperience: {str(e)}"
                    logger.error(f"Gift {self.gift_code}: {error_msg}", exc_info=True)
                    # Don't call _mark_claim_failed here - it will be called in outer except
                    raise ValueError(error_msg)

                # SAVEPOINT 2: Journey creation
                sid_journey = transaction.savepoint()

                # 2. Create JourneyConfiguration
                try:
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
                    create_wonderland_chapters(
                        journey,
                        self.recipient_name,
                        self.date_first_met,
                        self.location_first_met
                    )
                except Exception as e:
                    transaction.savepoint_rollback(sid_journey)
                    error_msg = f"Failed to create journey: {str(e)}"
                    logger.error(f"Gift {self.gift_code}: {error_msg}", exc_info=True)
                    # Don't call _mark_claim_failed here - it will be called in outer except
                    raise ValueError(error_msg)

                # SAVEPOINT 3: Media attachment
                sid_media = transaction.savepoint()

                # 4. Attach media to rewards if provided
                try:
                    media_results = self._attach_media_to_rewards(journey)

                    # Check for critical failures
                    critical_failures = [r for r in media_results if not r.success and r.is_critical]
                    if critical_failures:
                        # Rollback journey creation if critical media fails
                        transaction.savepoint_rollback(sid_media)
                        error_details = [f"{r.chapter}: {r.error_message}" for r in critical_failures]
                        error_msg = f"Critical media attachment failed: {'; '.join(error_details)}"
                        logger.error(f"Gift {self.gift_code}: {error_msg}")
                        # Don't call _mark_claim_failed here - it will be called in outer except
                        raise ValueError(error_msg)

                    # Log non-critical failures but continue
                    non_critical_failures = [r for r in media_results if not r.success and not r.is_critical]
                    if non_critical_failures:
                        error_details = [f"{r.chapter}: {r.error_message}" for r in non_critical_failures]
                        logger.warning(
                            f"Gift {self.gift_code}: Non-critical media attachment failures: {error_details}"
                        )

                except Exception as e:
                    transaction.savepoint_rollback(sid_media)
                    error_msg = f"Media attachment failed unexpectedly: {str(e)}"
                    logger.error(f"Gift {self.gift_code}: {error_msg}", exc_info=True)
                    # Don't call _mark_claim_failed here - it will be called in outer except
                    raise ValueError(error_msg)

                # 5. Update gift record - SUCCESS!
                self.journey = journey
                self.special_experience = special_exp
                self.status = self.Status.CLAIMED
                self.claimed_by = user
                self.claimed_at = timezone.now()
                self.claim_error_message = ""  # Clear any previous errors
                self.save()

                logger.info(f"Gift {self.gift_code}: Successfully claimed by user {user.id}")
                return journey

        except ValueError as e:
            # ValueError was raised from inner exception handlers
            # Mark as failed now that we're outside the atomic block
            self._mark_claim_failed(str(e))
            raise
        except Exception as e:
            # Catch-all for unexpected errors
            error_msg = f"Unexpected error during claim: {str(e)}"
            logger.error(f"Gift {self.gift_code}: {error_msg}", exc_info=True)
            self._mark_claim_failed(error_msg)
            raise ValueError(error_msg)

    def _mark_claim_failed(self, error_message):
        """
        Mark the gift claim as failed with error details.

        Should only be called outside of atomic blocks to ensure the status
        update is not rolled back.
        """
        self.status = self.Status.CLAIM_FAILED
        self.claim_error_message = error_message[:1000]  # Limit length
        self.save(update_fields=['status', 'claim_error_message'])

    def _attach_media_to_rewards(self, journey, max_retries=3):
        """
        Attach uploaded media files to the corresponding JourneyReward objects.

        Media mapping:
        - Chapter 1: Photo Puzzle (photo_reveal)
        - Chapter 3: Photo Slideshow (photo_slideshow)
        - Chapter 4: Video Message (voice_message)
        - Chapter 5: Future Letter Music (future_letter)

        Args:
            journey: JourneyConfiguration to attach media to
            max_retries: Maximum retry attempts for transient storage failures

        Returns:
            List[MediaAttachmentResult]: Results for each chapter media attachment

        Note: Uses atomic transaction to ensure consistency. Critical failures
        will raise exceptions to trigger rollback of entire claim process.
        """
        from .journey import JourneyReward
        from django.core.files.base import ContentFile
        from django.core.files.storage import default_storage

        results: List[MediaAttachmentResult] = []

        # Helper function for retrying storage operations
        def save_with_retry(file_field, filename, file_obj, max_attempts=max_retries):
            """Save file with exponential backoff retry for transient failures."""
            for attempt in range(1, max_attempts + 1):
                try:
                    file_obj.seek(0)
                    file_field.save(filename, file_obj, save=True)
                    return True
                except Exception as e:
                    if attempt < max_attempts:
                        wait_time = 2 ** attempt  # Exponential backoff: 2, 4, 8 seconds
                        logger.warning(
                            f"Gift {self.gift_code}: Storage save failed (attempt {attempt}/{max_attempts}). "
                            f"Retrying in {wait_time}s. Error: {e}"
                        )
                        time.sleep(wait_time)
                    else:
                        raise
            return False

        # Use atomic transaction to ensure consistency
        with transaction.atomic():
            # Chapter 1: Photo Puzzle
            if self.chapter1_image:
                chapter = "chapter1"
                try:
                    reward = JourneyReward.objects.filter(
                        chapter__journey=journey,
                        chapter__chapter_number=1,
                        reward_type='photo_reveal'
                    ).first()

                    if not reward:
                        error_msg = "Chapter 1 photo_reveal reward not found"
                        logger.warning(f"Gift {self.gift_code}: {error_msg}")
                        results.append(MediaAttachmentResult(
                            chapter=chapter,
                            success=False,
                            error_message=error_msg,
                            is_critical=False
                        ))
                    else:
                        # Use retry logic for storage operations
                        save_with_retry(
                            reward.photo,
                            f"puzzle_{self.gift_code}.jpg",
                            self.chapter1_image
                        )
                        results.append(MediaAttachmentResult(
                            chapter=chapter,
                            success=True,
                            files_attached=1,
                            files_total=1
                        ))
                        logger.info(f"Attached Chapter 1 image to reward {reward.id}")
                except Exception as e:
                    error_msg = f"Failed to attach Chapter 1 image: {str(e)}"
                    logger.error(f"Gift {self.gift_code}: {error_msg}", exc_info=True)
                    results.append(MediaAttachmentResult(
                        chapter=chapter,
                        success=False,
                        error_message=error_msg,
                        is_critical=True  # Core puzzle image is critical
                    ))
                    raise  # Re-raise to trigger rollback

            # Chapter 3: Photo Slideshow
            if self.chapter3_images:
                chapter = "chapter3"
                images = self.chapter3_images
                total_images = len(images)
                images_attached = 0

                try:
                    reward = JourneyReward.objects.filter(
                        chapter__journey=journey,
                        chapter__chapter_number=3,
                        reward_type='photo_slideshow'
                    ).first()

                    if not reward:
                        error_msg = "Chapter 3 photo_slideshow reward not found"
                        logger.warning(f"Gift {self.gift_code}: {error_msg}")
                        results.append(MediaAttachmentResult(
                            chapter=chapter,
                            success=False,
                            error_message=error_msg,
                            files_total=total_images,
                            is_critical=False
                        ))
                    else:
                        slideshow_photos = []
                        image_errors = []

                        for idx, image in enumerate(images):
                            try:
                                # Always seek to start before reading
                                image.seek(0)
                                image_content = image.read()
                                image.seek(0)  # Reset for potential re-read

                                if idx == 0:
                                    # First image: save to legacy photo field with retry
                                    content_file = ContentFile(image_content)
                                    save_with_retry(
                                        reward.photo,
                                        f"slideshow_{self.gift_code}_1.jpg",
                                        content_file
                                    )
                                    images_attached += 1
                                    logger.info(f"Attached slideshow image 1 to reward {reward.id}")
                                else:
                                    # Additional images: save to storage with retry
                                    from .journey import journey_reward_photo_path

                                    storage = get_crush_photo_storage()
                                    filename = f"slideshow_{self.gift_code}_{idx + 1}.jpg"
                                    file_path = journey_reward_photo_path(reward, filename)

                                    # Retry logic for storage.save
                                    saved_path = None
                                    for attempt in range(1, max_retries + 1):
                                        try:
                                            saved_path = storage.save(file_path, ContentFile(image_content))
                                            break
                                        except Exception as storage_err:
                                            if attempt < max_retries:
                                                wait_time = 2 ** attempt
                                                logger.warning(
                                                    f"Gift {self.gift_code}: Storage save failed for image {idx + 1} "
                                                    f"(attempt {attempt}/{max_retries}). Retrying in {wait_time}s."
                                                )
                                                time.sleep(wait_time)
                                            else:
                                                raise storage_err

                                    slideshow_photos.append({
                                        'path': saved_path,
                                        'order': idx
                                    })
                                    images_attached += 1
                                    logger.info(f"Attached slideshow image {idx + 1} to storage")

                            except Exception as img_err:
                                error_msg = f"Image {idx + 1}: {str(img_err)}"
                                image_errors.append(error_msg)
                                logger.error(f"Gift {self.gift_code}: Failed to attach slideshow image {idx + 1}: {img_err}")
                                # Continue processing other images (non-critical failures)

                        reward.slideshow_photos = slideshow_photos
                        reward.save()

                        # Report results
                        if images_attached == total_images:
                            results.append(MediaAttachmentResult(
                                chapter=chapter,
                                success=True,
                                files_attached=images_attached,
                                files_total=total_images
                            ))
                        elif images_attached > 0:
                            # Partial success
                            results.append(MediaAttachmentResult(
                                chapter=chapter,
                                success=True,  # At least some images attached
                                files_attached=images_attached,
                                files_total=total_images,
                                error_message=f"Partial success: {'; '.join(image_errors)}"
                            ))
                        else:
                            # Total failure
                            results.append(MediaAttachmentResult(
                                chapter=chapter,
                                success=False,
                                error_message=f"All images failed: {'; '.join(image_errors)}",
                                files_total=total_images,
                                is_critical=False  # Slideshow not critical to journey
                            ))

                        logger.info(f"Gift {self.gift_code}: Attached {images_attached}/{total_images} slideshow images")

                except Exception as e:
                    error_msg = f"Failed to attach Chapter 3 images: {str(e)}"
                    logger.error(f"Gift {self.gift_code}: {error_msg}", exc_info=True)
                    results.append(MediaAttachmentResult(
                        chapter=chapter,
                        success=False,
                        error_message=error_msg,
                        files_attached=images_attached,
                        files_total=total_images,
                        is_critical=False
                    ))

            # Chapter 4: Video Message
            if self.chapter4_video:
                chapter = "chapter4"
                try:
                    reward = JourneyReward.objects.filter(
                        chapter__journey=journey,
                        chapter__chapter_number=4,
                        reward_type='voice_message'
                    ).first()

                    if not reward:
                        error_msg = "Chapter 4 voice_message reward not found"
                        logger.warning(f"Gift {self.gift_code}: {error_msg}")
                        results.append(MediaAttachmentResult(
                            chapter=chapter,
                            success=False,
                            error_message=error_msg,
                            is_critical=False
                        ))
                    else:
                        # Use retry logic for storage operations
                        save_with_retry(
                            reward.video_file,
                            f"message_{self.gift_code}.mp4",
                            self.chapter4_video
                        )
                        results.append(MediaAttachmentResult(
                            chapter=chapter,
                            success=True,
                            files_attached=1,
                            files_total=1
                        ))
                        logger.info(f"Attached Chapter 4 video to reward {reward.id}")
                except Exception as e:
                    error_msg = f"Failed to attach Chapter 4 video: {str(e)}"
                    logger.error(f"Gift {self.gift_code}: {error_msg}", exc_info=True)
                    results.append(MediaAttachmentResult(
                        chapter=chapter,
                        success=False,
                        error_message=error_msg,
                        is_critical=False  # Video message is nice-to-have
                    ))

            # Chapter 5: Future Letter Music
            # Check both new field (chapter5_letter_music) and legacy field (chapter4_audio)
            letter_music = self.chapter5_letter_music or self.chapter4_audio
            if letter_music:
                chapter = "chapter5"
                try:
                    reward = JourneyReward.objects.filter(
                        chapter__journey=journey,
                        chapter__chapter_number=5,
                        reward_type='future_letter'
                    ).first()

                    if not reward:
                        error_msg = "Chapter 5 future_letter reward not found"
                        logger.warning(f"Gift {self.gift_code}: {error_msg}")
                        results.append(MediaAttachmentResult(
                            chapter=chapter,
                            success=False,
                            error_message=error_msg,
                            is_critical=False
                        ))
                    else:
                        # Use retry logic for storage operations
                        save_with_retry(
                            reward.audio_file,
                            f"letter_music_{self.gift_code}.mp3",
                            letter_music
                        )
                        results.append(MediaAttachmentResult(
                            chapter=chapter,
                            success=True,
                            files_attached=1,
                            files_total=1
                        ))
                        logger.info(f"Attached Chapter 5 letter music to reward {reward.id}")
                except Exception as e:
                    error_msg = f"Failed to attach Chapter 5 letter music: {str(e)}"
                    logger.error(f"Gift {self.gift_code}: {error_msg}", exc_info=True)
                    results.append(MediaAttachmentResult(
                        chapter=chapter,
                        success=False,
                        error_message=error_msg,
                        is_critical=False  # Letter music is nice-to-have
                    ))

        # Log summary
        successful = [r for r in results if r.success]
        failed = [r for r in results if not r.success]
        critical_failures = [r for r in failed if r.is_critical]

        if critical_failures:
            error_details = [f"{r.chapter}: {r.error_message}" for r in critical_failures]
            logger.error(
                f"Gift {self.gift_code}: Critical media attachment failures. "
                f"Errors: {error_details}"
            )
        elif failed:
            error_details = [f"{r.chapter}: {r.error_message}" for r in failed]
            logger.warning(
                f"Gift {self.gift_code}: Media attachment completed with non-critical errors. "
                f"Successful: {len(successful)}, Failed: {len(failed)}. Errors: {error_details}"
            )
        else:
            logger.info(
                f"Gift {self.gift_code}: Successfully attached all {len(successful)} media items to rewards."
            )

        return results
