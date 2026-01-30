"""
Test script to verify all storage backends are accessible and properly configured.

Usage:
    python scripts/test_storage_backends.py
"""

import os
import sys
import django

# Add project root to Python path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'azureproject.settings')
django.setup()

from django.core.files.storage import storages
from django.conf import settings

def test_storage_backends():
    """Test that all storage backends can be instantiated."""
    print("=" * 80)
    print("Testing Storage Backend Configuration")
    print("=" * 80)
    print()

    # Check if using Azurite, Azure, or local filesystem
    if hasattr(settings, 'AZURITE_MODE') and settings.AZURITE_MODE:
        storage_mode = "Azurite (local emulator)"
    elif os.getenv('AZURE_ACCOUNT_NAME'):
        storage_mode = "Azure Blob Storage"
    else:
        storage_mode = "Local Filesystem"

    print(f"Storage Mode: {storage_mode}")
    print()

    # Test each storage alias
    aliases_to_test = [
        'default',
        'crush_media',
        'crush_private',
        'vinsdelux_media',
        'vinsdelux_private',
        'entreprinder_media',
        'powerup_media',
        'powerup_finops',
        'shared_media',
        'staticfiles',
    ]

    print("Available Storage Aliases:")
    print("-" * 80)

    success_count = 0
    failure_count = 0

    for alias in aliases_to_test:
        try:
            backend = storages[alias]
            backend_class = backend.__class__.__name__

            # For Azure backends, check container name
            if hasattr(backend, 'azure_container'):
                container = backend.azure_container
                print(f"  [OK] {alias:25s} -> {backend_class:40s} ({container})")
            else:
                print(f"  [OK] {alias:25s} -> {backend_class}")

            success_count += 1

        except Exception as e:
            print(f"  [FAIL] {alias:25s} -> ERROR: {e}")
            failure_count += 1

    print()
    print("=" * 80)
    print(f"Summary: {success_count} passed, {failure_count} failed")
    print("=" * 80)

    if failure_count > 0:
        print()
        print("FAILED: Some storage backends could not be instantiated")
        sys.exit(1)
    else:
        print()
        print("SUCCESS: All storage backends configured correctly")
        sys.exit(0)


if __name__ == '__main__':
    test_storage_backends()
