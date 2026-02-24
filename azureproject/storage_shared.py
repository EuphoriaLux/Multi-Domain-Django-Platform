"""
Shared Azure Storage backend for cross-platform assets

Provides:
- Shared media storage (shared-media) for assets used across multiple platforms

Environment Variables:
    AZURE_SHARED_MEDIA_CONTAINER - Shared media container
                                   Default: 'shared-media'

Used for:
- Multi-domain homepage assets
- Legal documents (terms, privacy policy)
- Shared web fonts
- Common icon libraries
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


class SharedMediaStorage(AzureStorage):
    """
    Storage backend for cross-platform shared assets

    Container: shared-media (public, anonymous read)

    Used for:
    - Homepage assets used across domains
    - Legal documents (Terms of Service, Privacy Policy)
    - Web fonts (if self-hosted)
    - Shared icon libraries
    - Common UI components
    """

    def __init__(self, *args, **kwargs):
        # Get credentials from settings
        self.account_name = getattr(settings, 'AZURE_ACCOUNT_NAME', None)
        self.account_key = getattr(settings, 'AZURE_ACCOUNT_KEY', None)

        # Container name (configurable via environment)
        container_name = os.getenv(
            'AZURE_SHARED_MEDIA_CONTAINER',
            'shared-media'
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
            # Production: prefer Managed Identity over account key
            if not self.account_key:
                from azure.identity import DefaultAzureCredential
                self.credential = DefaultAzureCredential()

        super().__init__(*args, **kwargs)
        self.overwrite_files = False


# =============================================================================
# Upload Path Helpers for Shared Assets
# =============================================================================

def _shared_upload_path(subfolder: str, instance, filename: str) -> str:
    """
    Generate shared upload path with UUID.

    Args:
        subfolder: Subfolder within shared container (e.g., 'homepage')
        instance: Model instance (not used but required by Django)
        filename: Original filename

    Returns:
        Path like: homepage/{uuid}.{ext}
    """
    ext = os.path.splitext(filename)[1].lower()
    unique_filename = f"{uuid.uuid4().hex}{ext}"

    # Normalize subfolder (remove leading/trailing slashes)
    subfolder = subfolder.strip('/')

    if subfolder:
        return f"{subfolder}/{unique_filename}"
    return unique_filename


def shared_upload_path(subfolder: str = ''):
    """
    Factory function to create shared asset upload_to callables.

    Usage in models:
        document = models.FileField(
            upload_to=shared_upload_path('legal'),
            storage=shared_media_storage
        )

    Examples:
        shared_upload_path('homepage')
        -> homepage/{uuid}.jpg

        shared_upload_path('legal')
        -> legal/{uuid}.pdf
    """
    return partial(_shared_upload_path, subfolder)


# =============================================================================
# Convenience Instance (for model field declarations)
# =============================================================================
# Note: This is a callable factory, not an instance, to avoid Django settings
# issues during module import. It will be instantiated when first accessed.

def get_shared_media_storage():
    """Get shared media storage instance (lazy initialization)."""
    return SharedMediaStorage()

# Alias for backward compatibility and convenience
shared_media_storage = get_shared_media_storage
