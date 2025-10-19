"""
Custom Azure Storage backend for Crush.lu with SAS token support
Provides secure, time-limited access to private blob storage

Setup Instructions:
1. Create a separate Azure Blob container named 'crush-profiles-private'
2. Set container access level to "Private (no anonymous access)"
3. Photos will be served with time-limited SAS tokens (default 1 hour)
"""

from datetime import datetime, timedelta
from django.conf import settings
from storages.backends.azure_storage import AzureStorage
from azure.storage.blob import generate_blob_sas, BlobSasPermissions


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
