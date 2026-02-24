"""
Custom Azure Storage backend for Crush.lu with SAS token support
Provides secure, time-limited access to private blob storage

Supports:
- Production: Azure Blob Storage with HTTPS and SAS tokens
- Development: Azurite (local Azure emulator) with HTTP
- Fallback: Local filesystem

Setup Instructions:
1. Create a separate Azure Blob container named 'crush-lu-private'
   (or custom name via AZURE_PRIVATE_CONTAINER_NAME env var)
2. Set container access level to "Private (no anonymous access)"
3. Photos will be served with time-limited SAS tokens (default 1 hour)

Environment Variables:
    AZURE_PRIVATE_CONTAINER_NAME - Container name for private storage
                                   Default: 'crush-lu-private'
                                   Example for staging: 'crush-lu-private-staging'

User Storage Structure:
    users/{user_id}/.user_created    # Marker file
    users/{user_id}/photos/          # Profile photos
    users/{user_id}/exports/         # GDPR exports
"""

import os
import logging
from datetime import datetime, timedelta, timezone
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

logger = logging.getLogger(__name__)

# Conditional imports for Azure storage (only available in production)
# These packages are not installed in development environments
try:
    from storages.backends.azure_storage import AzureStorage
    from azure.storage.blob import generate_blob_sas, BlobSasPermissions
    AZURE_STORAGE_AVAILABLE = True
except ImportError:
    AzureStorage = object  # Placeholder for class inheritance
    AZURE_STORAGE_AVAILABLE = False
    logger.debug("Azure storage packages not available - using local filesystem")


def is_azurite_mode():
    """Check if we're running in Azurite (local emulator) mode."""
    return getattr(settings, 'AZURITE_MODE', False)


class PrivateAzureStorage(AzureStorage):
    """
    Custom Azure Storage backend that stores files privately
    and generates SAS tokens for temporary access.

    Supports both production Azure Blob Storage and local Azurite emulator.

    Authentication:
    - Production: Managed Identity (DefaultAzureCredential) with UserDelegationKey SAS
    - Migration fallback: AZURE_ACCOUNT_KEY if still set
    - Development: Azurite connection string

    Container name can be configured via AZURE_CRUSH_PRIVATE_CONTAINER env var.
    Default: 'crush-lu-private'
    Staging: 'crush-lu-private-staging' (set via env var)
    """
    # Container name configurable via environment variable
    # Check new env var first, fall back to legacy name for backward compatibility
    azure_container = os.getenv('AZURE_CRUSH_PRIVATE_CONTAINER',
                                 os.getenv('AZURE_PRIVATE_CONTAINER_NAME', 'crush-lu-private'))
    expiration_secs = 3600  # SAS token valid for 1 hour

    def __init__(self, *args, **kwargs):
        # Get credentials from settings (handles both Azurite and production)
        self.account_name = getattr(settings, 'AZURE_ACCOUNT_NAME', None)
        self.account_key = getattr(settings, 'AZURE_ACCOUNT_KEY', None)

        # Allow container name override from settings or environment
        # Priority: 1) New env var, 2) Legacy env var, 3) Default
        container_name = getattr(
            settings, 'AZURE_CRUSH_PRIVATE_CONTAINER',
            os.getenv('AZURE_CRUSH_PRIVATE_CONTAINER',
                     os.getenv('AZURE_PRIVATE_CONTAINER_NAME', 'crush-lu-private'))
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
        # Set container to private (no public access)
        self.overwrite_files = False

    def url(self, name, expire=None):
        """
        Generate a time-limited SAS URL for accessing the blob.

        Uses UserDelegationKey SAS (Managed Identity) in production,
        falls back to account key SAS during migration period.

        Args:
            name: Blob name (file path)
            expire: Optional expiration time in seconds (default: 1 hour)

        Returns:
            Secure URL with SAS token (HTTPS for production, HTTP for Azurite)
        """
        if not expire:
            expire = self.expiration_secs

        if self._is_azurite:
            # Azurite: use account key SAS (Managed Identity not supported)
            sas_token = generate_blob_sas(
                account_name=self.account_name,
                account_key=self.account_key,
                container_name=self.azure_container,
                blob_name=name,
                permission=BlobSasPermissions(read=True),
                expiry=datetime.now(timezone.utc) + timedelta(seconds=expire)
            )
            return (
                f"http://{self._azurite_host}/{self.account_name}/"
                f"{self.azure_container}/{name}?{sas_token}"
            )
        else:
            # Production: use shared utility (Managed Identity or account key fallback)
            from azureproject.storage_utils import generate_sas_token
            sas_token = generate_sas_token(
                container_name=self.azure_container,
                blob_name=name,
                permission=BlobSasPermissions(read=True),
                expiry_seconds=expire,
            )
            return (
                f"https://{self.account_name}.blob.core.windows.net/"
                f"{self.azure_container}/{name}?{sas_token}"
            )


class CrushMediaStorage(AzureStorage):
    """
    Storage backend for Crush.lu public media files

    Container: crush-lu-media (public, anonymous read)

    Used for:
    - Branding assets (social preview images, OG tags)
    - Event photos
    - Advent calendar backgrounds
    - Journey default images
    - Static assets (icons, illustrations)
    """

    def __init__(self, *args, **kwargs):
        # Get credentials from settings
        self.account_name = getattr(settings, 'AZURE_ACCOUNT_NAME', None)
        self.account_key = getattr(settings, 'AZURE_ACCOUNT_KEY', None)

        # Container name (configurable via environment)
        container_name = os.getenv(
            'AZURE_CRUSH_MEDIA_CONTAINER',
            'crush-lu-media'
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


class CrushProfilePhotoStorage(PrivateAzureStorage):
    """
    Specialized storage for Crush.lu profile photos
    Includes additional security checks and naming conventions

    Container: Configured via AZURE_CRUSH_PRIVATE_CONTAINER env var
    Default: 'crush-lu-private'
    """
    # Inherits container name from PrivateAzureStorage (configurable via env var)
    expiration_secs = 1800  # 30 minutes for profile photos

    def get_available_name(self, name, max_length=None):
        """
        Generate unique filename to prevent overwrites and enumeration
        Format: crush_profiles/{user_id}/{uuid}_{original_name}
        """
        import os
        import uuid
        from django.utils.text import get_valid_filename

        # Get the directory path and filename
        dir_name, file_name = os.path.split(name)
        file_root, file_ext = os.path.splitext(file_name)

        # Sanitize filename
        file_root = get_valid_filename(file_root)

        # Generate unique filename with UUID
        unique_filename = f"{uuid.uuid4().hex}_{file_root}{file_ext}"

        # Reconstruct path
        return os.path.join(dir_name, unique_filename)


def initialize_user_storage(user_id):
    """
    Create user storage folder structure with a marker file.

    This function creates the folder structure for a user in Azure Blob Storage
    (Azurite in development, Azure in production) or local filesystem as fallback.
    Azure Blob Storage doesn't have true folders, but creating a file at a path
    implicitly creates the "folder" structure.

    Structure created:
        users/{user_id}/.user_created    # Empty marker file

    The photos/ and exports/ subfolders are created implicitly when files
    are uploaded to them.

    Args:
        user_id: The Django User ID

    Returns:
        bool: True if successful, False otherwise
    """
    marker_path = f"users/{user_id}/.user_created"

    try:
        # Check storage mode: Azurite, Production Azure, or Local filesystem
        if is_azurite_mode() or os.getenv('AZURE_ACCOUNT_NAME'):
            # Use CrushProfilePhotoStorage for both Azurite and production Azure
            storage = CrushProfilePhotoStorage()

            # Check if marker already exists
            if storage.exists(marker_path):
                logger.debug(f"User storage already initialized for user {user_id}")
                return True

            # Create empty marker file
            storage.save(marker_path, ContentFile(b''))
            mode = "Azurite" if is_azurite_mode() else "Azure"
            logger.info(f"Initialized {mode} storage for user {user_id}")
        else:
            # Fallback: Use default storage (local filesystem)
            if default_storage.exists(marker_path):
                logger.debug(f"User storage already initialized for user {user_id}")
                return True

            # Ensure directory exists for local filesystem
            full_path = os.path.join(settings.MEDIA_ROOT, f"users/{user_id}")
            os.makedirs(full_path, exist_ok=True)

            # Create empty marker file
            default_storage.save(marker_path, ContentFile(b''))
            logger.info(f"Initialized local storage for user {user_id}")

        return True

    except Exception as e:
        logger.error(f"Failed to initialize storage for user {user_id}: {str(e)}")
        return False


def user_storage_exists(user_id):
    """
    Check if user storage folder has been initialized.

    Args:
        user_id: The Django User ID

    Returns:
        bool: True if user storage exists, False otherwise
    """
    marker_path = f"users/{user_id}/.user_created"

    try:
        if is_azurite_mode() or os.getenv('AZURE_ACCOUNT_NAME'):
            storage = CrushProfilePhotoStorage()
            return storage.exists(marker_path)
        else:
            return default_storage.exists(marker_path)
    except Exception as e:
        logger.error(f"Error checking storage for user {user_id}: {str(e)}")
        return False


def delete_user_storage(user_id):
    """
    Delete all blobs in a user's storage folder from ALL containers.

    Removes all files under users/{user_id}/ from:
        - 'media' container (public) - actual photos
        - 'crush-lu-private' container - marker files

    Works with both Azurite (local emulator) and production Azure Blob Storage.
    For local filesystem fallback, deletes the entire user directory.

    Args:
        user_id: The Django User ID

    Returns:
        tuple: (success: bool, deleted_count: int)
    """
    prefix = f"users/{user_id}/"
    deleted_count = 0

    try:
        if is_azurite_mode() or os.getenv('AZURE_ACCOUNT_NAME'):
            # Azure Blob Storage (Azurite or production)
            # Need to clean up BOTH containers:
            # 1. 'media' (public) - where actual photos are stored
            # 2. 'crush-lu-private' - where marker files are stored

            from azureproject.storage_utils import get_blob_service_client
            blob_service = get_blob_service_client()

            # Containers to clean up
            # Use new env var first, fall back to legacy for backward compatibility
            private_container = os.getenv('AZURE_CRUSH_PRIVATE_CONTAINER',
                                         os.getenv('AZURE_PRIVATE_CONTAINER_NAME', 'crush-lu-private'))
            containers = [
                getattr(settings, 'AZURE_CONTAINER_NAME', 'media'),  # Public media
                private_container,  # Private profile data
            ]

            for container_name in containers:
                try:
                    container_client = blob_service.get_container_client(container_name)

                    # List all blobs with the user's prefix
                    blobs = list(container_client.list_blobs(name_starts_with=prefix))

                    for blob in blobs:
                        try:
                            container_client.delete_blob(blob.name)
                            deleted_count += 1
                            logger.debug(f"Deleted blob: {container_name}/{blob.name}")
                        except Exception as e:
                            logger.warning(
                                f"Failed to delete blob {container_name}/{blob.name}: {e}"
                            )

                    if blobs:
                        logger.debug(
                            f"Deleted {len(blobs)} blob(s) from {container_name} "
                            f"for user {user_id}"
                        )

                except Exception as e:
                    logger.warning(f"Error accessing container {container_name}: {e}")

            if deleted_count > 0:
                mode = "Azurite" if is_azurite_mode() else "Azure"
                logger.info(
                    f"Deleted {deleted_count} blob(s) from {mode} storage "
                    f"for user {user_id}"
                )
            else:
                logger.debug(f"No blobs found for user {user_id}")

        else:
            # Local filesystem fallback
            import shutil
            user_dir = os.path.join(settings.MEDIA_ROOT, f"users/{user_id}")

            if os.path.exists(user_dir):
                # Count files before deletion
                for root, dirs, files in os.walk(user_dir):
                    deleted_count += len(files)

                shutil.rmtree(user_dir)
                logger.info(
                    f"Deleted local storage folder for user {user_id} "
                    f"({deleted_count} files)"
                )
            else:
                logger.debug(f"No local storage folder found for user {user_id}")

        return (True, deleted_count)

    except Exception as e:
        logger.error(f"Failed to delete storage for user {user_id}: {e}")
        return (False, deleted_count)


def list_user_storage_folders():
    """
    List all user IDs that have storage folders in blob storage.

    Scans the users/ prefix in ALL containers and extracts unique user IDs.
    Used by the cleanup_orphan_storage management command to find orphaned folders.

    Containers scanned:
        - 'media' (public) - actual photos
        - 'crush-lu-private' - marker files

    Returns:
        set: Set of user IDs (as integers) that have storage folders
    """
    user_ids = set()

    try:
        if is_azurite_mode() or os.getenv('AZURE_ACCOUNT_NAME'):
            # Azure Blob Storage (Azurite or production)
            from azureproject.storage_utils import get_blob_service_client
            blob_service = get_blob_service_client()

            # Scan both containers
            # Use new env var first, fall back to legacy for backward compatibility
            private_container = os.getenv('AZURE_CRUSH_PRIVATE_CONTAINER',
                                         os.getenv('AZURE_PRIVATE_CONTAINER_NAME', 'crush-lu-private'))
            containers = [
                getattr(settings, 'AZURE_CONTAINER_NAME', 'media'),
                private_container,
            ]

            for container_name in containers:
                try:
                    container_client = blob_service.get_container_client(container_name)
                    blobs = container_client.list_blobs(name_starts_with="users/")

                    for blob in blobs:
                        parts = blob.name.split('/')
                        if len(parts) >= 2 and parts[0] == "users":
                            try:
                                user_id = int(parts[1])
                                if user_id > 0:
                                    user_ids.add(user_id)
                            except ValueError:
                                logger.warning(f"Invalid user folder path: {blob.name}")

                except Exception as e:
                    logger.warning(f"Error scanning container {container_name}: {e}")

        else:
            # Local filesystem fallback
            users_dir = os.path.join(settings.MEDIA_ROOT, "users")

            if os.path.exists(users_dir):
                for folder_name in os.listdir(users_dir):
                    folder_path = os.path.join(users_dir, folder_name)
                    if os.path.isdir(folder_path):
                        try:
                            user_id = int(folder_name)
                            if user_id > 0:
                                user_ids.add(user_id)
                        except ValueError:
                            logger.warning(f"Invalid user folder: {folder_name}")

        logger.debug(f"Found {len(user_ids)} user storage folders")
        return user_ids

    except Exception as e:
        logger.error(f"Failed to list user storage folders: {e}")
        return set()


# =============================================================================
# Domain-Prefixed Upload Paths for Public Media
# =============================================================================
#
# Blob Structure in 'media' container (public):
#     crush-lu/           -> Crush.lu public assets
#     powerup/            -> PowerUP public assets
#     vinsdelux/          -> VinsDelux public assets
#     shared/             -> Cross-domain assets
#
# Usage:
#     from crush_lu.storage import vinsdelux_upload_path
#
#     class VdlProducer(models.Model):
#         logo = models.ImageField(upload_to=vinsdelux_upload_path('producers/logos'))
# =============================================================================

import uuid
from functools import partial


# Domain prefixes for blob storage organization
DOMAIN_PREFIXES = {
    'crush_lu': '',
    'powerup': '',
    'vinsdelux': '',
    'shared': '',
}


def _domain_upload_path(domain: str, subfolder: str, instance, filename: str) -> str:
    """
    Generate an upload path with optional domain prefix and UUID.

    Args:
        domain: Domain prefix (empty string if not needed)
        subfolder: Subfolder (e.g., 'producers/logos')
        instance: Model instance (not used but required by Django)
        filename: Original filename

    Returns:
        Path like: producers/logos/{uuid}.{ext}
    """
    ext = os.path.splitext(filename)[1].lower()
    unique_filename = f"{uuid.uuid4().hex}{ext}"

    # Normalize subfolder (remove leading/trailing slashes)
    subfolder = subfolder.strip('/')

    parts = [p for p in (domain, subfolder) if p]
    parts.append(unique_filename)
    return '/'.join(parts)


def _make_upload_path(domain: str, subfolder: str = ''):
    """
    Factory function to create domain-prefixed upload_to callables.

    Args:
        domain: Domain key from DOMAIN_PREFIXES
        subfolder: Optional subfolder within domain

    Returns:
        Callable suitable for ImageField/FileField upload_to parameter
    """
    prefix = DOMAIN_PREFIXES.get(domain, domain)
    return partial(_domain_upload_path, prefix, subfolder)


def vinsdelux_upload_path(subfolder: str = ''):
    """
    Create upload path for VinsDelux assets.

    Examples:
        upload_to=vinsdelux_upload_path('producers/logos')
        -> producers/logos/{uuid}.{ext}

        upload_to=vinsdelux_upload_path('products/gallery')
        -> products/gallery/{uuid}.{ext}
    """
    return _make_upload_path('vinsdelux', subfolder)


def crush_upload_path(subfolder: str = ''):
    """
    Create upload path for Crush.lu PUBLIC assets.

    Note: For private user photos, use user_photo_path in models/profiles.py
    which stores in the crush-lu-private container.

    Examples:
        upload_to=crush_upload_path('branding')
        -> branding/{uuid}.{ext}

        upload_to=crush_upload_path('advent/backgrounds')
        -> advent/backgrounds/{uuid}.{ext}
    """
    return _make_upload_path('crush_lu', subfolder)


def powerup_upload_path(subfolder: str = ''):
    """
    Create upload path for PowerUP assets.

    Examples:
        upload_to=powerup_upload_path('defaults')
        -> defaults/{uuid}.{ext}

        upload_to=powerup_upload_path('companies/logos')
        -> companies/logos/{uuid}.{ext}
    """
    return _make_upload_path('powerup', subfolder)


def shared_upload_path(subfolder: str = ''):
    """
    Create upload path for shared cross-domain assets.

    Examples:
        upload_to=shared_upload_path('homepage')
        -> homepage/{uuid}.{ext}
    """
    return _make_upload_path('shared', subfolder)


# =============================================================================
# Convenience Instances (for model field declarations)
# =============================================================================
# Note: This is a callable factory, not an instance, to avoid Django settings
# issues during module import. It will be instantiated when first accessed.

def get_crush_media_storage():
    """Get Crush.lu media storage instance (lazy initialization)."""
    return CrushMediaStorage()

# Alias for backward compatibility and convenience
crush_media_storage = get_crush_media_storage
