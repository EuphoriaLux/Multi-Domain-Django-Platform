"""
Custom Azure Storage backends for PowerUP platform

Provides separate storage containers for:
- PowerUP public media (powerup-media)
- PowerUP FinOps cost exports (powerup-finops)

Environment Variables:
    AZURE_POWERUP_MEDIA_CONTAINER - Public media container
                                    Default: 'powerup-media'
    AZURE_POWERUP_FINOPS_CONTAINER - FinOps cost export container
                                     Default: 'powerup-finops'
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


class PowerUpMediaStorage(AzureStorage):
    """
    Storage backend for PowerUP public media files

    Container: powerup-media (public, anonymous read)

    Used for:
    - Company logos (delegations)
    - Delegation profile photos
    - PowerUP branding assets
    - Default profile picture
    """

    def __init__(self, *args, **kwargs):
        # Get credentials from settings
        self.account_name = getattr(settings, 'AZURE_ACCOUNT_NAME', None)
        self.account_key = getattr(settings, 'AZURE_ACCOUNT_KEY', None)

        # Container name (configurable via environment)
        container_name = os.getenv(
            'AZURE_POWERUP_MEDIA_CONTAINER',
            'powerup-media'
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


class FinOpsStorage(AzureStorage):
    """
    Storage backend for Azure Cost Management exports

    Container: powerup-finops (private, read-only)

    Used for:
    - Azure Cost Management CSV exports
    - Partner-led export data
    - Subscription cost reports

    Note: This container is managed by Azure Cost Management service.
    The Django app has read-only access for analysis purposes.

    Lifecycle: 90-day retention (configurable in Azure Portal)
    """

    def __init__(self, *args, **kwargs):
        # Get credentials from settings
        self.account_name = getattr(settings, 'AZURE_ACCOUNT_NAME', None)
        self.account_key = getattr(settings, 'AZURE_ACCOUNT_KEY', None)

        # Container name (configurable via environment)
        container_name = os.getenv(
            'AZURE_POWERUP_FINOPS_CONTAINER',
            'powerup-finops'
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
        self.overwrite_files = False  # Read-only from Django perspective


# =============================================================================
# Upload Path Helpers for PowerUP Models
# =============================================================================

def _powerup_upload_path(subfolder: str, instance, filename: str) -> str:
    """
    Generate PowerUP upload path with UUID.

    Args:
        subfolder: Subfolder within powerup container (e.g., 'delegation/companies')
        instance: Model instance (not used but required by Django)
        filename: Original filename

    Returns:
        Path like: delegation/companies/{uuid}.{ext}
    """
    ext = os.path.splitext(filename)[1].lower()
    unique_filename = f"{uuid.uuid4().hex}{ext}"

    # Normalize subfolder (remove leading/trailing slashes)
    subfolder = subfolder.strip('/')

    if subfolder:
        return f"{subfolder}/{unique_filename}"
    return unique_filename


def powerup_upload_path(subfolder: str = ''):
    """
    Factory function to create PowerUP upload_to callables.

    Usage in models:
        logo = models.ImageField(
            upload_to=powerup_upload_path('delegation/companies'),
            storage=powerup_media_storage
        )

    Examples:
        powerup_upload_path('delegation/companies')
        -> delegation/companies/{uuid}.png

        powerup_upload_path('defaults')
        -> defaults/{uuid}.jpg
    """
    return partial(_powerup_upload_path, subfolder)


# =============================================================================
# Convenience Instances (for model field declarations)
# =============================================================================
# Note: These are callable factories, not instances, to avoid Django settings
# issues during module import. They will be instantiated when first accessed.

def get_powerup_media_storage():
    """Get PowerUP media storage instance (lazy initialization)."""
    return PowerUpMediaStorage()

def get_finops_storage():
    """Get FinOps storage instance (lazy initialization)."""
    return FinOpsStorage()

# Aliases for backward compatibility and convenience
powerup_media_storage = get_powerup_media_storage
finops_storage = get_finops_storage
