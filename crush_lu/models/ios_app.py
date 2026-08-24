import hashlib
import secrets
from collections import namedtuple
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

# Why a redemption was refused. These are logged verbatim so the three causes
# are separable in App Insights: before they existed, "code never issued",
# "code already used" and "code timed out" all arrived as the same opaque 400
# and nothing in 30 days of production telemetry could tell them apart.
AUTH_CODE_OK = "ok"
AUTH_CODE_UNKNOWN = "unknown"
AUTH_CODE_REPLAYED = "replayed"
AUTH_CODE_EXPIRED = "expired"

#: ``user`` is set only when this call is the one that claimed the code.
#: ``record`` is the row when one exists, so callers can compare its owner
#: against the requesting session (see crush_lu/native_auth.py).
RedeemedCode = namedtuple("RedeemedCode", "user reason record")


def _hash_native_auth_code(code, secret_key=None):
    secret = (secret_key if secret_key is not None else settings.SECRET_KEY).encode(
        "utf-8"
    )
    return hashlib.sha256(secret + code.encode("utf-8")).hexdigest()


def _candidate_auth_code_hashes(code):
    """Hashes of `code` under the current SECRET_KEY and every fallback.

    This hand-rolled hash (not django.core.signing) gets no automatic
    SECRET_KEY_FALLBACKS support — issue() always hashes under the *current*
    key at write time, so a code issued just before a SECRET_KEY rotation
    would otherwise be silently unmatchable at read time in consume(), even
    though it's still well within its short TTL (IOS_AUTH_CODE_TTL_SECONDS,
    default 300s). Walk the candidates by hand instead, same idea as
    django.core.signing.Signer's fallback_keys walk.
    """
    return [
        _hash_native_auth_code(code, key)
        for key in (settings.SECRET_KEY, *settings.SECRET_KEY_FALLBACKS)
    ]


class IOSAppDevice(models.Model):
    """
    Native iOS APNS registration for the App Store app.

    This is intentionally separate from Web Push subscriptions. The PWA uses
    browser VAPID subscriptions; the iOS app uses APNS device tokens.
    """

    ENVIRONMENT_CHOICES = [
        ("sandbox", _("Sandbox")),
        ("production", _("Production")),
    ]

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="ios_app_devices",
        help_text=_("User who owns this iOS app installation"),
    )
    device_token = models.CharField(
        max_length=255,
        unique=True,
        help_text=_("APNS device token for the native iOS app"),
    )
    device_id = models.CharField(
        max_length=128,
        blank=True,
        db_index=True,
        help_text=_("Stable app-generated device identifier"),
    )
    environment = models.CharField(
        max_length=20,
        choices=ENVIRONMENT_CHOICES,
        default="production",
        db_index=True,
    )
    bundle_id = models.CharField(max_length=128, default="lu.crush.app")
    app_version = models.CharField(max_length=32, blank=True)
    app_build = models.CharField(max_length=32, blank=True)
    device_name = models.CharField(max_length=100, blank=True)
    system_version = models.CharField(max_length=50, blank=True)
    user_agent = models.TextField(blank=True)

    enabled = models.BooleanField(default=True)
    notify_new_messages = models.BooleanField(
        default=True, help_text=_("Notify about new connection messages")
    )
    notify_event_reminders = models.BooleanField(
        default=True, help_text=_("Notify about upcoming events")
    )
    notify_new_connections = models.BooleanField(
        default=True, help_text=_("Notify about new connection requests")
    )
    notify_profile_updates = models.BooleanField(
        default=True, help_text=_("Notify about profile approval status")
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_seen_at = models.DateTimeField(auto_now=True)
    last_push_at = models.DateTimeField(null=True, blank=True)
    failure_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-last_seen_at"]
        verbose_name = _("iOS App Device")
        verbose_name_plural = _("iOS App Devices")
        indexes = [
            models.Index(fields=["user", "enabled"]),
            models.Index(fields=["environment", "enabled"]),
        ]

    def __str__(self):
        label = self.device_name or self.device_id or self.device_token[:12]
        return f"{self.user.username} - {label}"

    def mark_success(self):
        self.last_push_at = timezone.now()
        self.failure_count = 0
        self.save(update_fields=["last_push_at", "failure_count"])

    def mark_failure(self):
        self.failure_count += 1
        update_fields = ["failure_count"]
        if self.failure_count >= 5:
            self.enabled = False
            update_fields.append("enabled")
        self.save(update_fields=update_fields)


class IOSNativeAuthCode(models.Model):
    """
    One-time bridge from ASWebAuthenticationSession to the WKWebView session.

    Flow:
    1. Native app opens /api/mobile/ios/auth/handoff/ in ASWebAuthenticationSession.
    2. Django/allauth authenticates the user and redirects back with a short code.
    3. The native app loads /api/mobile/ios/auth/complete/<code>/ in WKWebView.
    4. Django consumes the code and sets the normal session cookie in WKWebView.
    """

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="ios_native_auth_codes",
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
        verbose_name = _("iOS Native Auth Code")
        verbose_name_plural = _("iOS Native Auth Codes")

    def __str__(self):
        status = "consumed" if self.consumed_at else "pending"
        return f"{self.user.username} - {status}"

    @property
    def is_expired(self):
        return timezone.now() >= self.expires_at

    @classmethod
    def issue(cls, user, redirect_uri, request=None):
        code = secrets.token_urlsafe(32)
        ttl_seconds = getattr(settings, "IOS_AUTH_CODE_TTL_SECONDS", 300)
        cls.objects.create(
            user=user,
            code_hash=_hash_native_auth_code(code),
            redirect_uri=redirect_uri,
            expires_at=timezone.now() + timedelta(seconds=ttl_seconds),
            user_agent=(request.META.get("HTTP_USER_AGENT", "") if request else "")[:500],
            ip_address=(request.META.get("REMOTE_ADDR") if request else None),
        )
        return code

    @classmethod
    def redeem(cls, code):
        """Atomically claim `code`, or say why it could not be claimed.

        The claim is a single conditional UPDATE, so the database serialises
        it and exactly one caller can ever see ``claimed``. The obvious
        alternative — filter(consumed_at__isnull=True).first() then save() —
        is a read-then-write: the guard lives only in the SELECT, the UPDATE
        it emits is `SET consumed_at=%s WHERE id=%s` with no `AND consumed_at
        IS NULL`, and under autocommit + READ COMMITTED (ATOMIC_REQUESTS is
        off, production runs four gunicorn workers) two requests milliseconds
        apart both pass the guard and both log in. One "one-time" code, two
        sessions. ATOMIC_REQUESTS would not close that: the blocked
        transaction re-checks `WHERE id = N`, still matches, and commits.

        `.order_by()` clears Meta.ordering, which would otherwise put a
        pointless ORDER BY on both queries.
        """
        now = timezone.now()
        candidate_hashes = _candidate_auth_code_hashes(code)
        claimed = (
            cls.objects.order_by()
            .filter(
                code_hash__in=candidate_hashes,
                consumed_at__isnull=True,
                expires_at__gt=now,
            )
            .update(consumed_at=now)
        )
        record = (
            cls.objects.select_related("user")
            .order_by()
            .filter(code_hash__in=candidate_hashes)
            .first()
        )
        if claimed:
            return RedeemedCode(record.user, AUTH_CODE_OK, record)
        if record is None:
            return RedeemedCode(None, AUTH_CODE_UNKNOWN, None)
        if record.consumed_at is not None:
            return RedeemedCode(None, AUTH_CODE_REPLAYED, record)
        return RedeemedCode(None, AUTH_CODE_EXPIRED, record)

    @classmethod
    def consume(cls, code):
        """The user this code belongs to, or None. Prefer `redeem()`.

        Kept because callers that only need "did it work" read better this
        way; anything that has to tell a replay from an expiry — which is
        every caller that reports to a human — needs `redeem()`.
        """
        return cls.redeem(code).user
