from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils import timezone
from django.utils.crypto import get_random_string

from .profiles import CrushProfile


def _generate_referral_code(length=8):
    return get_random_string(length=length, allowed_chars="ABCDEFGHJKLMNPQRSTUVWXYZ23456789")


class ReferralCode(models.Model):
    """Referral codes tied to a referrer profile."""

    code = models.CharField(max_length=32, unique=True, db_index=True)
    referrer = models.ForeignKey(
        CrushProfile,
        on_delete=models.CASCADE,
        related_name='referral_codes'
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Referral Code"
        verbose_name_plural = "🧲 Referral Codes"

    def __str__(self):
        return f"{self.code} ({self.referrer.user.email})"

    def save(self, *args, **kwargs):
        if not self.code:
            self.code = self._generate_unique_code()
        super().save(*args, **kwargs)

    @classmethod
    def _generate_unique_code(cls, length=8):
        code = _generate_referral_code(length=length)
        while cls.objects.filter(code=code).exists():
            code = _generate_referral_code(length=length)
        return code

    @classmethod
    def get_or_create_for_profile(cls, profile):
        code = cls.objects.filter(referrer=profile, is_active=True).order_by('-created_at').first()
        if code:
            return code
        return cls.objects.create(referrer=profile)

    def get_absolute_url(self):
        return reverse('crush_lu:referral_redirect', kwargs={'code': self.code})

    def get_referral_url(self, base_url="https://crush.lu"):
        return f"{base_url.rstrip('/')}{self.get_absolute_url()}"


class ReferralAttribution(models.Model):
    """Track referral attributions from link click to signup."""

    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        CONVERTED = 'converted', 'Converted'

    referral_code = models.ForeignKey(
        ReferralCode,
        on_delete=models.CASCADE,
        related_name='attributions'
    )
    referrer = models.ForeignKey(
        CrushProfile,
        on_delete=models.CASCADE,
        related_name='referral_attributions'
    )
    referred_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='referral_attributions'
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING
    )
    session_key = models.CharField(max_length=40, blank=True)
    ip_address = models.CharField(max_length=64, blank=True)
    user_agent = models.TextField(blank=True)
    landing_path = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    converted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Referral Attribution"
        verbose_name_plural = "🧲 Referral Attributions"
        indexes = [
            models.Index(fields=['status', 'created_at']),
            models.Index(fields=['session_key']),
        ]

    def __str__(self):
        return f"{self.referral_code.code} -> {self.referred_user or 'pending'}"

    def mark_converted(self, user):
        self.referred_user = user
        self.status = self.Status.CONVERTED
        self.converted_at = timezone.now()
        self.save(update_fields=['referred_user', 'status', 'converted_at'])
