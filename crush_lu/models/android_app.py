import hashlib
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


def _hash_android_auth_code(code):
    secret = settings.SECRET_KEY.encode("utf-8")
    return hashlib.sha256(secret + code.encode("utf-8")).hexdigest()


class AndroidNativeAuthCode(models.Model):
    """
    One-time bridge from browser login to the Android WebView session.

    Flow:
    1. Native app opens /api/mobile/android/auth/handoff/ in the browser.
    2. Django/allauth authenticates the user and redirects back with a short code.
    3. The native app loads /api/mobile/android/auth/complete/<code>/ in WebView.
    4. Django consumes the code and sets the normal session cookie in WebView.
    """

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="android_native_auth_codes",
    )
    code_hash = models.CharField(max_length=64, unique=True, db_index=True)
    redirect_uri = models.URLField(max_length=500)
    expires_at = models.DateTimeField(db_index=True)
    consumed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    user_agent = models.TextField(blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("Android Native Auth Code")
        verbose_name_plural = _("Android Native Auth Codes")

    def __str__(self):
        status = "consumed" if self.consumed_at else "pending"
        return f"{self.user.username} - {status}"

    @property
    def is_expired(self):
        return timezone.now() >= self.expires_at

    @classmethod
    def issue(cls, user, redirect_uri, request=None):
        code = secrets.token_urlsafe(32)
        ttl_seconds = getattr(settings, "ANDROID_AUTH_CODE_TTL_SECONDS", 300)
        cls.objects.create(
            user=user,
            code_hash=_hash_android_auth_code(code),
            redirect_uri=redirect_uri,
            expires_at=timezone.now() + timedelta(seconds=ttl_seconds),
            user_agent=(request.META.get("HTTP_USER_AGENT", "") if request else "")[:500],
            ip_address=(request.META.get("REMOTE_ADDR") if request else None),
        )
        return code

    @classmethod
    def consume(cls, code):
        code_hash = _hash_android_auth_code(code)
        auth_code = cls.objects.select_related("user").filter(
            code_hash=code_hash,
            consumed_at__isnull=True,
        ).first()
        if not auth_code or auth_code.is_expired:
            return None
        auth_code.consumed_at = timezone.now()
        auth_code.save(update_fields=["consumed_at"])
        return auth_code.user
