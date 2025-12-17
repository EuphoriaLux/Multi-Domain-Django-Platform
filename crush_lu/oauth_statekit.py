"""
Custom OAuth State Kit for Crush.lu

This module monkey-patches allauth's statekit to use database storage
instead of session storage. This solves the Android PWA OAuth issue where
the OAuth flow opens in system browser (different session).

Usage:
    Import this module early in your Django startup (e.g., in settings or apps.py)
    to apply the monkey-patch.

How it works:
1. When OAuth starts, we store state in both session (for compatibility) AND database
2. When OAuth callback arrives, we try session first (fast path)
3. If session lookup fails, we fall back to database lookup
4. This allows OAuth to work even when sessions don't persist across browser contexts
"""
import json
import logging
import traceback
from typing import Any, Dict, Optional

from django.utils import timezone

logger = logging.getLogger(__name__)

# Flag to track if patching has been applied
_patched = False
_patch_verified = False


def is_patched() -> bool:
    """Check if the OAuth statekit has been patched."""
    return _patched


def ensure_patched():
    """Ensure the OAuth statekit is patched. Safe to call multiple times."""
    if not _patched:
        logger.warning("[OAUTH-DB] Patch not applied yet, applying now...")
        patch_allauth_statekit()
    return _patched


def get_client_ip(request) -> Optional[str]:
    """Extract client IP address from request (without port)."""
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0].strip()
    else:
        ip = request.META.get('REMOTE_ADDR')

    # Remove port if present (e.g., "185.40.60.86:13580" -> "185.40.60.86")
    if ip and ':' in ip and not ip.startswith('['):
        # IPv4 with port - split on last colon
        ip = ip.rsplit(':', 1)[0]

    return ip


def patch_allauth_statekit():
    """
    Monkey-patch allauth's statekit to use database-backed state storage.

    This allows OAuth state to persist across browser context switches,
    which is essential for Android PWA OAuth flows.
    """
    global _patched
    if _patched:
        return

    try:
        from allauth.socialaccount.internal import statekit
        from allauth.socialaccount.adapter import get_adapter
    except ImportError:
        logger.warning("Could not import allauth statekit - OAuth patches not applied")
        return

    # Store original functions
    _original_stash_state = statekit.stash_state
    _original_unstash_state = statekit.unstash_state
    _original_unstash_last_state = statekit.unstash_last_state

    def db_stash_state(request, state: Dict[str, Any], state_id: Optional[str] = None) -> str:
        """
        Enhanced stash_state that stores in both session AND database.

        Args:
            request: Django request object
            state: State dictionary to store
            state_id: Optional state ID (generated if not provided)

        Returns:
            state_id: The state identifier to pass to OAuth provider
        """
        # Log that our patched function is being called (WARNING level to ensure visibility)
        logger.warning(f"[OAUTH-DB] >>> db_stash_state CALLED (patched version) <<<")

        # Check for popup mode parameter and store in session
        # This happens BEFORE the redirect to the OAuth provider
        if request.GET.get('popup') == '1':
            request.session['oauth_popup_mode'] = True
            logger.warning("[OAUTH-DB] Popup mode detected and stored in session")

        # First, use original session-based storage (for compatibility)
        logger.warning("[OAUTH-DB] About to call _original_stash_state...")
        try:
            state_id = _original_stash_state(request, state, state_id)
            logger.warning(f"[OAUTH-DB] State ID from session storage: {state_id[:8]}...")
        except Exception as e:
            logger.error(f"[OAUTH-DB] _original_stash_state FAILED: {e}")
            logger.error(f"[OAUTH-DB] Traceback: {traceback.format_exc()}")
            raise

        # Also store in database for cross-browser persistence
        try:
            from crush_lu.models import OAuthState

            # Get request metadata for security tracking
            user_agent = request.META.get('HTTP_USER_AGENT', '')
            ip_address = get_client_ip(request)

            # Determine provider from state or request
            provider = state.get('provider', '')
            if not provider:
                # Try to get from request path (e.g., /accounts/facebook/login/)
                path = request.path
                if '/accounts/' in path:
                    parts = path.strip('/').split('/')
                    if len(parts) >= 2:
                        provider = parts[1]  # e.g., 'facebook'

            logger.warning(f"[OAUTH-DB] Storing state {state_id[:8]}... in database (provider: {provider}, ip: {ip_address})")

            # Create database record
            deleted_count, _ = OAuthState.objects.filter(state_id=state_id).delete()
            if deleted_count:
                logger.warning(f"[OAUTH-DB] Deleted {deleted_count} existing state(s) with same ID")

            oauth_state = OAuthState.objects.create(
                state_id=state_id,
                state_data=json.dumps(state),
                expires_at=timezone.now() + timezone.timedelta(minutes=15),
                provider=provider,
                user_agent=user_agent[:500] if user_agent else '',
                ip_address=ip_address,
            )
            logger.warning(f"[OAUTH-DB] SUCCESS: State {state_id[:8]}... stored in database (pk={oauth_state.pk})")

        except Exception as e:
            # Log the full exception with traceback
            logger.error(f"[OAUTH-DB] FAILED to store state in database: {e}")
            logger.error(f"[OAUTH-DB] Traceback: {traceback.format_exc()}")

        return state_id

    def db_unstash_state(request, state_id: str) -> Optional[Dict[str, Any]]:
        """
        Enhanced unstash_state that tries session first, then database.

        Args:
            request: Django request object
            state_id: The state identifier from OAuth callback

        Returns:
            State dictionary, or None if not found/expired/used
        """
        logger.info(f"[OAUTH-DB] db_unstash_state called for state {state_id[:8]}... (patched version)")

        # First, try original session-based lookup (fast path)
        state = _original_unstash_state(request, state_id)
        if state is not None:
            logger.info(f"[OAUTH-DB] State {state_id[:8]}... found in session")
            # Also clean up database record
            try:
                from crush_lu.models import OAuthState
                OAuthState.objects.filter(state_id=state_id).update(used=True)
            except Exception:
                pass
            return state

        # Session lookup failed - try database (cross-browser case)
        logger.info(f"[OAUTH-DB] State {state_id[:8]}... NOT in session, trying database...")
        try:
            from crush_lu.models import OAuthState

            # First, check if the state exists at all
            existing = OAuthState.objects.filter(state_id=state_id).first()
            if existing:
                logger.info(f"[OAUTH-DB] State {state_id[:8]}... EXISTS in DB (used={existing.used}, expired={timezone.now() > existing.expires_at})")
            else:
                logger.warning(f"[OAUTH-DB] State {state_id[:8]}... does NOT exist in database!")
                # Log recent states for debugging
                recent_states = OAuthState.objects.order_by('-created_at')[:5]
                if recent_states:
                    recent_ids = [s.state_id[:8] for s in recent_states]
                    logger.info(f"[OAUTH-DB] Recent state IDs in DB: {recent_ids}")

            state = OAuthState.get_and_consume_state(state_id)
            if state:
                logger.info(f"[OAUTH-DB] SUCCESS: State {state_id[:8]}... retrieved from database")
                return state
            else:
                logger.warning(f"[OAUTH-DB] get_and_consume_state returned None for {state_id[:8]}...")
        except Exception as e:
            logger.error(f"[OAUTH-DB] Exception during database lookup: {e}")
            logger.error(f"[OAUTH-DB] Traceback: {traceback.format_exc()}")

        logger.warning(f"[OAUTH-DB] FAILED: State {state_id[:8]}... not found in session or database")
        return None

    def db_unstash_last_state(request) -> Optional[Dict[str, Any]]:
        """
        Enhanced unstash_last_state that tries session first, then database.

        Used for providers that don't support state parameter.
        """
        # First try session
        state = _original_unstash_last_state(request)
        if state is not None:
            return state

        # Try to find the most recent unused state in database for this client
        try:
            from crush_lu.models import OAuthState

            ip_address = get_client_ip(request)
            recent_state = OAuthState.objects.filter(
                used=False,
                expires_at__gt=timezone.now(),
                ip_address=ip_address,
            ).order_by('-created_at').first()

            if recent_state:
                state = OAuthState.get_and_consume_state(recent_state.state_id)
                if state:
                    logger.info(f"OAuth last state retrieved from database")
                    return state
        except Exception as e:
            logger.error(f"Failed to retrieve last OAuth state from database: {e}")

        return None

    # Apply patches
    statekit.stash_state = db_stash_state
    statekit.unstash_state = db_unstash_state
    statekit.unstash_last_state = db_unstash_last_state

    _patched = True
    logger.warning("[OAUTH-DB] *** Allauth statekit PATCHED for database-backed OAuth state storage ***")

    # Verify the patch was applied correctly
    if statekit.stash_state == db_stash_state:
        logger.warning("[OAUTH-DB] Verification: stash_state patch confirmed")
    else:
        logger.error("[OAUTH-DB] Verification FAILED: stash_state patch not applied!")

    if statekit.unstash_state == db_unstash_state:
        logger.warning("[OAUTH-DB] Verification: unstash_state patch confirmed")
    else:
        logger.error("[OAUTH-DB] Verification FAILED: unstash_state patch not applied!")

    # Log the actual function IDs for debugging
    logger.warning(f"[OAUTH-DB] statekit.stash_state id: {id(statekit.stash_state)}")
    logger.warning(f"[OAUTH-DB] db_stash_state id: {id(db_stash_state)}")


def cleanup_expired_states():
    """
    Cleanup expired OAuth states.

    Call this periodically (e.g., in a management command or celery task).
    """
    try:
        from crush_lu.models import OAuthState
        expired_count = OAuthState.cleanup_expired()
        used_count = OAuthState.cleanup_old_used(hours=24)
        if expired_count or used_count:
            logger.info(f"Cleaned up {expired_count} expired and {used_count} old used OAuth states")
    except Exception as e:
        logger.error(f"Failed to cleanup OAuth states: {e}")
