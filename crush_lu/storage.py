"""
Custom Azure Storage backend for Crush.lu with SAS token support
Provides secure, time-limited access to private blob storage

Setup Instructions:
1. Create a separate Azure Blob container named 'crush-profiles-private'
2. Set container access level to "Private (no anonymous access)"
3. Photos will be served with time-limited SAS tokens (default 1 hour)

User Storage Structure:
    users/{user_id}/.user_created    # Marker file
    users/{user_id}/photos/          # Profile photos
    users/{user_id}/exports/         # GDPR exports
"""

import os
import logging
from datetime import datetime, timedelta
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


class PrivateAzureStorage(AzureStorage):
    """
    Custom Azure Storage backend that stores files privately
    and generates SAS tokens for temporary access
    """
    account_name = getattr(settings, 'AZURE_ACCOUNT_NAME', None)
    account_key = getattr(settings, 'AZURE_ACCOUNT_KEY', None)
    azure_container = 'crush-profiles-private'  # Separate private container
    expiration_secs = 3600  # SAS token valid for 1 hour

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Set container to private (no public access)
        self.overwrite_files = False
        self.azure_ssl = True

    def url(self, name, expire=None):
        """
        Generate a time-limited SAS URL for accessing the blob

        Args:
            name: Blob name (file path)
            expire: Optional expiration time in seconds (default: 1 hour)

        Returns:
            Secure URL with SAS token
        """
        if not expire:
            expire = self.expiration_secs

        # Generate SAS token with read permissions
        sas_token = generate_blob_sas(
            account_name=self.account_name,
            account_key=self.account_key,
            container_name=self.azure_container,
            blob_name=name,
            permission=BlobSasPermissions(read=True),
            expiry=datetime.utcnow() + timedelta(seconds=expire)
        )

        # Return full URL with SAS token
        return f"https://{self.account_name}.blob.core.windows.net/{self.azure_container}/{name}?{sas_token}"


class CrushProfilePhotoStorage(PrivateAzureStorage):
    """
    Specialized storage for Crush.lu profile photos
    Includes additional security checks and naming conventions
    """
    azure_container = 'crush-profiles-private'  # Use separate private container
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
    (or local filesystem in development). Azure Blob Storage doesn't have true
    folders, but creating a file at a path implicitly creates the "folder" structure.

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
        # Check if we're in production (Azure) or development (local)
        if os.getenv('AZURE_ACCOUNT_NAME'):
            # Production: Use CrushProfilePhotoStorage
            storage = CrushProfilePhotoStorage()

            # Check if marker already exists
            if storage.exists(marker_path):
                logger.debug(f"User storage already initialized for user {user_id}")
                return True

            # Create empty marker file
            storage.save(marker_path, ContentFile(b''))
            logger.info(f"Initialized Azure storage for user {user_id}")
        else:
            # Development: Use default storage (local filesystem)
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
        if os.getenv('AZURE_ACCOUNT_NAME'):
            storage = CrushProfilePhotoStorage()
            return storage.exists(marker_path)
        else:
            return default_storage.exists(marker_path)
    except Exception as e:
        logger.error(f"Error checking storage for user {user_id}: {str(e)}")
        return False
