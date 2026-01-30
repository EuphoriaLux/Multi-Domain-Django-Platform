"""
Migrate Crush.lu private photos from 'crush-profiles-private' to 'crush-lu-private'

This script copies all user photos, coach photos, and journey gifts from the legacy
container name to the new naming convention.

Usage:
    python scripts/migrate_crush_private_container.py [options]

Options:
    --dry-run          - Preview what would be migrated without copying
    --verify           - Verify integrity after migration
    --delete-source    - Delete source blobs after successful copy (DANGER!)

Environment Variables Required:
    AZURE_ACCOUNT_NAME
    AZURE_ACCOUNT_KEY

Container Mapping:
    crush-profiles-private -> crush-lu-private

Structure Copied:
    users/{user_id}/photos/         -> users/{user_id}/photos/
    users/{user_id}/.user_created   -> users/{user_id}/.user_created
    users/{user_id}/journey_gifts/  -> users/{user_id}/journey_gifts/
    coaches/{user_id}/              -> coaches/{user_id}/
"""

import os
import sys
import argparse
import logging
from collections import defaultdict
from datetime import datetime

# Add project root to Python path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from azure.storage.blob import BlobServiceClient, BlobClient
    from azure.core.exceptions import ResourceNotFoundError, ResourceExistsError
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
        logging.FileHandler(f'crush_private_migration_{datetime.now():%Y%m%d_%H%M%S}.log')
    ]
)
logger = logging.getLogger(__name__)


SOURCE_CONTAINER = 'crush-profiles-private'
TARGET_CONTAINER = 'crush-lu-private'


def get_blob_service_client():
    """Create BlobServiceClient from environment variables."""
    account_name = os.getenv('AZURE_ACCOUNT_NAME')
    account_key = os.getenv('AZURE_ACCOUNT_KEY')

    if not account_name or not account_key:
        logger.error("AZURE_ACCOUNT_NAME and AZURE_ACCOUNT_KEY must be set")
        sys.exit(1)

    account_url = f"https://{account_name}.blob.core.windows.net"
    return BlobServiceClient(account_url=account_url, credential=account_key)


def analyze_blobs(blob_service_client, source_container):
    """
    Analyze blobs in source container.

    Returns:
        dict: {category: [blob_info]}
    """
    logger.info(f"Analyzing blobs in '{source_container}' container...")

    container_client = blob_service_client.get_container_client(source_container)
    blob_groups = defaultdict(list)

    try:
        blob_list = container_client.list_blobs()
        total_count = 0
        total_size = 0

        for blob in blob_list:
            total_count += 1
            total_size += blob.size

            # Categorize by path
            if blob.name.startswith('users/'):
                if '/photos/' in blob.name:
                    category = 'user_photos'
                elif '/journey_gifts/' in blob.name:
                    category = 'journey_gifts'
                elif '.user_created' in blob.name:
                    category = 'user_markers'
                else:
                    category = 'user_other'
            elif blob.name.startswith('coaches/'):
                category = 'coach_photos'
            else:
                category = 'unknown'

            blob_groups[category].append({
                'name': blob.name,
                'size': blob.size,
                'content_type': blob.content_settings.content_type if blob.content_settings else None,
            })

        logger.info(f"Total blobs analyzed: {total_count}")
        logger.info(f"Total size: {total_size / 1024 / 1024:.2f} MB")
        logger.info("")

        # Print summary by category
        for category, blobs in sorted(blob_groups.items()):
            total_size = sum(b['size'] for b in blobs)
            logger.info(f"  {category}: {len(blobs)} blobs ({total_size / 1024 / 1024:.2f} MB)")

        return blob_groups, total_count

    except ResourceNotFoundError:
        logger.error(f"Container '{source_container}' does not exist")
        sys.exit(1)


def create_target_container(blob_service_client, target_container):
    """
    Create target container if it doesn't exist.

    Args:
        blob_service_client: BlobServiceClient instance
        target_container: Target container name
    """
    try:
        container_client = blob_service_client.get_container_client(target_container)
        if not container_client.exists():
            # Private access (no anonymous access)
            container_client.create_container(public_access=None)
            logger.info(f"Created container: {target_container} (private)")
        else:
            logger.info(f"Container already exists: {target_container}")
    except Exception as e:
        logger.error(f"Failed to create container {target_container}: {e}")
        raise


def copy_blobs(blob_service_client, blob_groups, source_container, target_container, dry_run=False):
    """
    Copy blobs from source container to target container.

    Args:
        blob_service_client: BlobServiceClient instance
        blob_groups: dict of {category: [blob_info]}
        source_container: Source container name
        target_container: Target container name
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
    target_container_client = blob_service_client.get_container_client(target_container)

    # Flatten all blobs from all categories
    all_blobs = []
    for category, blobs in blob_groups.items():
        all_blobs.extend(blobs)

    logger.info(f"\nMigrating {len(all_blobs)} blobs from {source_container} to {target_container}")

    for blob_info in all_blobs:
        blob_name = blob_info['name']

        try:
            # Check if target blob already exists
            target_blob_client = target_container_client.get_blob_client(blob_name)
            if target_blob_client.exists():
                logger.debug(f"Skipped (exists): {blob_name}")
                stats['skipped'] += 1
                continue

            if dry_run:
                logger.info(f"[DRY-RUN] Would copy: {blob_name}")
                stats['success'] += 1
                continue

            # Copy blob (server-side, fast)
            source_blob_url = source_container_client.get_blob_client(blob_name).url
            copy_result = target_blob_client.start_copy_from_url(source_blob_url)

            # Wait for copy to complete
            props = target_blob_client.get_blob_properties()
            if props.copy.status == 'success':
                logger.debug(f"Copied: {blob_name}")
                stats['success'] += 1
            else:
                logger.warning(f"Copy status: {props.copy.status} for {blob_name}")
                stats['failed'] += 1

        except Exception as e:
            logger.error(f"Failed to copy {blob_name}: {e}")
            stats['failed'] += 1
            stats['errors'].append({
                'blob': blob_name,
                'error': str(e)
            })

    return stats


def verify_migration(blob_service_client, source_container, target_container):
    """
    Verify that all blobs were successfully migrated.

    Args:
        blob_service_client: BlobServiceClient instance
        source_container: Source container name
        target_container: Target container name

    Returns:
        bool: True if all blobs verified successfully
    """
    logger.info("\nVerifying migration integrity...")

    source_container_client = blob_service_client.get_container_client(source_container)
    target_container_client = blob_service_client.get_container_client(target_container)

    all_verified = True
    verification_errors = []

    # Get all source blobs
    source_blobs = {blob.name: blob for blob in source_container_client.list_blobs()}

    for blob_name, source_blob in source_blobs.items():
        try:
            target_blob_client = target_container_client.get_blob_client(blob_name)
            target_props = target_blob_client.get_blob_properties()

            # Compare sizes
            if source_blob.size != target_props.size:
                logger.error(f"Size mismatch: {blob_name} ({source_blob.size} != {target_props.size})")
                all_verified = False
                verification_errors.append({'blob': blob_name, 'issue': 'size_mismatch'})

        except ResourceNotFoundError:
            logger.error(f"Blob not found in target: {blob_name}")
            all_verified = False
            verification_errors.append({'blob': blob_name, 'issue': 'not_found'})
        except Exception as e:
            logger.error(f"Verification error for {blob_name}: {e}")
            all_verified = False
            verification_errors.append({'blob': blob_name, 'issue': str(e)})

    if all_verified:
        logger.info("[OK] All blobs verified successfully")
    else:
        logger.error(f"[FAIL] Verification failed for {len(verification_errors)} blobs")

    return all_verified


def delete_source_blobs(blob_service_client, source_container, dry_run=False):
    """
    Delete source blobs after successful migration (DANGER!).

    Args:
        blob_service_client: BlobServiceClient instance
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

    for blob in source_container_client.list_blobs():
        try:
            if dry_run:
                logger.info(f"[DRY-RUN] Would delete: {blob.name}")
                stats['deleted'] += 1
            else:
                source_container_client.delete_blob(blob.name)
                logger.debug(f"Deleted: {blob.name}")
                stats['deleted'] += 1

        except Exception as e:
            logger.error(f"Failed to delete {blob.name}: {e}")
            stats['failed'] += 1

    return stats


def main():
    parser = argparse.ArgumentParser(
        description='Migrate Crush.lu private photos to new container',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument('--dry-run', action='store_true',
                        help='Preview migration without copying')
    parser.add_argument('--verify', action='store_true',
                        help='Verify integrity after migration')
    parser.add_argument('--delete-source', action='store_true',
                        help='Delete source blobs after migration (DANGER!)')

    args = parser.parse_args()

    logger.info("="*80)
    logger.info("Crush.lu Private Container Migration")
    logger.info(f"FROM: {SOURCE_CONTAINER} -> TO: {TARGET_CONTAINER}")
    logger.info("="*80)

    if args.dry_run:
        logger.info("DRY-RUN MODE: No changes will be made")

    # Initialize Azure Blob Service Client
    blob_service_client = get_blob_service_client()

    # Step 1: Analyze blobs
    blob_groups, total_count = analyze_blobs(blob_service_client, SOURCE_CONTAINER)

    if not blob_groups:
        logger.info("No blobs to migrate")
        return

    # Step 2: Create target container
    if not args.dry_run:
        create_target_container(blob_service_client, TARGET_CONTAINER)

    # Step 3: Copy blobs
    stats = copy_blobs(blob_service_client, blob_groups, SOURCE_CONTAINER, TARGET_CONTAINER, args.dry_run)

    # Print migration statistics
    logger.info("\n" + "="*80)
    logger.info("Migration Statistics")
    logger.info("="*80)
    logger.info(f"Successful: {stats['success']}")
    logger.info(f"Failed: {stats['failed']}")
    logger.info(f"Skipped (already exists): {stats['skipped']}")

    if stats['errors']:
        logger.error(f"\nErrors ({len(stats['errors'])}):")
        for error in stats['errors'][:10]:
            logger.error(f"  {error['blob']}: {error['error']}")
        if len(stats['errors']) > 10:
            logger.error(f"  ... and {len(stats['errors']) - 10} more errors")

    # Step 4: Verify migration (if requested)
    if args.verify and not args.dry_run:
        verify_migration(blob_service_client, SOURCE_CONTAINER, TARGET_CONTAINER)

    # Step 5: Delete source blobs (if requested)
    if args.delete_source:
        delete_stats = delete_source_blobs(blob_service_client, SOURCE_CONTAINER, args.dry_run)
        logger.info(f"\nDeletion Statistics:")
        logger.info(f"  Deleted: {delete_stats['deleted']}")
        logger.info(f"  Failed: {delete_stats['failed']}")

    logger.info("\nMigration complete")


if __name__ == '__main__':
    main()
