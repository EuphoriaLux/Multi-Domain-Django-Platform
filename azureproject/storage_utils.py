"""
Shared Azure Storage credential utilities for Managed Identity support.

Provides:
- DefaultAzureCredential for keyless authentication
- Cached UserDelegationKey for SAS token generation (private containers)
- Thread-safe BlobServiceClient singleton

Usage:
    from azureproject.storage_utils import get_blob_service_client, generate_user_delegation_sas

Production: Uses Managed Identity (DefaultAzureCredential)
Development: Falls back to AZURE_ACCOUNT_KEY if set, then to Azurite connection string
"""

import logging
import threading
from datetime import datetime, timedelta, timezone

from django.conf import settings

logger = logging.getLogger(__name__)

# Thread-safe singletons
_blob_service_client = None
_blob_service_lock = threading.Lock()
_user_delegation_key = None
_user_delegation_key_expiry = None
_delegation_key_lock = threading.Lock()


def _is_azurite_mode():
    return getattr(settings, 'AZURITE_MODE', False)


def get_blob_service_client():
    """
    Get a cached BlobServiceClient using Managed Identity or fallback credentials.

    Priority:
    1. Azurite mode -> connection string
    2. Managed Identity (DefaultAzureCredential) -> keyless auth
    3. Account key fallback (during migration) -> AZURE_ACCOUNT_KEY

    Returns:
        BlobServiceClient instance (cached, thread-safe)
    """
    global _blob_service_client

    if _blob_service_client is not None:
        return _blob_service_client

    with _blob_service_lock:
        # Double-check after acquiring lock
        if _blob_service_client is not None:
            return _blob_service_client

        from azure.storage.blob import BlobServiceClient

        if _is_azurite_mode():
            connection_string = getattr(settings, 'AZURE_CONNECTION_STRING', None)
            _blob_service_client = BlobServiceClient.from_connection_string(connection_string)
            logger.debug("BlobServiceClient initialized with Azurite connection string")
        else:
            account_name = getattr(settings, 'AZURE_ACCOUNT_NAME', None)
            account_url = f"https://{account_name}.blob.core.windows.net"
            account_key = getattr(settings, 'AZURE_ACCOUNT_KEY', None)

            if account_key:
                # Fallback: use account key during migration period
                _blob_service_client = BlobServiceClient(
                    account_url=account_url,
                    credential=account_key,
                )
                logger.debug("BlobServiceClient initialized with account key (migration fallback)")
            else:
                # Production: use Managed Identity
                from azure.identity import DefaultAzureCredential
                credential = DefaultAzureCredential()
                _blob_service_client = BlobServiceClient(
                    account_url=account_url,
                    credential=credential,
                )
                logger.debug("BlobServiceClient initialized with Managed Identity")

        return _blob_service_client


def get_user_delegation_key():
    """
    Get a cached UserDelegationKey for generating user-delegation SAS tokens.

    The key is cached for 6 hours (max allowed is 7 days, but shorter is safer).
    Thread-safe with double-checked locking.

    Returns:
        UserDelegationKey instance

    Raises:
        Exception if account key is being used (user delegation requires Managed Identity)
    """
    global _user_delegation_key, _user_delegation_key_expiry

    now = datetime.now(timezone.utc)

    # Check if cached key is still valid (with 5-minute buffer)
    if (_user_delegation_key is not None
            and _user_delegation_key_expiry is not None
            and now < _user_delegation_key_expiry - timedelta(minutes=5)):
        return _user_delegation_key

    with _delegation_key_lock:
        # Double-check after acquiring lock
        if (_user_delegation_key is not None
                and _user_delegation_key_expiry is not None
                and now < _user_delegation_key_expiry - timedelta(minutes=5)):
            return _user_delegation_key

        blob_service = get_blob_service_client()
        key_start = now - timedelta(minutes=5)  # Small buffer for clock skew
        key_expiry = now + timedelta(hours=6)

        _user_delegation_key = blob_service.get_user_delegation_key(
            key_start_time=key_start,
            key_expiry_time=key_expiry,
        )
        _user_delegation_key_expiry = key_expiry
        logger.debug("UserDelegationKey refreshed, valid until %s", key_expiry)

        return _user_delegation_key


def generate_sas_token(container_name, blob_name, permission, expiry_seconds=3600):
    """
    Generate a SAS token using either UserDelegationKey (Managed Identity)
    or account key (fallback).

    Args:
        container_name: Azure Blob container name
        blob_name: Blob path within container
        permission: BlobSasPermissions instance
        expiry_seconds: Token validity in seconds (default: 1 hour)

    Returns:
        SAS token string
    """
    from azure.storage.blob import generate_blob_sas

    account_name = getattr(settings, 'AZURE_ACCOUNT_NAME', None)
    account_key = getattr(settings, 'AZURE_ACCOUNT_KEY', None)
    expiry = datetime.now(timezone.utc) + timedelta(seconds=expiry_seconds)

    if account_key:
        # Fallback: account key SAS (during migration)
        return generate_blob_sas(
            account_name=account_name,
            account_key=account_key,
            container_name=container_name,
            blob_name=blob_name,
            permission=permission,
            expiry=expiry,
        )
    else:
        # Production: user delegation SAS (Managed Identity)
        delegation_key = get_user_delegation_key()
        return generate_blob_sas(
            account_name=account_name,
            container_name=container_name,
            blob_name=blob_name,
            permission=permission,
            expiry=expiry,
            user_delegation_key=delegation_key,
        )
