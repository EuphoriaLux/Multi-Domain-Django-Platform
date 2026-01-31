"""
Audit legacy media containers (media and media-staging)

Analyzes blob content to determine:
1. What files exist in legacy containers
2. Which platform they belong to (based on path patterns)
3. Whether they're still referenced in the database
4. Safe migration targets (which platform-specific container they should move to)

Usage:
    python scripts/audit_legacy_containers.py

Requirements:
    - Azure credentials configured (az login)
    - Environment variables: AZURE_ACCOUNT_NAME, AZURE_ACCOUNT_KEY
"""

import os
import sys
from collections import defaultdict
from datetime import datetime

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Load environment variables from .env if it exists
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Set Django settings before imports
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'azureproject.settings')

import django
django.setup()

from django.conf import settings

# Try to import Azure SDK
try:
    from azure.storage.blob import BlobServiceClient
    AZURE_AVAILABLE = True
except ImportError:
    AZURE_AVAILABLE = False
    print("ERROR: Azure Storage SDK not available. Install with: pip install azure-storage-blob")
    sys.exit(1)


class ContainerAuditor:
    """Audit legacy media containers"""

    # Path patterns to identify platform ownership
    PATH_PATTERNS = {
        'crush_lu': [
            'users/',           # User photos, GDPR exports, journey gifts
            'events/',          # Event photos
            'advent/',          # Advent calendar
            'journey/',         # Journey default images
            'coaches/',         # Coach photos
            'branding/',        # Crush.lu branding
        ],
        'vinsdelux': [
            'producers/',       # Producer logos
            'plots/',           # Plot images
            'coffrets/',        # Wine box photos
            'vineyard/',        # Vineyard images
            'wines/',           # Wine bottle photos
        ],
        'entreprinder': [
            'industries/',      # Industry icons
            'entreprinder/',    # Profile photos, events
        ],
        'power_up': [
            'delegation/',      # Delegation logos
            'powerup/',         # Company profiles
        ],
        'shared': [
            'legal/',           # Legal documents
            'icons/',           # Shared icons
            'shared/',          # Cross-platform assets
        ],
    }

    def __init__(self):
        self.account_name = os.getenv('AZURE_ACCOUNT_NAME')
        self.account_key = os.getenv('AZURE_ACCOUNT_KEY')

        if not self.account_name:
            raise ValueError("AZURE_ACCOUNT_NAME environment variable not set")

        # Try to connect (will work with either account key or Azure CLI auth)
        connection_string = None
        if self.account_key:
            connection_string = (
                f"DefaultEndpointsProtocol=https;"
                f"AccountName={self.account_name};"
                f"AccountKey={self.account_key};"
                f"EndpointSuffix=core.windows.net"
            )
            self.blob_service = BlobServiceClient.from_connection_string(connection_string)
        else:
            # Try using Azure CLI credentials
            from azure.identity import DefaultAzureCredential
            account_url = f"https://{self.account_name}.blob.core.windows.net"
            credential = DefaultAzureCredential()
            self.blob_service = BlobServiceClient(account_url, credential=credential)

        self.results = {
            'media': {'blobs': [], 'stats': defaultdict(int), 'by_platform': defaultdict(list)},
            'media-staging': {'blobs': [], 'stats': defaultdict(int), 'by_platform': defaultdict(list)},
        }

    def identify_platform(self, blob_name):
        """Identify which platform a blob belongs to based on path"""
        for platform, patterns in self.PATH_PATTERNS.items():
            for pattern in patterns:
                if blob_name.startswith(pattern):
                    return platform
        return 'unknown'

    def get_target_container(self, platform):
        """Get the target platform-specific container for a blob"""
        mapping = {
            'crush_lu': 'crush-lu-media or crush-lu-private',
            'vinsdelux': 'vinsdelux-media',
            'entreprinder': 'entreprinder-media',
            'power_up': 'powerup-media',
            'shared': 'shared-media',
            'unknown': 'NEEDS_MANUAL_REVIEW',
        }
        return mapping.get(platform, 'NEEDS_MANUAL_REVIEW')

    def audit_container(self, container_name):
        """Audit a single container"""
        print(f"\n{'='*80}")
        print(f"Auditing container: {container_name}")
        print(f"{'='*80}\n")

        try:
            container_client = self.blob_service.get_container_client(container_name)

            # Check if container exists
            if not container_client.exists():
                print(f"⚠️  Container '{container_name}' does not exist")
                return

            # List all blobs
            blob_list = list(container_client.list_blobs())
            total_blobs = len(blob_list)
            total_size = 0

            print(f"Found {total_blobs} blobs in container '{container_name}'")

            if total_blobs == 0:
                print(f"✅ Container '{container_name}' is EMPTY - Safe to delete!")
                return

            print("\nAnalyzing blobs...\n")

            # Analyze each blob
            for blob in blob_list:
                platform = self.identify_platform(blob.name)
                target = self.get_target_container(platform)
                size_mb = blob.size / (1024 * 1024)

                blob_info = {
                    'name': blob.name,
                    'size': blob.size,
                    'size_mb': size_mb,
                    'platform': platform,
                    'target_container': target,
                    'last_modified': blob.last_modified,
                    'content_type': blob.content_settings.content_type if blob.content_settings else 'unknown',
                }

                self.results[container_name]['blobs'].append(blob_info)
                self.results[container_name]['stats']['total_blobs'] += 1
                self.results[container_name]['stats']['total_size'] += blob.size
                self.results[container_name]['stats'][f'platform_{platform}'] += 1
                self.results[container_name]['by_platform'][platform].append(blob_info)

                total_size += blob.size

            # Print summary
            self.print_container_summary(container_name)

        except Exception as e:
            print(f"❌ Error auditing container '{container_name}': {e}")
            import traceback
            traceback.print_exc()

    def print_container_summary(self, container_name):
        """Print summary for a container"""
        data = self.results[container_name]
        stats = data['stats']

        print(f"\n{'─'*80}")
        print(f"SUMMARY: {container_name}")
        print(f"{'─'*80}")

        total_size_mb = stats['total_size'] / (1024 * 1024)
        total_size_gb = total_size_mb / 1024

        print(f"\n📊 Overall Statistics:")
        print(f"   Total blobs: {stats['total_blobs']}")
        print(f"   Total size: {total_size_mb:.2f} MB ({total_size_gb:.2f} GB)")

        print(f"\n🏢 By Platform:")
        for platform in sorted(data['by_platform'].keys()):
            count = len(data['by_platform'][platform])
            size = sum(b['size'] for b in data['by_platform'][platform])
            size_mb = size / (1024 * 1024)
            target = self.get_target_container(platform)

            print(f"   {platform:15s}: {count:4d} blobs ({size_mb:8.2f} MB) → {target}")

        # Show sample files per platform
        print(f"\n📁 Sample Files (first 5 per platform):")
        for platform in sorted(data['by_platform'].keys()):
            blobs = data['by_platform'][platform][:5]
            if blobs:
                print(f"\n   {platform}:")
                for blob in blobs:
                    print(f"      - {blob['name']} ({blob['size_mb']:.2f} MB)")

        # Highlight issues
        unknown_blobs = data['by_platform'].get('unknown', [])
        if unknown_blobs:
            print(f"\n⚠️  WARNING: {len(unknown_blobs)} blobs with unknown platform!")
            print(f"   These need manual review before migration:")
            for blob in unknown_blobs[:10]:  # Show first 10
                print(f"      - {blob['name']}")
            if len(unknown_blobs) > 10:
                print(f"      ... and {len(unknown_blobs) - 10} more")

    def generate_report(self):
        """Generate final audit report"""
        print(f"\n\n{'='*80}")
        print(f"FINAL AUDIT REPORT")
        print(f"{'='*80}\n")

        print(f"🗓️  Audit Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

        # Overall summary
        total_blobs = sum(r['stats']['total_blobs'] for r in self.results.values())
        total_size = sum(r['stats']['total_size'] for r in self.results.values())
        total_size_mb = total_size / (1024 * 1024)
        total_size_gb = total_size_mb / 1024

        print(f"📦 Containers Audited: 2 (media, media-staging)")
        print(f"📊 Total Blobs: {total_blobs}")
        print(f"💾 Total Size: {total_size_mb:.2f} MB ({total_size_gb:.2f} GB)\n")

        # Check if safe to delete
        media_blobs = self.results['media']['stats']['total_blobs']
        staging_blobs = self.results['media-staging']['stats']['total_blobs']

        print(f"\n{'─'*80}")
        print(f"DELETION SAFETY ASSESSMENT")
        print(f"{'─'*80}\n")

        if media_blobs == 0 and staging_blobs == 0:
            print("✅ SAFE TO DELETE: Both containers are empty!")
            print("\nRun these commands:")
            print(f"   az storage container delete --name media --account-name {self.account_name}")
            print(f"   az storage container delete --name media-staging --account-name {self.account_name}")
        else:
            print("⚠️  NOT SAFE TO DELETE: Containers contain data!")
            print(f"\n   - media: {media_blobs} blobs")
            print(f"   - media-staging: {staging_blobs} blobs")
            print("\n📋 Next Steps:")
            print("   1. Review the platform assignments above")
            print("   2. Run migration script to move blobs to platform-specific containers")
            print("   3. Verify database references are updated")
            print("   4. Re-run this audit to confirm containers are empty")
            print("   5. Only then delete the legacy containers")

            # Check for unknown files
            unknown_count = (
                len(self.results['media']['by_platform'].get('unknown', [])) +
                len(self.results['media-staging']['by_platform'].get('unknown', []))
            )
            if unknown_count > 0:
                print(f"\n⚠️  CRITICAL: {unknown_count} blobs have UNKNOWN platform assignment!")
                print("   These MUST be manually reviewed before migration.")

        print(f"\n{'─'*80}\n")

    def run(self):
        """Run the full audit"""
        print("\n🔍 Legacy Container Audit Tool")
        print(f"Storage Account: {self.account_name}\n")

        # Audit both containers
        self.audit_container('media')
        self.audit_container('media-staging')

        # Generate final report
        self.generate_report()

        # Save detailed results to file
        self.save_results()

    def save_results(self):
        """Save detailed results to JSON file"""
        import json
        from datetime import datetime

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        output_file = f"legacy_container_audit_{timestamp}.json"

        # Convert datetime objects to strings for JSON serialization
        output_data = {}
        for container, data in self.results.items():
            output_data[container] = {
                'stats': dict(data['stats']),
                'by_platform': {},
                'blobs': []
            }

            for platform, blobs in data['by_platform'].items():
                output_data[container]['by_platform'][platform] = [
                    {
                        'name': b['name'],
                        'size': b['size'],
                        'size_mb': b['size_mb'],
                        'platform': b['platform'],
                        'target_container': b['target_container'],
                        'last_modified': b['last_modified'].isoformat() if b['last_modified'] else None,
                        'content_type': b['content_type'],
                    }
                    for b in blobs
                ]

            output_data[container]['blobs'] = output_data[container]['by_platform']

        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(output_data, f, indent=2)

        print(f"📄 Detailed results saved to: {output_file}\n")


def main():
    """Main entry point"""
    try:
        auditor = ContainerAuditor()
        auditor.run()
    except KeyboardInterrupt:
        print("\n\n⚠️  Audit cancelled by user")
        sys.exit(1)
    except Exception as e:
        print(f"\nERROR: Audit failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
