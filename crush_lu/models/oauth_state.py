"""
OAuth State Model for Cross-Browser/PWA Authentication.

This module provides database-backed OAuth state storage to solve the
Android PWA issue where OAuth opens in system browser (different session).

The problem:
- User clicks Facebook login in PWA
- OAuth redirects to system browser (new session)
- Facebook callback arrives in system browser
- State stored in PWA session is not accessible
- Allauth fails with "authentication error"

The solution:
- Store OAuth state in database with unique state_id
- State can be retrieved from any session using the state_id
- Allauth's state_id is passed through OAuth flow and returned by Facebook
- We look up state from database instead of session
"""
import secrets
import json
from datetime import timedelta

from django.db import models
from django.utils import timezone


class OAuthState(models.Model):
    """
    Database-backed OAuth state storage for cross-browser authentication.

    This model stores OAuth state data that needs to persist across
    browser context switches (e.g., PWA to system browser on Android).

    Attributes:
        state_id: Unique identifier passed through OAuth flow
        state_data: JSON serialized state dictionary from allauth
        created_at: When the state was created
        expires_at: When the state expires (default: 10 minutes)
        used: Whether the state has been consumed
        provider: OAuth provider (facebook, google, etc.)
        user_agent: User agent of the originating request (for debugging)
        ip_address: IP address of the originating request (for security)
    """
    state_id = models.CharField(
        max_length=64,
        unique=True,
        primary_key=True,
        help_text="Unique state identifier passed through OAuth flow"
    )
    state_data = models.TextField(
        help_text="JSON serialized state dictionary"
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        help_text="When the state was created"
    )
    expires_at = models.DateTimeField(
        help_text="When the state expires"
    )
    used = models.BooleanField(
        default=False,
        help_text="Whether the state has been consumed"
    )
    provider = models.CharField(
        max_length=50,
        blank=True,
        default='',
        help_text="OAuth provider name"
    )
    user_agent = models.TextField(
        blank=True,
        default='',
        help_text="User agent of originating request"
    )
    ip_address = models.GenericIPAddressField(
        null=True,
        blank=True,
        help_text="IP address of originating request"
    )

    class Meta:
        app_label = 'crush_lu'
        verbose_name = 'OAuth State'
        verbose_name_plural = 'OAuth States'
        indexes = [
            models.Index(fields=['expires_at']),
            models.Index(fields=['used']),
        ]

    def __str__(self):
        return f"OAuthState {self.state_id[:8]}... ({self.provider})"

    @classmethod
    def generate_state_id(cls) -> str:
        """Generate a cryptographically secure state ID."""
        return secrets.token_urlsafe(32)

    @classmethod
    def create_state(
        cls,
        state_data: dict,
        provider: str = '',
        user_agent: str = '',
        ip_address: str = None,
        expiry_minutes: int = 10
    ) -> 'OAuthState':
        """
        Create a new OAuth state record.

        Args:
            state_data: Dictionary of state data from allauth
            provider: OAuth provider name
            user_agent: User agent string
            ip_address: Client IP address
            expiry_minutes: Minutes until state expires

        Returns:
            OAuthState instance
        """
        state_id = cls.generate_state_id()
        expires_at = timezone.now() + timedelta(minutes=expiry_minutes)

        return cls.objects.create(
            state_id=state_id,
            state_data=json.dumps(state_data),
            expires_at=expires_at,
            provider=provider,
            user_agent=user_agent[:500] if user_agent else '',  # Truncate long UAs
            ip_address=ip_address,
        )

    @classmethod
    def get_and_consume_state(cls, state_id: str) -> dict:
        """
        Retrieve and consume an OAuth state.

        This is an atomic operation - the state is marked as used
        immediately to prevent replay attacks.

        Args:
            state_id: The state identifier

        Returns:
            State data dictionary, or None if invalid/expired/used

        Raises:
            OAuthState.DoesNotExist: If state_id is not found
        """
        from django.db import transaction

        try:
            with transaction.atomic():
                state = cls.objects.select_for_update().get(state_id=state_id)

                # Check if expired
                if timezone.now() > state.expires_at:
                    state.delete()  # Clean up expired state
                    return None

                # Check if already used
                if state.used:
                    return None

                # Mark as used (consume)
                state.used = True
                state.save(update_fields=['used'])

                return json.loads(state.state_data)

        except cls.DoesNotExist:
            return None

    @classmethod
    def cleanup_expired(cls) -> int:
        """
        Remove expired OAuth states.

        Should be called periodically (e.g., via management command or celery).

        Returns:
            Number of deleted states
        """
        deleted_count, _ = cls.objects.filter(
            expires_at__lt=timezone.now()
        ).delete()
        return deleted_count

    @classmethod
    def cleanup_old_used(cls, hours: int = 24) -> int:
        """
        Remove old used OAuth states.

        Keeps used states for a while for debugging purposes.

        Args:
            hours: Delete used states older than this many hours

        Returns:
            Number of deleted states
        """
        cutoff = timezone.now() - timedelta(hours=hours)
        deleted_count, _ = cls.objects.filter(
            used=True,
            created_at__lt=cutoff
        ).delete()
        return deleted_count
