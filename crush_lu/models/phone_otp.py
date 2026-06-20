import secrets

from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class PhoneOTP(models.Model):
    """A one-time passcode we generated and delivered ourselves.

    Used for channels where the provider is delivery-only (WhatsApp): unlike
    Firebase SMS — where Google generates and verifies the code and hands us a
    signed token — here we own generation, storage, and verification. The raw
    code is never stored; only a salted hash (``make_password``).

    Rows are short-lived (``expires_at``) and single-purpose; a successful
    verification flips ``CrushProfile.phone_verified`` in the view, not here.
    """

    CODE_LENGTH = 6
    MAX_ATTEMPTS = 5

    class Channel(models.TextChoices):
        WHATSAPP = "whatsapp", _("WhatsApp")
        SMS = "sms", _("SMS")

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="phone_otps",
    )
    phone_number = models.CharField(max_length=20)
    code_hash = models.CharField(max_length=128)
    channel = models.CharField(
        max_length=16,
        choices=Channel.choices,
        default=Channel.WHATSAPP,
    )
    attempts = models.PositiveSmallIntegerField(default=0)
    consumed = models.BooleanField(
        default=False,
        help_text=_("Set once the code is successfully verified (single-use)."),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "channel", "consumed"]),
        ]

    def __str__(self):
        return f"OTP({self.channel}) for user {self.user_id} [{self.phone_number}]"

    @staticmethod
    def generate_code() -> str:
        """Cryptographically random zero-padded numeric code."""
        upper = 10**PhoneOTP.CODE_LENGTH
        return str(secrets.randbelow(upper)).zfill(PhoneOTP.CODE_LENGTH)

    @classmethod
    def issue(cls, *, user, phone_number, code, channel, ttl_minutes):
        """Create a stored OTP (hashed) and invalidate any prior open ones.

        Superseding earlier unconsumed codes for the same user+channel means a
        "resend" can't be defeated by entering a stale code.
        """
        cls.objects.filter(
            user=user, channel=channel, consumed=False
        ).update(consumed=True)
        return cls.objects.create(
            user=user,
            phone_number=phone_number,
            code_hash=make_password(code),
            channel=channel,
            expires_at=timezone.now() + timezone.timedelta(minutes=ttl_minutes),
        )

    @property
    def is_expired(self) -> bool:
        return timezone.now() >= self.expires_at

    def verify(self, raw_code: str) -> bool:
        """Check a submitted code, recording the attempt.

        Returns True only for a live (unconsumed, unexpired, under attempt cap)
        code that matches. On success the row is consumed (single-use).

        Read-modify-writes ``attempts``/``consumed``, so callers must hold a row
        lock (``select_for_update`` inside a transaction) to keep concurrent
        verifies from sharing a pre-increment count and exceeding MAX_ATTEMPTS.
        """
        if self.consumed or self.is_expired or self.attempts >= self.MAX_ATTEMPTS:
            return False

        self.attempts += 1
        if check_password(raw_code, self.code_hash):
            self.consumed = True
            self.save(update_fields=["attempts", "consumed"])
            return True

        self.save(update_fields=["attempts"])
        return False
