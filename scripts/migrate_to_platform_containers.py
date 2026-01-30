"""
Migrate blobs from legacy 'media' container to platform-specific containers

This script copies blobs from the single shared 'media' container to
platform-specific containers while maintaining backward compatibility.

Usage:
    python scripts/migrate_to_platform_containers.py [options]

Options:
    --dry-run          - Preview what would be migrated without copying
    --platform <name>  - Migrate only specified platform (crush-lu, vinsdelux, etc.)
    --verify           - Verify integrity after migration
    --delete-source    - Delete source blobs after successful copy (DANGER!)

Environment Variables Required:
    AZURE_ACCOUNT_NAME
    AZURE_ACCOUNT_KEY

Container Mapping:
    crush-lu/*    -> crush-lu-media/
    vinsdelux/*   -> vinsdelux-media/
    powerup/*     -> powerup-media/
    shared/*      -> shared-media/
    users/*       -> crush-lu-private/ (if applicable)

Note: This script DOES NOT modify the legacy 'media' container by default.
      Use --delete-source to remove source blobs after successful migration.
"""

import os
import sys
import argparse
import logging
from collections import defaultdict
from datetime import datetime

# Add project root to Python path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Note: This script works standalone without Django
# It only requires AZURE_ACCOUNT_NAME and AZURE_ACCOUNT_KEY environment variables

try:
    from azure.storage.blob import BlobServiceClient, BlobClient
    from azure.core.exceptions import ResourceNotFoundError
except ImportError:
    print("ERROR: azure-storage-blob package not installed")
    print("Run: pip install azure-storage-blob")
    sys.exit(1)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(f'migration_{datetime.now():%Y%m%d_%H%M%S}.log')
    ]
)
logger = logging.getLogger(__name__)


# Platform prefix mapping
CONTAINER_MAPPING = {
    'crush-lu': 'crush-lu-media',
    'vinsdelux': 'vinsdelux-media',
    'powerup': 'powerup-media',
    'shared': 'shared-media',
    'users': 'crush-lu-private',  # User-specific content goes to private container
    'journey_gifts': 'crush-lu-media',  # Journey gift QR codes (public, anonymous access)
}


def get_blob_service_client():
    """Create BlobServiceClient from environment variables."""
    account_name = os.getenv('AZURE_ACCOUNT_NAME')
    account_key = os.getenv('AZURE_ACCOUNT_KEY')

    if not account_name or not account_key:
        logger.error("AZURE_ACCOUNT_NAME and AZURE_ACCOUNT_KEY must be set")
        sys.exit(1)

    account_url = f"https://{account_name}.blob.core.windows.net"
    return BlobServiceClient(account_url=account_url, credential=account_key)


def analyze_blobs(blob_service_client, source_container='media'):
    """
    Analyze blobs in source container and group by target container.

    Returns:
        dict: {target_container: [blob_names]}
    """
    logger.info(f"Analyzing blobs in '{source_container}' container...")

    container_client = blob_service_client.get_container_client(source_container)
    blob_groups = defaultdict(list)
    unknown_blobs = []

    try:
        blob_list = container_client.list_blobs()
        total_count = 0

        for blob in blob_list:
            total_count += 1
            blob_name = blob.name

            # Determine target container based on prefix
            prefix = blob_name.split('/')[0] if '/' in blob_name else None

            if prefix in CONTAINER_MAPPING:
                target_container = CONTAINER_MAPPING[prefix]
                # Remove prefix from target blob name (crush-lu/branding/logo.png -> branding/logo.png)
                target_blob_name = blob_name[len(prefix) + 1:] if '/' in blob_name else blob_name
                blob_groups[target_container].append({
                    'source_name': blob_name,
                    'target_name': target_blob_name,
                    'size': blob.size,
                    'content_type': blob.content_settings.content_type if blob.content_settings else None,
                })
            else:
                unknown_blobs.append(blob_name)

        logger.info(f"Total blobs analyzed: {total_count}")
        logger.info(f"Blobs to migrate: {total_count - len(unknown_blobs)}")
        logger.info(f"Unknown/unmapped blobs: {len(unknown_blobs)}")

        # Print summary by target container
        for target_container, blobs in blob_groups.items():
            total_size = sum(b['size'] for b in blobs)
            logger.info(f"  {target_container}: {len(blobs)} blobs ({total_size / 1024 / 1024:.2f} MB)")

        if unknown_blobs:
            logger.warning(f"Unknown blobs (not migrated):")
            for blob_name in unknown_blobs[:10]:  # Show first 10
                logger.warning(f"  - {blob_name}")
            if len(unknown_blobs) > 10:
                logger.warning(f"  ... and {len(unknown_blobs) - 10} more")

        return blob_groups, unknown_blobs

    except ResourceNotFoundError:
        logger.error(f"Container '{source_container}' does not exist")
        sys.exit(1)


def create_target_containers(blob_service_client, target_containers):
    """
    Create target containers if they don't exist.

    Args:
        blob_service_client: BlobServiceClient instance
        target_containers: List of container names to create
    """
    for container_name in target_containers:
        try:
            container_client = blob_service_client.get_container_client(container_name)
            if not container_client.exists():
                # Determine public access level based on container type
                public_access = 'blob' if 'private' not in container_name else None
                container_client.create_container(public_access=public_access)
                logger.info(f"Created container: {container_name} (public_access={public_access})")
            else:
                logger.info(f"Container already exists: {container_name}")
        except Exception as e:
            logger.error(f"Failed to create container {container_name}: {e}")
            raise


def copy_blobs(blob_service_client, blob_groups, source_container='media', dry_run=False):
    """
    Copy blobs from source container to target containers.

    Args:
        blob_service_client: BlobServiceClient instance
        blob_groups: dict of {target_container: [blob_info]}
        source_container: Source container name
        dry_run: If True, only log what would be done without copying

    Returns:
        dict: Migration statistics
    """
    stats = {
        'success': 0,
        'failed': 0,
        'skipped': 0,
        'errors': []
    }

    source_container_client = blob_service_client.get_container_client(source_container)

    for target_container, blobs in blob_groups.items():
        logger.info(f"\nMigrating to {target_container}: {len(blobs)} blobs")
        target_container_client = blob_service_client.get_container_client(target_container)

        for blob_info in blobs:
            source_name = blob_info['source_name']
            target_name = blob_info['target_name']

            try:
                # Check if target blob already exists
                target_blob_client = target_container_client.get_blob_client(target_name)
                if target_blob_client.exists():
                    logger.debug(f"Skipped (exists): {target_name}")
                    stats['skipped'] += 1
                    continue

                if dry_run:
                    logger.info(f"[DRY-RUN] Would copy: {source_container}/{source_name} -> {target_container}/{target_name}")
                    stats['success'] += 1
                    continue

                # Copy blob
                source_blob_url = source_container_client.get_blob_client(source_name).url
                target_blob_client.start_copy_from_url(source_blob_url)

                # Wait for copy to complete (for small blobs this is instant)
                props = target_blob_client.get_blob_properties()
                if props.copy.status == 'success':
                    logger.debug(f"Copied: {target_name}")
                    stats['success'] += 1
                else:
                    logger.warning(f"Copy status: {props.copy.status} for {target_name}")
                    stats['failed'] += 1

            except Exception as e:
                logger.error(f"Failed to copy {source_name}: {e}")
                stats['failed'] += 1
                stats['errors'].append({
                    'blob': source_name,
                    'error': str(e)
                })

    return stats


def verify_migration(blob_service_client, blob_groups, source_container='media'):
    """
    Verify that all blobs were successfully migrated.

    Args:
        blob_service_client: BlobServiceClient instance
        blob_groups: dict of {target_container: [blob_info]}
        source_container: Source container name

    Returns:
        bool: True if all blobs verified successfully
    """
    logger.info("\nVerifying migration integrity...")

    source_container_client = blob_service_client.get_container_client(source_container)
    all_verified = True
    verification_errors = []

    for target_container, blobs in blob_groups.items():
        target_container_client = blob_service_client.get_container_client(target_container)

        for blob_info in blobs:
            source_name = blob_info['source_name']
            target_name = blob_info['target_name']

            try:
                # Get source and target blob properties
                source_blob = source_container_client.get_blob_client(source_name)
                target_blob = target_container_client.get_blob_client(target_name)

                source_props = source_blob.get_blob_properties()
                target_props = target_blob.get_blob_properties()

                # Compare sizes
                if source_props.size != target_props.size:
                    logger.error(f"Size mismatch: {source_name} ({source_props.size} != {target_props.size})")
                    all_verified = False
                    verification_errors.append({
                        'blob': source_name,
                        'issue': 'size_mismatch'
                    })

                # Compare content types
                source_ct = source_props.content_settings.content_type if source_props.content_settings else None
                target_ct = target_props.content_settings.content_type if target_props.content_settings else None
                if source_ct != target_ct:
                    logger.warning(f"Content-type mismatch: {source_name} ({source_ct} != {target_ct})")

            except ResourceNotFoundError as e:
                logger.error(f"Blob not found during verification: {target_name}")
                all_verified = False
                verification_errors.append({
                    'blob': target_name,
                    'issue': 'not_found'
                })
            except Exception as e:
                logger.error(f"Verification error for {source_name}: {e}")
                all_verified = False
                verification_errors.append({
                    'blob': source_name,
                    'issue': str(e)
                })

    if all_verified:
        logger.info("✓ All blobs verified successfully")
    else:
        logger.error(f"✗ Verification failed for {len(verification_errors)} blobs")

    return all_verified


def delete_source_blobs(blob_service_client, blob_groups, source_container='media', dry_run=False):
    """
    Delete source blobs after successful migration (DANGER!).

    Args:
        blob_service_client: BlobServiceClient instance
        blob_groups: dict of {target_container: [blob_info]}
        source_container: Source container name
        dry_run: If True, only log what would be deleted

    Returns:
        dict: Deletion statistics
    """
    logger.warning("\n⚠️  DELETING SOURCE BLOBS - THIS CANNOT BE UNDONE!")

    if not dry_run:
        confirm = input("Type 'DELETE' to confirm deletion: ")
        if confirm != 'DELETE':
            logger.info("Deletion cancelled")
            return {'deleted': 0, 'failed': 0}

    stats = {'deleted': 0, 'failed': 0}
    source_container_client = blob_service_client.get_container_client(source_container)

    for target_container, blobs in blob_groups.items():
        for blob_info in blobs:
            source_name = blob_info['source_name']

            try:
                if dry_run:
                    logger.info(f"[DRY-RUN] Would delete: {source_container}/{source_name}")
                    stats['deleted'] += 1
                else:
                    source_container_client.delete_blob(source_name)
                    logger.debug(f"Deleted: {source_name}")
                    stats['deleted'] += 1

            except Exception as e:
                logger.error(f"Failed to delete {source_name}: {e}")
                stats['failed'] += 1

    return stats


def main():
    parser = argparse.ArgumentParser(
        description='Migrate blobs to platform-specific containers',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument('--dry-run', action='store_true',
                        help='Preview migration without copying')
    parser.add_argument('--platform', choices=['crush-lu', 'vinsdelux', 'powerup', 'shared', 'users'],
                        help='Migrate only specified platform')
    parser.add_argument('--verify', action='store_true',
                        help='Verify integrity after migration')
    parser.add_argument('--delete-source', action='store_true',
                        help='Delete source blobs after migration (DANGER!)')
    parser.add_argument('--source-container', default='media',
                        help='Source container name (default: media)')

    args = parser.parse_args()

    logger.info("="*80)
    logger.info("Azure Blob Storage Migration to Platform-Specific Containers")
    logger.info("="*80)

    if args.dry_run:
        logger.info("DRY-RUN MODE: No changes will be made")

    # Initialize Azure Blob Service Client
    blob_service_client = get_blob_service_client()

    # Step 1: Analyze blobs
    blob_groups, unknown_blobs = analyze_blobs(blob_service_client, args.source_container)

    if not blob_groups:
        logger.info("No blobs to migrate")
        return

    # Filter by platform if specified
    if args.platform:
        target_container = CONTAINER_MAPPING.get(args.platform)
        if target_container and target_container in blob_groups:
            blob_groups = {target_container: blob_groups[target_container]}
            logger.info(f"Filtered to platform: {args.platform} -> {target_container}")
        else:
            logger.error(f"No blobs found for platform: {args.platform}")
            return

    # Step 2: Create target containers
    if not args.dry_run:
        create_target_containers(blob_service_client, blob_groups.keys())

    # Step 3: Copy blobs
    stats = copy_blobs(blob_service_client, blob_groups, args.source_container, args.dry_run)

    # Print migration statistics
    logger.info("\n" + "="*80)
    logger.info("Migration Statistics")
    logger.info("="*80)
    logger.info(f"Successful: {stats['success']}")
    logger.info(f"Failed: {stats['failed']}")
    logger.info(f"Skipped (already exists): {stats['skipped']}")

    if stats['errors']:
        logger.error(f"\nErrors ({len(stats['errors'])}):")
        for error in stats['errors'][:10]:  # Show first 10
            logger.error(f"  {error['blob']}: {error['error']}")
        if len(stats['errors']) > 10:
            logger.error(f"  ... and {len(stats['errors']) - 10} more errors")

    # Step 4: Verify migration (if requested)
    if args.verify and not args.dry_run:
        verify_migration(blob_service_client, blob_groups, args.source_container)

    # Step 5: Delete source blobs (if requested)
    if args.delete_source:
        delete_stats = delete_source_blobs(blob_service_client, blob_groups, args.source_container, args.dry_run)
        logger.info(f"\nDeletion Statistics:")
        logger.info(f"  Deleted: {delete_stats['deleted']}")
        logger.info(f"  Failed: {delete_stats['failed']}")

    logger.info("\n✓ Migration complete")


if __name__ == '__main__':
    main()
