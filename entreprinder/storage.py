"""
Custom Azure Storage backend for Entreprinder platform

Provides:
- Entreprinder public media (entreprinder-media)

Environment Variables:
    AZURE_ENTREPRINDER_MEDIA_CONTAINER - Public media container
                                         Default: 'entreprinder-media'

Note: Entreprinder currently uses LinkedIn profile URLs for most images.
This storage is for platform branding and default assets.
"""

import os
import uuid
import logging
from functools import partial
from django.conf import settings

logger = logging.getLogger(__name__)

# Conditional imports for Azure storage
try:
    from storages.backends.azure_storage import AzureStorage
    AZURE_STORAGE_AVAILABLE = True
except ImportError:
    AzureStorage = object  # Placeholder
    AZURE_STORAGE_AVAILABLE = False
    logger.debug("Azure storage packages not available - using local filesystem")


def is_azurite_mode():
    """Check if we're running in Azurite (local emulator) mode."""
    return getattr(settings, 'AZURITE_MODE', False)


class EntreprinderMediaStorage(AzureStorage):
    """
    Storage backend for Entreprinder public media files

    Container: entreprinder-media (public, anonymous read)

    Used for:
    - Default profile picture
    - Networking event photos
    - Industry icons/images
    - Platform branding assets
    """

    def __init__(self, *args, **kwargs):
        # Get credentials from settings
        self.account_name = getattr(settings, 'AZURE_ACCOUNT_NAME', None)
        self.account_key = getattr(settings, 'AZURE_ACCOUNT_KEY', None)

        # Container name (configurable via environment)
        container_name = os.getenv(
            'AZURE_ENTREPRINDER_MEDIA_CONTAINER',
            'entreprinder-media'
        )
        self.azure_container = container_name

        # Azurite-specific configuration
        self._is_azurite = is_azurite_mode()
        if self._is_azurite:
            self.connection_string = getattr(settings, 'AZURE_CONNECTION_STRING', None)
            self.azure_ssl = False  # Azurite uses HTTP
            self._azurite_host = getattr(settings, 'AZURITE_BLOB_HOST', '127.0.0.1:10000')
        else:
            self.azure_ssl = True  # Production uses HTTPS

        super().__init__(*args, **kwargs)
        self.overwrite_files = False


# =============================================================================
# Upload Path Helpers for Entreprinder Models
# =============================================================================

def _entreprinder_upload_path(subfolder: str, instance, filename: str) -> str:
    """
    Generate Entreprinder upload path with UUID.

    Args:
        subfolder: Subfolder within entreprinder container (e.g., 'defaults')
        instance: Model instance (not used but required by Django)
        filename: Original filename

    Returns:
        Path like: defaults/{uuid}.{ext}
    """
    ext = os.path.splitext(filename)[1].lower()
    unique_filename = f"{uuid.uuid4().hex}{ext}"

    # Normalize subfolder (remove leading/trailing slashes)
    subfolder = subfolder.strip('/')

    if subfolder:
        return f"{subfolder}/{unique_filename}"
    return unique_filename


def entreprinder_upload_path(subfolder: str = ''):
    """
    Factory function to create Entreprinder upload_to callables.

    Usage in models:
        logo = models.ImageField(
            upload_to=entreprinder_upload_path('industries'),
            storage=entreprinder_media_storage
        )

    Examples:
        entreprinder_upload_path('defaults')
        -> defaults/{uuid}.png

        entreprinder_upload_path('events')
        -> events/{uuid}.jpg
    """
    return partial(_entreprinder_upload_path, subfolder)


# =============================================================================
# Convenience Instance (for model field declarations)
# =============================================================================
# Note: This is a callable factory, not an instance, to avoid Django settings
# issues during module import. It will be instantiated when first accessed.

def get_entreprinder_media_storage():
    """Get Entreprinder media storage instance (lazy initialization)."""
    return EntreprinderMediaStorage()

# Alias for backward compatibility and convenience
entreprinder_media_storage = get_entreprinder_media_storage
