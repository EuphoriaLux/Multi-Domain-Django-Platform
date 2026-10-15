"""
Social Photos Module for Crush.lu

This module provides helper functions for fetching, displaying, and importing
profile photos from connected social accounts (Facebook, Google, Microsoft).

Key features:
- Get photo URLs from social account extra_data
- Fetch Microsoft photos via Graph API (requires ProfilePhoto.Read.All permission)
- Download and save social photos to any profile photo slot
- Unified interface for all OAuth providers
- Caching to avoid slow API calls on every page load
- Persistent photo cache: photos are saved to storage on login so they
  remain available even after OAuth tokens expire
"""

import contextlib
import contextvars
import logging
import re
import time
import requests
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.utils import timezone
from .utils.image_processing import process_uploaded_image

logger = logging.getLogger(__name__)

# Cache timeout for social photo URLs (1 hour)
# Profile pictures rarely change, and longer caching reduces slow Facebook API calls
SOCIAL_PHOTO_CACHE_TIMEOUT = 3600

# Shorter cache timeout for persisted storage URLs (SAS tokens expire in 30 min)
PERSISTED_PHOTO_CACHE_TIMEOUT = 1500  # 25 minutes

# How often a login is allowed to re-download and re-persist a social photo.
# Profile pictures change on the order of months; the login path was paying a
# full image download plus image processing plus a blob-storage write on every
# single sign-in. One refresh per day per account keeps the persisted copy
# usably fresh for the token-expiry fallback at a fraction of the cost.
SOCIAL_PHOTO_REFRESH_INTERVAL = 24 * 3600  # 24 hours

# Provider display names
PROVIDER_DISPLAY_NAMES = {
    'facebook': 'Facebook',
    'google': 'Google',
    'microsoft': 'Microsoft',
}


_ACCESS_TOKEN_RE = re.compile(r'access_token=[^&\s)"\']+')


def _safe_exception_text(exc):
    """Render an exception for logging without leaking credentials.

    The Facebook Graph photo requests carry the user's live access token in
    the query string, and requests' HTTPError / ConnectionError text embeds
    the full URL. Log the exception class and HTTP status (when a response
    exists) instead of ``str(exc)``, and redact any ``access_token=`` value
    that might still appear as a belt-and-braces measure.
    """
    text = type(exc).__name__
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if status is not None:
        text += f" (HTTP {status})"
    return _ACCESS_TOKEN_RE.sub("access_token=REDACTED", text)


def _get_token_for_account(social_account):
    """Get the SocialToken for a social account, using prefetch if available."""
    try:
        if (
            hasattr(social_account, '_prefetched_objects_cache')
            and 'socialtoken_set' in social_account._prefetched_objects_cache
        ):
            tokens = list(social_account.socialtoken_set.all())
            return tokens[0] if tokens else None
        else:
            from allauth.socialaccount.models import SocialToken
            return SocialToken.objects.filter(account=social_account).first()
    except Exception:
        return None


def _is_token_expired(token):
    """Check if a SocialToken is expired."""
    if not token:
        return True
    if token.expires_at and token.expires_at <= timezone.now():
        return True
    return False


# =============================================================================
# PERSISTENT SOCIAL PHOTO CACHE
# =============================================================================
# Social photo URLs (especially Facebook and Microsoft) expire when OAuth
# tokens expire. To keep photos available, we persist a copy to storage
# on each login (when we have a fresh token) and fall back to it when
# the live API is unavailable.


# Providers whose photo refresh_social_photo_cache() persists to storage.
PHOTO_CACHE_PROVIDERS = ('facebook', 'google', 'microsoft')

# Set while a caller deletes SocialAccounts whose cached photos it has already
# erased (see already_erased_social_photo_cache); the pre_delete hook skips.
_cache_already_erased = contextvars.ContextVar(
    'social_photo_cache_already_erased', default=False
)


@contextlib.contextmanager
def already_erased_social_photo_cache():
    """Delete SocialAccounts without the pre_delete hook touching storage.

    Only for a caller that has just erased every cached copy itself, fail
    closed: full-account deletion removes the accounts after it has
    deactivated the member, when a storage error could no longer be retried
    by the member signing in again.
    """
    token = _cache_already_erased.set(True)
    try:
        yield
    finally:
        _cache_already_erased.reset(token)


def social_photo_cache_already_erased():
    return _cache_already_erased.get()


def _get_social_cache_dir(user_id):
    """Get the storage folder holding a member's cached social photos."""
    return f"users/{user_id}/social_cache/"


def _get_social_cache_path(social_account):
    """Get the storage path for a cached social photo."""
    folder = _get_social_cache_dir(social_account.user_id)
    return f"{folder}{social_account.provider}.jpg"


def _get_photo_storage():
    """Get the storage backend for social photo cache."""
    from .models.profiles import get_crush_photo_storage
    return get_crush_photo_storage()


def _delete_cached_photo_files(storage, user_id, provider):
    """
    Delete every stored copy of one provider's persisted photo.

    The production storage (CrushProfilePhotoStorage) gives every save a
    unique ``<uuid>_<provider>.jpg`` name, so the path passed to save() is not
    the name that gets stored: list the folder and match on the provider.
    Azure lists full blob names and the filesystem bare file names, so only
    the last path segment is compared. Storage errors propagate.
    """
    folder = _get_social_cache_dir(user_id)
    try:
        _dirs, files = storage.listdir(folder)
    except FileNotFoundError:
        return
    for name in files:
        name = name.rsplit('/', 1)[-1]
        if name == f"{provider}.jpg" or name.endswith(f"_{provider}.jpg"):
            storage.delete(folder + name)


def _is_banned_from_crush(user_id):
    """True when the member may never hold a Crush.lu profile again.

    Profile deletion sets this ban permanently, so a later login must not
    re-collect a provider photo that has nowhere to go.
    """
    from .models.profiles import UserDataConsent

    return UserDataConsent.objects.filter(
        user_id=user_id, crushlu_banned=True
    ).exists()


def _may_persist_social_photo(social_account):
    """The account is still connected and its member may hold a Crush profile.

    A refresh fetches from the provider for several seconds; a disconnect, a
    profile deletion or an account merge can land in between, so this is
    checked again around the write. The row must still belong to the user and
    provider the refresh started with: a merge keeps the pk but moves the
    account to the keeper, whose folder is not the one being written. An
    unsaved account, as in a connect flow, never qualifies.
    """
    from allauth.socialaccount.models import SocialAccount

    if not social_account.pk:
        return False
    still_owned = SocialAccount.objects.filter(
        pk=social_account.pk,
        user_id=social_account.user_id,
        provider=social_account.provider,
    ).exists()
    if not still_owned:
        return False
    return not _is_banned_from_crush(social_account.user_id)


def _may_hold_social_photo_cache(user_id):
    """Whether a member can have a persisted photo that cleanup must reach.

    Photos are only persisted on crush.lu logins, so a member with no Crush
    profile, consent or ban has none in practice: deleting their PowerUp or
    Entreprinder account must not depend on Crush storage. The rare copy a
    one-off crush.lu login left for such a member is removed by
    sweep_orphaned_social_photo_cache() once the account is gone.
    """
    from django.db.models import Q

    from .models import CrushProfile
    from .models.profiles import UserDataConsent

    if CrushProfile.objects.filter(user_id=user_id).exists():
        return True
    return UserDataConsent.objects.filter(
        Q(crushlu_consent_given=True) | Q(crushlu_banned=True), user_id=user_id
    ).exists()


def _persist_social_photo(social_account, image_data):
    """
    Persist social photo binary data to storage for token-expiry fallback.

    Saves the image to: users/{user_id}/social_cache/{provider}.jpg
    This file persists independently of OAuth tokens.

    The write is bracketed by _may_persist_social_photo(): a disconnect or a
    profile deletion that ran its cleanup while the provider fetch was in
    flight must not find the photo back afterwards. A deletion that commits
    after the second check is covered by the SocialAccount pre_delete hook's
    post-commit pass, and profile deletion commits its ban before erasing.

    Args:
        social_account: SocialAccount instance
        image_data: Raw image bytes

    Returns:
        bool: True if saved successfully
    """
    if not _may_persist_social_photo(social_account):
        return False

    storage = _get_photo_storage()
    path = _get_social_cache_path(social_account)

    try:
        # Process image (fix orientation, strip EXIF, resize)
        filename = f"{social_account.provider}_cache.jpg"
        raw_file = ContentFile(image_data, name=filename)
        processed = process_uploaded_image(raw_file, filename)

        # Replace the previous copy (whatever name the storage gave it)
        try:
            _delete_cached_photo_files(
                storage, social_account.user_id, social_account.provider
            )
        except Exception:
            pass

        saved_name = storage.save(path, processed)

        if not _may_persist_social_photo(social_account):
            storage.delete(saved_name)
            logger.info(
                f"Discarded {social_account.provider} photo for user "
                f"{social_account.user_id}: account disconnected or erased "
                f"during the refresh"
            )
            return False

        # Mark that persisted photo exists (avoids storage.exists() checks)
        cache.set(
            f"social_photo_persisted_exists_{social_account.id}",
            True,
            SOCIAL_PHOTO_CACHE_TIMEOUT * 24,  # 24 hours
        )

        logger.info(
            f"Persisted {social_account.provider} photo to storage "
            f"for user {social_account.user_id}"
        )
        return True
    except Exception as e:
        logger.warning(f"Failed to persist social photo: {e}")
        return False


def delete_social_photo_cache(social_account):
    """
    Delete the persisted social photo and every cache entry pointing at it.

    Runs whenever a SocialAccount is deleted (pre_delete hook in signals.py)
    and when the member deletes their Crush.lu profile: the persisted copy
    only exists to offer the member their own provider photo for their
    profile, so it must not outlive either. Providers that never persist a
    photo (Apple, LuxID, ...) are skipped without touching storage.

    A file that is already missing is fine; any other storage error propagates
    so the caller can fail closed instead of reporting the data as gone.
    """
    if social_account.provider not in PHOTO_CACHE_PROVIDERS:
        return
    _delete_cached_photo_files(
        _get_photo_storage(), social_account.user_id, social_account.provider
    )
    cache.delete_many(
        [
            f"social_photo_persisted_exists_{social_account.id}",
            f"social_photo_{social_account.provider}_{social_account.id}",
            f"social_photo_refresh_attempted_{social_account.id}",
        ]
    )


def _list_social_cache_files(storage, deadline=None):
    """List the storage name of every persisted social photo.

    Azure storage (``list_all``) is listed with one recursive prefix query
    that the SDK pages through: no request per user folder, whatever
    ``listdir`` returns in a given django-storages version. Other backends
    (the local filesystem) list one level, so each users/<id>/social_cache/
    is walked, stopping at ``deadline`` (a time.monotonic() value).

    Returns:
        tuple: (names, complete) - complete is False when the walk stopped
        at the deadline.
    """
    list_all = getattr(storage, 'list_all', None)
    if list_all is not None:
        return [name for name in list_all('users/') if '/social_cache/' in name], True
    try:
        dirs, files = storage.listdir('users/')
    except FileNotFoundError:
        return [], True
    names = [name for name in files if '/social_cache/' in name]
    for user_dir in dirs:
        if deadline is not None and time.monotonic() >= deadline:
            return names, False
        folder = f"users/{user_dir}/social_cache/"
        try:
            _subdirs, cached = storage.listdir(folder)
        except FileNotFoundError:
            continue
        names.extend(folder + name for name in cached)
    return names, True


def sweep_orphaned_social_photo_cache(apply=False, deadline=None, limit=500):
    """
    Find, and with ``apply`` delete, persisted photos nobody may keep.

    A copy is kept only while its member is active, is not banned from
    Crush.lu and still has a SocialAccount for that provider. Anything else in
    users/<id>/social_cache/ is orphaned: left by a post-commit cleanup that
    failed during a storage outage, by a login that raced a deletion, or by an
    account deleted without Crush storage being touched (see
    _may_hold_social_photo_cache). Runs from gdpr_retention_cleanup.

    Deletes at most ``limit`` files and stops at ``deadline`` (a
    time.monotonic() value), during the listing as well as the deletes; the
    next run picks up the rest. Storage errors propagate.

    Returns:
        tuple: (orphaned_found, deleted, budget_exhausted)
    """
    from allauth.socialaccount.models import SocialAccount

    storage = _get_photo_storage()
    names, listed_all = _list_social_cache_files(storage, deadline)
    entries = []
    for name in names:
        parts = name.split('/')
        if len(parts) != 4 or not parts[1].isdigit():
            continue
        # <uuid>_<provider>.jpg (production naming) or <provider>.jpg
        provider = parts[3].rsplit('.', 1)[0].rsplit('_', 1)[-1]
        entries.append((name, int(parts[1]), provider))

    user_ids = sorted({user_id for _name, user_id, _provider in entries})
    keep = set()
    for start in range(0, len(user_ids), 500):
        keep.update(
            SocialAccount.objects.filter(
                user_id__in=user_ids[start:start + 500],
                user__is_active=True,
                provider__in=PHOTO_CACHE_PROVIDERS,
            )
            .exclude(user__data_consent__crushlu_banned=True)
            .values_list('user_id', 'provider')
        )
    orphaned = [
        name for name, user_id, provider in entries
        if (user_id, provider) not in keep
    ]

    deleted = 0
    if apply:
        for name in orphaned:
            if deleted >= limit or (
                deadline is not None and time.monotonic() >= deadline
            ):
                return len(orphaned), deleted, True
            storage.delete(name)
            deleted += 1
    return len(orphaned), deleted, not listed_all


def _get_persisted_social_photo_url(social_account):
    """
    Get a fresh storage URL for the persisted social photo.

    Returns a URL with a fresh SAS token (for Azure) or a local file URL.
    Uses a short cache timeout since SAS tokens expire in 30 minutes.

    Args:
        social_account: SocialAccount instance

    Returns:
        str: Storage URL or None if no persisted photo exists
    """
    # Quick check: do we know a persisted photo exists?
    exists_key = f"social_photo_persisted_exists_{social_account.id}"
    known_exists = cache.get(exists_key)

    if known_exists is False:
        return None

    storage = _get_photo_storage()
    path = _get_social_cache_path(social_account)

    try:
        if known_exists is True or storage.exists(path):
            cache.set(exists_key, True, SOCIAL_PHOTO_CACHE_TIMEOUT * 24)
            return storage.url(path)
        else:
            cache.set(exists_key, False, SOCIAL_PHOTO_CACHE_TIMEOUT * 24)
    except Exception as e:
        logger.debug(f"Error checking persisted social photo: {e}")

    return None


def refresh_social_photo_cache(social_account, token=None, force=False):
    """
    Download and persist the social photo for a given account.

    Should be called during social login when we have a fresh token.
    This ensures the persisted copy stays up-to-date.

    Skipped for members banned from Crush.lu (which includes everyone who
    deleted their Crush.lu profile): profile deletion erased this copy, and a
    later login must not collect it again.

    Throttled to at most once per SOCIAL_PHOTO_REFRESH_INTERVAL per account.
    Every call here is a full image download followed by
    ``process_uploaded_image`` and a blob-storage delete-and-write, and it sat
    unconditionally on the login request thread: production telemetry over 90
    days measured graph.facebook.com at avg 677ms / p90 1451ms per call (and
    Facebook makes two — one for the URL, one for the image), which is most of
    that provider's 3.86s callback. Profile pictures change on the order of
    months, so re-fetching one on every login bought nothing.

    The throttle marker is set only where network work is actually attempted,
    not at entry. Marking on the cheap early returns instead — no token, token
    expired, no picture URL — would mean a member who reconnects an expired
    account still waits out the full interval before their photo is refreshed.

    Args:
        social_account: SocialAccount instance
        token: Optional SocialToken (if not provided, will be looked up)
        force: Skip the throttle (for management commands / explicit refresh)

    Returns:
        bool: True if photo was persisted successfully
    """
    provider = social_account.provider
    throttle_key = f"social_photo_refresh_attempted_{social_account.id}"

    if not force:
        try:
            if cache.get(throttle_key):
                logger.debug(
                    f"Skipping {provider} photo refresh for account "
                    f"{social_account.id}: refreshed recently"
                )
                return False
        except Exception:
            # A cache outage must not stop a login; fall through and refresh.
            pass

    # After the throttle, so a throttled login pays no query for this.
    if _is_banned_from_crush(social_account.user_id):
        return False

    def _mark_attempted():
        """Record the attempt, successes and failures alike.

        Backing off on failure too is deliberate: an account whose token has
        gone permanently stale would otherwise pay the full network timeout on
        every single login, which is the exact cost this throttle exists to
        remove.
        """
        try:
            cache.set(throttle_key, True, SOCIAL_PHOTO_REFRESH_INTERVAL)
        except Exception:
            pass

    if provider == 'google':
        # Google photo URLs are public and don't expire, but we persist
        # them too for consistency
        extra_data = social_account.extra_data
        picture_url = extra_data.get('picture')
        if not picture_url:
            return False

        # Get high-res URL
        if '=s' in picture_url:
            picture_url = re.sub(r'=s\d+(-c)?', '=s720-c', picture_url)

        _mark_attempted()
        try:
            response = requests.get(picture_url, timeout=10)
            response.raise_for_status()
            if response.headers.get('Content-Type', '').startswith('image/'):
                return _persist_social_photo(social_account, response.content)
        except Exception as e:
            logger.warning(f"Failed to refresh Google photo cache: {e}")
        return False

    elif provider == 'facebook':
        if not token:
            token = _get_token_for_account(social_account)
        if not token or _is_token_expired(token):
            return False

        facebook_id = social_account.extra_data.get('id')
        if not facebook_id:
            return False

        # Fetch high-res photo URL from Graph API
        url = (
            f"https://graph.facebook.com/v24.0/{facebook_id}/picture"
            f"?width=720&height=720&redirect=false"
            f"&access_token={token.token}"
        )
        _mark_attempted()
        try:
            resp = requests.get(url, timeout=5)
            resp.raise_for_status()
            data = resp.json()
            photo_url = data.get('data', {}).get('url')
            if not photo_url:
                return False

            # Download the actual image
            img_resp = requests.get(photo_url, timeout=10)
            img_resp.raise_for_status()
            if img_resp.headers.get('Content-Type', '').startswith('image/'):
                return _persist_social_photo(social_account, img_resp.content)
        except Exception as e:
            logger.warning(
                f"Failed to refresh Facebook photo cache: {_safe_exception_text(e)}"
            )
        return False

    elif provider == 'microsoft':
        if not token:
            token = _get_token_for_account(social_account)
        if not token or _is_token_expired(token):
            return False

        _mark_attempted()
        try:
            response = requests.get(
                'https://graph.microsoft.com/v1.0/me/photo/$value',
                headers={'Authorization': f'Bearer {token.token}'},
                timeout=5,
            )
            if response.status_code == 200:
                return _persist_social_photo(social_account, response.content)
        except Exception as e:
            logger.warning(f"Failed to refresh Microsoft photo cache: {e}")
        return False

    return False


def get_facebook_photo_url(social_account):
    """
    Get Facebook profile photo URL from social account.

    Tries high-resolution first (720x720), falls back to standard picture,
    then falls back to persisted cache when token is expired.
    Results are cached to avoid slow API calls.

    Args:
        social_account: SocialAccount instance for Facebook

    Returns:
        str: Photo URL or None if unavailable
    """
    # Check cache first
    cache_key = f"social_photo_facebook_{social_account.id}"
    cached_url = cache.get(cache_key)
    if cached_url is not None:
        return cached_url if cached_url != '' else None

    extra_data = social_account.extra_data
    facebook_id = extra_data.get('id')
    result_url = None

    # Try high-resolution photo first via Graph API
    if facebook_id:
        token = _get_token_for_account(social_account)

        # Skip API call if token is expired - the result will be a broken URL anyway
        if _is_token_expired(token):
            logger.info(f"Facebook token expired for account {social_account.id}, skipping API call")
        else:
            # Request 720x720 photo (maximum size for profile pictures)
            url = f"https://graph.facebook.com/v24.0/{facebook_id}/picture?width=720&height=720&redirect=false"
            if token:
                url += f"&access_token={token.token}"

            try:
                response = requests.get(url, timeout=5)  # Reduced timeout
                response.raise_for_status()
                data = response.json()

                if data.get('data', {}).get('url'):
                    result_url = data['data']['url']
            except Exception as e:
                logger.warning(
                    "Could not get high-res Facebook photo: "
                    f"{_safe_exception_text(e)}"
                )

    # Fallback to standard picture from extra_data
    if not result_url and 'picture' in extra_data:
        if isinstance(extra_data['picture'], dict):
            result_url = extra_data['picture'].get('data', {}).get('url')
        else:
            result_url = extra_data['picture']

    # Fallback to persisted cache (works even when token is expired)
    if not result_url:
        result_url = _get_persisted_social_photo_url(social_account)
        if result_url:
            logger.info(f"Using persisted cache for Facebook account {social_account.id}")
            # Use shorter cache timeout for storage SAS URLs
            cache.set(cache_key, result_url, PERSISTED_PHOTO_CACHE_TIMEOUT)
            return result_url

    # Cache the result (use '' for None to distinguish from cache miss)
    cache.set(cache_key, result_url if result_url else '', SOCIAL_PHOTO_CACHE_TIMEOUT)
    return result_url


def get_google_photo_url(social_account):
    """
    Get Google profile photo URL from social account.

    Modifies the URL to request high-resolution version (720px).

    Args:
        social_account: SocialAccount instance for Google

    Returns:
        str: Photo URL or None if unavailable
    """
    extra_data = social_account.extra_data
    picture_url = extra_data.get('picture')

    if not picture_url:
        return None

    # Google photo URLs end with size parameter like =s96-c
    # We can replace this with =s720-c for higher resolution
    if '=s' in picture_url:
        # Replace size parameter with 720
        return re.sub(r'=s\d+(-c)?', '=s720-c', picture_url)
    elif '?sz=' in picture_url:
        # Alternative size parameter format
        return re.sub(r'\?sz=\d+', '?sz=720', picture_url)

    # Return original URL if no size parameter found
    return picture_url


def get_microsoft_photo_url(social_account):
    """
    Get Microsoft profile photo via Graph API.

    Microsoft doesn't include photo URL in OAuth extra_data.
    We need to fetch it using the stored access token.
    Falls back to persisted cache when token is expired.
    Results are cached to avoid slow API calls.

    Requires: ProfilePhoto.Read.All permission on Azure app registration.

    Args:
        social_account: SocialAccount instance for Microsoft

    Returns:
        str: Base64 data URL, storage URL, or None if unavailable
    """
    # Check cache first
    cache_key = f"social_photo_microsoft_{social_account.id}"
    cached_url = cache.get(cache_key)
    if cached_url is not None:
        return cached_url if cached_url != '' else None

    result_url = None
    try:
        token = _get_token_for_account(social_account)

        if not token or _is_token_expired(token):
            logger.warning(f"No valid token for Microsoft account {social_account.id}")
            # Fall back to persisted cache
            result_url = _get_persisted_social_photo_url(social_account)
            if result_url:
                logger.info(f"Using persisted cache for Microsoft account {social_account.id}")
                cache.set(cache_key, result_url, PERSISTED_PHOTO_CACHE_TIMEOUT)
                return result_url
            cache.set(cache_key, '', SOCIAL_PHOTO_CACHE_TIMEOUT)
            return None

        # Fetch photo from Microsoft Graph API with reduced timeout
        response = requests.get(
            'https://graph.microsoft.com/v1.0/me/photo/$value',
            headers={'Authorization': f'Bearer {token.token}'},
            timeout=5  # Reduced timeout
        )

        if response.status_code == 200:
            # Microsoft returns binary image data
            # Convert to base64 data URL for display
            import base64
            content_type = response.headers.get('Content-Type', 'image/jpeg')
            base64_data = base64.b64encode(response.content).decode('utf-8')
            result_url = f"data:{content_type};base64,{base64_data}"
        elif response.status_code == 404:
            # User has no profile photo set
            logger.info("Microsoft user has no profile photo set")
        else:
            logger.warning(f"Microsoft Graph API returned status {response.status_code}")

    except Exception as e:
        logger.error(f"Error fetching Microsoft photo: {str(e)}")

    # If API failed, try persisted cache as last resort
    if not result_url:
        result_url = _get_persisted_social_photo_url(social_account)
        if result_url:
            logger.info(f"Using persisted cache for Microsoft account {social_account.id}")
            cache.set(cache_key, result_url, PERSISTED_PHOTO_CACHE_TIMEOUT)
            return result_url

    # Cache the result (use '' for None to distinguish from cache miss)
    cache.set(cache_key, result_url if result_url else '', SOCIAL_PHOTO_CACHE_TIMEOUT)
    return result_url


def get_social_photo_url(social_account):
    """
    Get profile photo URL from any social account.

    Unified interface for all OAuth providers.

    Args:
        social_account: SocialAccount instance

    Returns:
        str: Photo URL (or base64 data URL for Microsoft) or None
    """
    provider = social_account.provider

    if provider == 'facebook':
        return get_facebook_photo_url(social_account)
    elif provider == 'google':
        return get_google_photo_url(social_account)
    elif provider == 'microsoft':
        return get_microsoft_photo_url(social_account)
    else:
        logger.warning(f"Unknown provider: {provider}")
        return None


def get_all_social_photos(user):
    """
    Get all available social photos for a user.

    Returns a list of dictionaries with photo information for each
    connected social account.

    Args:
        user: Django User instance

    Returns:
        list: List of dicts with keys:
            - provider: Provider name (facebook, google, microsoft)
            - provider_display: Display name (Facebook, Google, Microsoft)
            - photo_url: URL to photo or None
            - available: Boolean indicating if photo is available
            - account_id: SocialAccount ID for import API
            - reason: Optional explanation if not available
    """
    from allauth.socialaccount.models import SocialAccount

    # Only get Crush.lu supported providers
    CRUSH_PROVIDERS = ['facebook', 'google', 'microsoft']

    # Use select_related/prefetch_related to avoid N+1 queries when fetching tokens
    # SocialToken has a ForeignKey to SocialAccount, so we prefetch from the reverse relation
    social_accounts = SocialAccount.objects.filter(
        user=user,
        provider__in=CRUSH_PROVIDERS
    ).prefetch_related('socialtoken_set')

    photos = []
    for account in social_accounts:
        # Check token expiry before making API calls
        token = _get_token_for_account(account)
        token_expired = _is_token_expired(token)

        photo_url = get_social_photo_url(account)

        # Detect if the photo is from persisted cache (storage URL vs live URL)
        is_cached = False
        if photo_url and token_expired and account.provider in ('facebook', 'microsoft'):
            is_cached = True

        photo_info = {
            'provider': account.provider,
            'provider_display': PROVIDER_DISPLAY_NAMES.get(account.provider, account.provider.title()),
            'photo_url': photo_url,
            'available': photo_url is not None,
            'account_id': account.id,
            'token_expired': token_expired,
            'is_cached': is_cached,
        }

        # Add reason if not available
        if not photo_url:
            if token_expired:
                photo_info['reason'] = 'Token expired - please reconnect'
            elif account.provider == 'microsoft':
                photo_info['reason'] = 'No photo set'
            else:
                photo_info['reason'] = 'No photo available'
        elif is_cached:
            photo_info['reason'] = 'Showing saved copy - log in again to refresh'

        photos.append(photo_info)

    return photos


def download_and_save_social_photo(user, social_account, photo_slot):
    """
    Download photo from social account and save to specified profile photo slot.

    Handles the different photo URL formats:
    - Facebook/Google: Standard URLs
    - Microsoft: Base64 data URLs (already fetched)

    Args:
        user: Django User instance
        social_account: SocialAccount instance
        photo_slot: Integer 1, 2, or 3 for photo_1, photo_2, photo_3

    Returns:
        dict: {'success': True, 'photo_url': '...'} or {'success': False, 'error': '...'}
    """
    from crush_lu.models import CrushProfile

    # Validate photo_slot
    if photo_slot not in [1, 2, 3]:
        return {'success': False, 'error': 'Invalid photo slot. Must be 1, 2, or 3.'}

    # Get user's profile
    try:
        profile = CrushProfile.objects.get(user=user)
    except CrushProfile.DoesNotExist:
        return {'success': False, 'error': 'No profile found. Please create a profile first.'}

    # Get photo URL/data
    photo_url = get_social_photo_url(social_account)
    if not photo_url:
        return {'success': False, 'error': f'No photo available from {social_account.provider}'}

    try:
        # Handle base64 data URLs (Microsoft)
        if photo_url.startswith('data:'):
            import base64
            # Parse data URL: data:image/jpeg;base64,/9j/4AAQ...
            header, data = photo_url.split(',', 1)
            content_type = header.split(':')[1].split(';')[0]
            image_data = base64.b64decode(data)

            # Determine extension
            ext = 'jpg'
            if 'png' in content_type:
                ext = 'png'
            elif 'gif' in content_type:
                ext = 'gif'
            elif 'webp' in content_type:
                ext = 'webp'
        else:
            # Download from URL (Facebook/Google)
            response = requests.get(photo_url, timeout=15)
            response.raise_for_status()

            # Validate content type
            content_type = response.headers.get('Content-Type', '')
            if not content_type.startswith('image/'):
                return {'success': False, 'error': 'URL did not return an image'}

            image_data = response.content

            # Determine extension
            ext = 'jpg'
            if 'png' in content_type:
                ext = 'png'
            elif 'gif' in content_type:
                ext = 'gif'
            elif 'webp' in content_type:
                ext = 'webp'

        # Process image: fix orientation, strip EXIF metadata, resize
        filename = f'{social_account.provider}_{user.id}.{ext}'
        raw_file = ContentFile(image_data, name=filename)
        processed = process_uploaded_image(raw_file, filename)

        # Save to appropriate photo field. The replaced file is cleaned up by
        # CrushProfile.save(), which keeps it while other members still see
        # it as the approved photo (services/photo_publication.py).
        photo_field = getattr(profile, f'photo_{photo_slot}')
        photo_field.save(filename, processed, save=False)
        profile.save()

        # Get the new photo URL for response
        new_photo_url = photo_field.url if photo_field else None

        logger.info(f"Saved {social_account.provider} photo to photo_{photo_slot} for user {user.id}")
        return {'success': True, 'photo_url': new_photo_url}

    except requests.exceptions.Timeout:
        return {'success': False, 'error': 'Timeout downloading photo. Please try again.'}
    except requests.exceptions.RequestException as e:
        logger.error(f"Error downloading social photo: {str(e)}")
        return {'success': False, 'error': 'Error downloading photo. Please try again.'}
    except Exception as e:
        logger.error(f"Unexpected error saving social photo: {str(e)}", exc_info=True)
        return {'success': False, 'error': 'Unexpected error. Please try again.'}
