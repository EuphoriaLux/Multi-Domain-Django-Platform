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
"""

import logging
import re
import requests
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.utils import timezone
from .utils.image_processing import process_uploaded_image

logger = logging.getLogger(__name__)

# Cache timeout for social photo URLs (1 hour)
# Profile pictures rarely change, and longer caching reduces slow Facebook API calls
SOCIAL_PHOTO_CACHE_TIMEOUT = 3600

# Provider display names
PROVIDER_DISPLAY_NAMES = {
    'facebook': 'Facebook',
    'google': 'Google',
    'microsoft': 'Microsoft',
}


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


def get_facebook_photo_url(social_account):
    """
    Get Facebook profile photo URL from social account.

    Tries high-resolution first (720x720), falls back to standard picture.
    Results are cached for 1 hour to avoid slow API calls.

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
                logger.warning(f"Could not get high-res Facebook photo: {str(e)}")

    # Fallback to standard picture from extra_data
    if not result_url and 'picture' in extra_data:
        if isinstance(extra_data['picture'], dict):
            result_url = extra_data['picture'].get('data', {}).get('url')
        else:
            result_url = extra_data['picture']

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
    Results are cached for 1 hour to avoid slow API calls.

    Requires: ProfilePhoto.Read.All permission on Azure app registration.

    Args:
        social_account: SocialAccount instance for Microsoft

    Returns:
        str: Base64 data URL or None if unavailable
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
            logger.info(f"Microsoft user has no profile photo set")
        else:
            logger.warning(f"Microsoft Graph API returned status {response.status_code}")

    except Exception as e:
        logger.error(f"Error fetching Microsoft photo: {str(e)}")

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

        photo_info = {
            'provider': account.provider,
            'provider_display': PROVIDER_DISPLAY_NAMES.get(account.provider, account.provider.title()),
            'photo_url': photo_url,
            'available': photo_url is not None,
            'account_id': account.id,
            'token_expired': token_expired,
        }

        # Add reason if not available
        if not photo_url:
            if token_expired:
                photo_info['reason'] = 'Token expired - please reconnect'
            elif account.provider == 'microsoft':
                photo_info['reason'] = 'No photo set'
            else:
                photo_info['reason'] = 'No photo available'

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

        # Save to appropriate photo field
        photo_field = getattr(profile, f'photo_{photo_slot}')

        # Delete existing photo if any
        if photo_field:
            try:
                photo_field.delete(save=False)
            except Exception:
                pass

        # Save new photo
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
