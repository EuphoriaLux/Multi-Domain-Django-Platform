#!/usr/bin/env python
"""
Migrate Azure Blob Storage to domain-based folder structure.

This script reorganizes blobs in the 'media' container to use domain prefixes:
- crush-lu/     -> Crush.lu public assets
- powerup/      -> PowerUP public assets
- vinsdelux/    -> VinsDelux public assets
- shared/       -> Cross-domain assets

Current structure -> New structure:
- producers/    -> vinsdelux/producers/
- products/     -> vinsdelux/products/
- homepage/     -> shared/homepage/

Usage:
    # Dry run (preview changes)
    python scripts/migrate_blob_structure.py --dry-run

    # Execute migration
    python scripts/migrate_blob_structure.py

    # With custom storage account (production)
    python scripts/migrate_blob_structure.py --account-name mediabjnukuybtvjdy

Environment variables (for production):
    AZURE_ACCOUNT_NAME - Storage account name
    AZURE_ACCOUNT_KEY  - Storage account key
"""

import os
import sys
import argparse
from typing import Optional

# Add project root to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# Blob migration mappings: old_prefix -> new_prefix
MIGRATION_MAPPINGS = {
    # VinsDelux assets
    "producers/": "vinsdelux/producers/",
    "products/": "vinsdelux/products/",
    "categories/": "vinsdelux/categories/",
    "blog/": "vinsdelux/blog/",
    "adoption_plans/": "vinsdelux/adoption_plans/",

    # PowerUP / Crush Delegation assets
    "company_logos/": "powerup/delegation/companies/",
    "delegation_profiles/": "powerup/delegation/profiles/",

    # Shared assets
    "homepage/": "shared/homepage/",

    # Test files to clean up (optional - set to None to delete)
    "test_upload_from_django_command.txt": None,  # Delete test file
}

# Prefixes that are already correctly structured (skip these)
SKIP_PREFIXES = [
    "crush-lu/",
    "powerup/",
    "vinsdelux/",
    "shared/",
]


def get_blob_service_client(account_name: Optional[str] = None, use_azurite: bool = False):
    """Create a BlobServiceClient for Azure or Azurite."""
    from azure.storage.blob import BlobServiceClient

    if use_azurite:
        # Azurite local emulator
        azurite_account = "devstoreaccount1"
        azurite_key = (
            "Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq"
            "/K1SZFPTOtr/KBHBeksoGMGw=="
        )
        connection_string = (
            f"DefaultEndpointsProtocol=http;"
            f"AccountName={azurite_account};"
            f"AccountKey={azurite_key};"
            f"BlobEndpoint=http://127.0.0.1:10000/{azurite_account};"
        )
        return BlobServiceClient.from_connection_string(connection_string)

    # Production Azure
    account_name = account_name or os.getenv('AZURE_ACCOUNT_NAME')
    account_key = os.getenv('AZURE_ACCOUNT_KEY')

    if not account_name:
        raise ValueError("Storage account name required. Set AZURE_ACCOUNT_NAME or use --account-name")

    if account_key:
        # Use account key if available
        connection_string = (
            f"DefaultEndpointsProtocol=https;"
            f"AccountName={account_name};"
            f"AccountKey={account_key};"
            f"EndpointSuffix=core.windows.net"
        )
        return BlobServiceClient.from_connection_string(connection_string)
    else:
        # Use DefaultAzureCredential (Azure CLI login)
        from azure.identity import DefaultAzureCredential
        return BlobServiceClient(
            account_url=f"https://{account_name}.blob.core.windows.net",
            credential=DefaultAzureCredential()
        )


def should_skip_blob(blob_name: str) -> bool:
    """Check if blob is already in correct structure."""
    for prefix in SKIP_PREFIXES:
        if blob_name.startswith(prefix):
            return True
    return False


def get_new_blob_name(old_name: str) -> Optional[str]:
    """Get new blob name based on migration mappings."""
    for old_prefix, new_prefix in MIGRATION_MAPPINGS.items():
        if old_name.startswith(old_prefix):
            if new_prefix is None:
                return None  # Mark for deletion
            return new_prefix + old_name[len(old_prefix):]
    return None  # No mapping found


def migrate_blobs(
    account_name: Optional[str] = None,
    container_name: str = "media",
    dry_run: bool = True,
    use_azurite: bool = False,
    delete_old: bool = False,
):
    """Migrate blobs to new domain-based structure."""
    print("=" * 60)
    print("  Azure Blob Storage Migration")
    print("=" * 60)
    print()

    if dry_run:
        print("MODE: DRY RUN (no changes will be made)")
    else:
        print("MODE: EXECUTE (blobs will be copied/moved)")
    print()

    # Connect to storage
    try:
        client = get_blob_service_client(account_name, use_azurite)
        container = client.get_container_client(container_name)

        # Test connection
        if not container.exists():
            print(f"ERROR: Container '{container_name}' does not exist")
            return False

        print(f"Connected to container: {container_name}")
        print()

    except Exception as e:
        print(f"ERROR: Could not connect to storage: {e}")
        return False

    # Collect blobs to migrate
    to_migrate = []
    to_delete = []
    skipped = []

    print("Scanning blobs...")
    for blob in container.list_blobs():
        blob_name = blob.name

        if should_skip_blob(blob_name):
            skipped.append(blob_name)
            continue

        new_name = get_new_blob_name(blob_name)
        if new_name is None and blob_name in MIGRATION_MAPPINGS:
            # Marked for deletion
            to_delete.append(blob_name)
        elif new_name:
            to_migrate.append((blob_name, new_name))

    # Report findings
    print()
    print(f"Found {len(to_migrate)} blobs to migrate")
    print(f"Found {len(to_delete)} blobs to delete")
    print(f"Skipping {len(skipped)} already-organized blobs")
    print()

    if not to_migrate and not to_delete:
        print("Nothing to do! All blobs are already organized.")
        return True

    # Show migration plan
    if to_migrate:
        print("Migration plan:")
        print("-" * 60)
        for old_name, new_name in to_migrate:
            print(f"  {old_name}")
            print(f"    -> {new_name}")
        print()

    if to_delete:
        print("Blobs to delete:")
        print("-" * 60)
        for name in to_delete:
            print(f"  {name}")
        print()

    if dry_run:
        print("DRY RUN complete. Run without --dry-run to execute.")
        return True

    # Execute migration
    print("Executing migration...")
    print()

    migrated = 0
    deleted = 0
    errors = 0

    for old_name, new_name in to_migrate:
        try:
            # Get source and destination blob clients
            source_blob = container.get_blob_client(old_name)
            dest_blob = container.get_blob_client(new_name)

            # Check if destination already exists
            if dest_blob.exists():
                print(f"  SKIP (exists): {new_name}")
                continue

            # Copy blob to new location
            dest_blob.start_copy_from_url(source_blob.url)

            # Wait for copy to complete
            props = dest_blob.get_blob_properties()
            while props.copy.status == 'pending':
                import time
                time.sleep(0.5)
                props = dest_blob.get_blob_properties()

            if props.copy.status == 'success':
                print(f"  COPIED: {old_name} -> {new_name}")
                migrated += 1

                # Delete old blob if requested
                if delete_old:
                    source_blob.delete_blob()
                    print(f"  DELETED: {old_name}")
            else:
                print(f"  ERROR copying {old_name}: {props.copy.status}")
                errors += 1

        except Exception as e:
            print(f"  ERROR: {old_name}: {e}")
            errors += 1

    for name in to_delete:
        try:
            blob = container.get_blob_client(name)
            blob.delete_blob()
            print(f"  DELETED: {name}")
            deleted += 1
        except Exception as e:
            print(f"  ERROR deleting {name}: {e}")
            errors += 1

    # Summary
    print()
    print("=" * 60)
    print("  Migration Summary")
    print("=" * 60)
    print(f"  Blobs copied:  {migrated}")
    print(f"  Blobs deleted: {deleted}")
    print(f"  Errors:        {errors}")
    print()

    if not delete_old and migrated > 0:
        print("NOTE: Old blobs were NOT deleted. Run with --delete-old to remove them.")
        print()

    return errors == 0


def main():
    parser = argparse.ArgumentParser(
        description="Migrate Azure Blob Storage to domain-based structure"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without executing"
    )
    parser.add_argument(
        "--account-name",
        type=str,
        help="Azure Storage account name (or set AZURE_ACCOUNT_NAME)"
    )
    parser.add_argument(
        "--container",
        type=str,
        default="media",
        help="Container name (default: media)"
    )
    parser.add_argument(
        "--azurite",
        action="store_true",
        help="Use local Azurite emulator instead of Azure"
    )
    parser.add_argument(
        "--delete-old",
        action="store_true",
        help="Delete old blobs after successful copy"
    )

    args = parser.parse_args()

    success = migrate_blobs(
        account_name=args.account_name,
        container_name=args.container,
        dry_run=args.dry_run,
        use_azurite=args.azurite,
        delete_old=args.delete_old,
    )

    return 0 if success else 1


if __name__ == "__main__":
    exit(main())