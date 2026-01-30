"""
Custom Azure Storage backends for VinsDelux platform

Provides separate storage containers for:
- VinsDelux public media (vinsdelux-media)
- VinsDelux private documents (vinsdelux-private) - future use

Environment Variables:
    AZURE_VINSDELUX_MEDIA_CONTAINER - Public media container
                                      Default: 'vinsdelux-media'
    AZURE_VINSDELUX_PRIVATE_CONTAINER - Private document container
                                        Default: 'vinsdelux-private'
"""

import os
import uuid
import logging
from datetime import datetime, timedelta, timezone
from functools import partial
from django.conf import settings

logger = logging.getLogger(__name__)

# Conditional imports for Azure storage
try:
    from storages.backends.azure_storage import AzureStorage
    from azure.storage.blob import generate_blob_sas, BlobSasPermissions
    AZURE_STORAGE_AVAILABLE = True
except ImportError:
    AzureStorage = object  # Placeholder
    AZURE_STORAGE_AVAILABLE = False
    logger.debug("Azure storage packages not available - using local filesystem")


def is_azurite_mode():
    """Check if we're running in Azurite (local emulator) mode."""
    return getattr(settings, 'AZURITE_MODE', False)


class VdlMediaStorage(AzureStorage):
    """
    Storage backend for VinsDelux public media files

    Container: vinsdelux-media (public, anonymous read)

    Used for:
    - Producer logos and photos
    - Wine category images
    - Product photos (bottles, coffrets)
    - Blog post images
    - Adoption plan marketing images
    - Journey default images
    - Vineyard default images
    """

    def __init__(self, *args, **kwargs):
        # Get credentials from settings
        self.account_name = getattr(settings, 'AZURE_ACCOUNT_NAME', None)
        self.account_key = getattr(settings, 'AZURE_ACCOUNT_KEY', None)

        # Container name (configurable via environment)
        container_name = os.getenv(
            'AZURE_VINSDELUX_MEDIA_CONTAINER',
            'vinsdelux-media'
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
        self.overwrite_files = False  # Prevent accidental overwrites


class VdlPrivateStorage(AzureStorage):
    """
    Storage backend for VinsDelux private documents (future use)

    Container: vinsdelux-private (private, SAS token access)

    Planned for:
    - Order invoices (PDF)
    - Adoption certificates
    - Legal contracts
    - Customer data exports

    SAS token expiration: 2 hours (7200 seconds)
    """
    expiration_secs = 7200  # 2 hours for document access

    def __init__(self, *args, **kwargs):
        # Get credentials from settings
        self.account_name = getattr(settings, 'AZURE_ACCOUNT_NAME', None)
        self.account_key = getattr(settings, 'AZURE_ACCOUNT_KEY', None)

        # Container name (configurable via environment)
        container_name = os.getenv(
            'AZURE_VINSDELUX_PRIVATE_CONTAINER',
            'vinsdelux-private'
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
        self.overwrite_files = False  # Private documents should never be overwritten

    def url(self, name, expire=None):
        """
        Generate a time-limited SAS URL for accessing private documents.

        Args:
            name: Blob name (file path)
            expire: Optional expiration time in seconds (default: 2 hours)

        Returns:
            Secure URL with SAS token
        """
        if not AZURE_STORAGE_AVAILABLE:
            # Fallback for local development
            return super().url(name)

        if not expire:
            expire = self.expiration_secs

        # Generate SAS token with read permissions
        sas_token = generate_blob_sas(
            account_name=self.account_name,
            account_key=self.account_key,
            container_name=self.azure_container,
            blob_name=name,
            permission=BlobSasPermissions(read=True),
            expiry=datetime.now(timezone.utc) + timedelta(seconds=expire)
        )

        # Build URL based on environment
        if self._is_azurite:
            return (
                f"http://{self._azurite_host}/{self.account_name}/"
                f"{self.azure_container}/{name}?{sas_token}"
            )
        else:
            return (
                f"https://{self.account_name}.blob.core.windows.net/"
                f"{self.azure_container}/{name}?{sas_token}"
            )


# =============================================================================
# Upload Path Helpers for VinsDelux Models
# =============================================================================

def _vinsdelux_upload_path(subfolder: str, instance, filename: str) -> str:
    """
    Generate VinsDelux upload path with UUID.

    Args:
        subfolder: Subfolder within vinsdelux container (e.g., 'producers/logos')
        instance: Model instance (not used but required by Django)
        filename: Original filename

    Returns:
        Path like: producers/logos/{uuid}.{ext}
    """
    ext = os.path.splitext(filename)[1].lower()
    unique_filename = f"{uuid.uuid4().hex}{ext}"

    # Normalize subfolder (remove leading/trailing slashes)
    subfolder = subfolder.strip('/')

    if subfolder:
        return f"{subfolder}/{unique_filename}"
    return unique_filename


def vinsdelux_upload_path(subfolder: str = ''):
    """
    Factory function to create VinsDelux upload_to callables.

    Usage in models:
        logo = models.ImageField(
            upload_to=vinsdelux_upload_path('producers/logos'),
            storage=vinsdelux_media_storage
        )

    Examples:
        vinsdelux_upload_path('producers/logos')
        -> producers/logos/{uuid}.jpg

        vinsdelux_upload_path('products/gallery')
        -> products/gallery/{uuid}.png
    """
    return partial(_vinsdelux_upload_path, subfolder)


# =============================================================================
# Convenience Instances (for model field declarations)
# =============================================================================
# Note: These are callable factories, not instances, to avoid Django settings
# issues during module import. They will be instantiated when first accessed.

def get_vinsdelux_media_storage():
    """Get VinsDelux media storage instance (lazy initialization)."""
    return VdlMediaStorage()

def get_vinsdelux_private_storage():
    """Get VinsDelux private storage instance (lazy initialization)."""
    return VdlPrivateStorage()

# Aliases for backward compatibility and convenience
vinsdelux_media_storage = get_vinsdelux_media_storage
vinsdelux_private_storage = get_vinsdelux_private_storage
