"""
Verify blob migration integrity

This script verifies that all blobs were successfully migrated from the legacy
'media' container to platform-specific containers by comparing blob counts,
sizes, and content types.

Usage:
    python scripts/verify_blob_migration.py [options]

Options:
    --source-container <name> - Source container name (default: media)
    --detailed                - Show detailed comparison per blob
    --export-csv <file>       - Export verification results to CSV

Environment Variables Required:
    AZURE_ACCOUNT_NAME
    AZURE_ACCOUNT_KEY
"""

import os
import sys
import argparse
import logging
import csv
from collections import defaultdict
from datetime import datetime

# Add project root to Python path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Setup Django environment
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'azureproject.production')
import django
django.setup()

try:
    from azure.storage.blob import BlobServiceClient
    from azure.core.exceptions import ResourceNotFoundError
except ImportError:
    print("ERROR: azure-storage-blob package not installed")
    print("Run: pip install azure-storage-blob")
    sys.exit(1)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Platform prefix mapping (must match migration script)
CONTAINER_MAPPING = {
    'crush-lu': 'crush-lu-media',
    'vinsdelux': 'vinsdelux-media',
    'powerup': 'powerup-media',
    'shared': 'shared-media',
    'users': 'crush-lu-private',
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


def get_container_blobs(blob_service_client, container_name):
    """
    Get all blobs in a container with their properties.

    Returns:
        dict: {blob_name: {size, content_type, last_modified}}
    """
    try:
        container_client = blob_service_client.get_container_client(container_name)
        blobs = {}

        for blob in container_client.list_blobs():
            blobs[blob.name] = {
                'size': blob.size,
                'content_type': blob.content_settings.content_type if blob.content_settings else None,
                'last_modified': blob.last_modified,
            }

        return blobs

    except ResourceNotFoundError:
        logger.warning(f"Container '{container_name}' does not exist")
        return {}


def verify_migration(blob_service_client, source_container, detailed=False):
    """
    Verify migration by comparing source and target containers.

    Args:
        blob_service_client: BlobServiceClient instance
        source_container: Source container name
        detailed: Show detailed per-blob comparison

    Returns:
        dict: Verification statistics and details
    """
    logger.info(f"Verifying migration from '{source_container}'...")

    # Get source blobs
    source_blobs = get_container_blobs(blob_service_client, source_container)
    if not source_blobs:
        logger.error(f"No blobs found in source container '{source_container}'")
        return None

    logger.info(f"Found {len(source_blobs)} blobs in source container")

    # Group source blobs by target container
    expected_blobs = defaultdict(dict)
    for source_name, props in source_blobs.items():
        prefix = source_name.split('/')[0] if '/' in source_name else None

        if prefix in CONTAINER_MAPPING:
            target_container = CONTAINER_MAPPING[prefix]
            target_name = source_name[len(prefix) + 1:] if '/' in source_name else source_name
            expected_blobs[target_container][target_name] = props

    # Verify each target container
    verification_results = {
        'total_expected': len(source_blobs),
        'total_verified': 0,
        'missing_blobs': [],
        'size_mismatches': [],
        'content_type_mismatches': [],
        'containers': {}
    }

    for target_container, expected in expected_blobs.items():
        logger.info(f"\nVerifying {target_container}: {len(expected)} expected blobs")

        # Get actual blobs in target container
        actual_blobs = get_container_blobs(blob_service_client, target_container)

        container_stats = {
            'expected': len(expected),
            'found': 0,
            'missing': 0,
            'size_mismatches': 0,
            'content_type_mismatches': 0,
        }

        for target_name, expected_props in expected.items():
            if target_name not in actual_blobs:
                logger.error(f"  MISSING: {target_name}")
                verification_results['missing_blobs'].append({
                    'container': target_container,
                    'blob': target_name
                })
                container_stats['missing'] += 1
                continue

            actual_props = actual_blobs[target_name]
            container_stats['found'] += 1
            verification_results['total_verified'] += 1

            # Check size
            if expected_props['size'] != actual_props['size']:
                logger.error(f"  SIZE MISMATCH: {target_name}")
                logger.error(f"    Expected: {expected_props['size']} bytes")
                logger.error(f"    Actual: {actual_props['size']} bytes")
                verification_results['size_mismatches'].append({
                    'container': target_container,
                    'blob': target_name,
                    'expected_size': expected_props['size'],
                    'actual_size': actual_props['size']
                })
                container_stats['size_mismatches'] += 1

            # Check content type
            if expected_props['content_type'] != actual_props['content_type']:
                if detailed:
                    logger.warning(f"  CONTENT-TYPE MISMATCH: {target_name}")
                    logger.warning(f"    Expected: {expected_props['content_type']}")
                    logger.warning(f"    Actual: {actual_props['content_type']}")
                verification_results['content_type_mismatches'].append({
                    'container': target_container,
                    'blob': target_name,
                    'expected_type': expected_props['content_type'],
                    'actual_type': actual_props['content_type']
                })
                container_stats['content_type_mismatches'] += 1

        verification_results['containers'][target_container] = container_stats

        # Print container summary
        logger.info(f"  Found: {container_stats['found']}/{container_stats['expected']}")
        if container_stats['missing'] > 0:
            logger.error(f"  Missing: {container_stats['missing']}")
        if container_stats['size_mismatches'] > 0:
            logger.error(f"  Size mismatches: {container_stats['size_mismatches']}")
        if container_stats['content_type_mismatches'] > 0:
            logger.warning(f"  Content-type mismatches: {container_stats['content_type_mismatches']}")

    return verification_results


def export_to_csv(verification_results, output_file):
    """Export verification results to CSV file."""
    logger.info(f"\nExporting results to {output_file}...")

    with open(output_file, 'w', newline='', encoding='utf-8') as csvfile:
        fieldnames = ['issue_type', 'container', 'blob', 'details']
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()

        # Write missing blobs
        for item in verification_results['missing_blobs']:
            writer.writerow({
                'issue_type': 'MISSING',
                'container': item['container'],
                'blob': item['blob'],
                'details': ''
            })

        # Write size mismatches
        for item in verification_results['size_mismatches']:
            writer.writerow({
                'issue_type': 'SIZE_MISMATCH',
                'container': item['container'],
                'blob': item['blob'],
                'details': f"Expected: {item['expected_size']}, Actual: {item['actual_size']}"
            })

        # Write content-type mismatches
        for item in verification_results['content_type_mismatches']:
            writer.writerow({
                'issue_type': 'CONTENT_TYPE_MISMATCH',
                'container': item['container'],
                'blob': item['blob'],
                'details': f"Expected: {item['expected_type']}, Actual: {item['actual_type']}"
            })

    logger.info(f"✓ Exported to {output_file}")


def main():
    parser = argparse.ArgumentParser(
        description='Verify blob migration integrity',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument('--source-container', default='media',
                        help='Source container name (default: media)')
    parser.add_argument('--detailed', action='store_true',
                        help='Show detailed comparison per blob')
    parser.add_argument('--export-csv', metavar='FILE',
                        help='Export verification results to CSV')

    args = parser.parse_args()

    logger.info("="*80)
    logger.info("Azure Blob Storage Migration Verification")
    logger.info("="*80)

    # Initialize Azure Blob Service Client
    blob_service_client = get_blob_service_client()

    # Verify migration
    verification_results = verify_migration(
        blob_service_client,
        args.source_container,
        args.detailed
    )

    if not verification_results:
        sys.exit(1)

    # Print summary
    logger.info("\n" + "="*80)
    logger.info("Verification Summary")
    logger.info("="*80)
    logger.info(f"Total blobs expected: {verification_results['total_expected']}")
    logger.info(f"Total blobs verified: {verification_results['total_verified']}")
    logger.info(f"Missing blobs: {len(verification_results['missing_blobs'])}")
    logger.info(f"Size mismatches: {len(verification_results['size_mismatches'])}")
    logger.info(f"Content-type mismatches: {len(verification_results['content_type_mismatches'])}")

    # Export to CSV if requested
    if args.export_csv:
        export_to_csv(verification_results, args.export_csv)

    # Exit with error if critical issues found
    if verification_results['missing_blobs'] or verification_results['size_mismatches']:
        logger.error("\n✗ Verification FAILED - Critical issues found")
        sys.exit(1)
    elif verification_results['content_type_mismatches']:
        logger.warning("\n⚠ Verification PASSED with warnings (content-type mismatches)")
        sys.exit(0)
    else:
        logger.info("\n✓ Verification PASSED - All blobs migrated successfully")
        sys.exit(0)


if __name__ == '__main__':
    main()
