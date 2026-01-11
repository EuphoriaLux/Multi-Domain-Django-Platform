"""
Journey Gift model for the Crush.lu shareable journey experience.

Allows users to gift personalized "Wonderland of You" journeys to non-users
via QR codes.
"""
import secrets
import string

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


def generate_gift_code():
    """Generate a unique gift code in format WOY-XXXXXXXX"""
    # 8 random alphanumeric characters (uppercase + digits)
    chars = string.ascii_uppercase + string.digits
    random_part = ''.join(secrets.choice(chars) for _ in range(8))
    return f"WOY-{random_part}"


def gift_qr_code_path(instance, filename):
    """Generate path for QR code images"""
    return f"journey_gifts/qr/{instance.gift_code}.png"


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
            site = Site.objects.get(domain__contains='crush.lu')
            domain = site.domain
        except Site.DoesNotExist:
            domain = 'crush.lu'

        return f"https://{domain}/journey/gift/{self.gift_code}/"

    def mark_expired(self):
        """Mark the gift as expired"""
        self.status = self.Status.EXPIRED
        self.save(update_fields=['status'])

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

        # 4. Update gift record
        self.journey = journey
        self.special_experience = special_exp
        self.status = self.Status.CLAIMED
        self.claimed_by = user
        self.claimed_at = timezone.now()
        self.save()

        return journey
