"""
Check database references to files in legacy media containers

Scans all ImageField and FileField columns in the database to find references
to files stored in the legacy 'media' and 'media-staging' containers.

Usage:
    python scripts/check_legacy_media_references.py

Requirements:
    - Database connection configured
    - Django models loaded
"""

import os
import sys
from collections import defaultdict

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Load environment variables
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Set Django settings
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'azureproject.settings')

import django
django.setup()

from django.apps import apps
from django.db import models
from django.conf import settings


# Known files in legacy containers (from audit)
LEGACY_FILES = {
    'media': [
        'crush-lu/branding/social-preview.jpg',
        'journey_gifts/qr/WOY-0WR6XJ1Q.png',
        'journey_gifts/qr/WOY-49DAPAN8.png',
        'journey_gifts/qr/WOY-5038VJ55.png',
        'journey_gifts/qr/WOY-7F9XIZ7Y.png',
        'journey_gifts/qr/WOY-AH2D595W.png',
        'journey_gifts/qr/WOY-GV94Z6HR.png',
        'journey_gifts/qr/WOY-I9X63AT3.png',
        'journey_gifts/qr/WOY-L1FOCSJB.png',
        'journey_gifts/qr/WOY-LLZM40ZM.png',
        'journey_gifts/qr/WOY-MRKV0EB9.png',
        'journey_gifts/qr/WOY-QRWTTSJ0.png',
        'journey_gifts/qr/WOY-TPM1HGSA.png',
        'journey_gifts/qr/WOY-W8ZZHWTP.png',
        'journey_gifts/qr/WOY-ZPV8QW7C.png',
        'powerup/defaults/profile.png',
        'shared/homepage/hero-background.jpg',
        'vinsdelux/journey/step_01.png',
        'vinsdelux/journey/step_02.png',
        'vinsdelux/journey/step_03.png',
        'vinsdelux/journey/step_04.png',
        'vinsdelux/journey/step_05.png',
        'vinsdelux/producers/logos/producer_1_logo.jpg',
        'vinsdelux/producers/logos/producer_3_logo.jpg',
        'vinsdelux/producers/logos/producer_4_logo.jpg',
        'vinsdelux/producers/photos/producer_1_photo.jpg',
        'vinsdelux/producers/photos/producer_1_photo.png',
        'vinsdelux/producers/photos/producer_2_photo.jpg',
        'vinsdelux/producers/photos/producer_2_photo.png',
        'vinsdelux/producers/photos/producer_3_photo.jpg',
        'vinsdelux/producers/photos/producer_3_photo.png',
        'vinsdelux/producers/photos/producer_4_photo.jpg',
        'vinsdelux/producers/photos/producer_4_photo.png',
        'vinsdelux/producers/photos/producer_5_photo.jpg',
        'vinsdelux/producers/photos/producer_5_photo.png',
        'vinsdelux/products/gallery/coffret_1.png',
        'vinsdelux/products/gallery/coffret_2.png',
        'vinsdelux/products/gallery/coffret_3.png',
        'vinsdelux/products/gallery/coffret_4.png',
        'vinsdelux/products/gallery/coffret_5.png',
        'vinsdelux/products/gallery/plan_1.png',
        'vinsdelux/products/gallery/plan_2.jpg',
        'vinsdelux/products/gallery/plan_2.png',
        'vinsdelux/products/gallery/plan_3.jpg',
        'vinsdelux/products/gallery/plan_3.png',
        'vinsdelux/products/gallery/wine_1.jpg',
        'vinsdelux/products/gallery/wine_2.jpg',
        'vinsdelux/products/gallery/wine_3.jpg',
        'vinsdelux/vineyard-defaults/vineyard_01.jpg',
        'vinsdelux/vineyard-defaults/vineyard_02.jpg',
        'vinsdelux/vineyard-defaults/vineyard_03.jpg',
        'vinsdelux/vineyard-defaults/vineyard_04.jpg',
        'vinsdelux/vineyard-defaults/vineyard_05.jpg',
    ],
    'media-staging': [
        'journey_gifts/qr/WOY-GLLMPX1K.png',
        'journey_gifts/qr/WOY-WFW8TZAG.png',
    ]
}


class DatabaseReferenceChecker:
    """Check database for references to legacy media files"""

    def __init__(self):
        self.results = defaultdict(list)
        self.file_fields = []
        self.total_checked = 0

    def get_all_file_fields(self):
        """Get all ImageField and FileField from all models"""
        file_fields = []

        for model in apps.get_models():
            for field in model._meta.get_fields():
                if isinstance(field, (models.ImageField, models.FileField)):
                    file_fields.append({
                        'model': model,
                        'field': field,
                        'app': model._meta.app_label,
                        'model_name': model._meta.model_name,
                        'field_name': field.name,
                    })

        return file_fields

    def check_field_for_file(self, field_info, file_path):
        """Check if a specific field contains a reference to a file"""
        model = field_info['model']
        field = field_info['field']

        try:
            # Query for records where field contains this file path
            # Handle both full paths and partial paths
            queryset = model.objects.filter(**{
                f"{field.name}__icontains": file_path
            })

            count = queryset.count()
            if count > 0:
                # Get sample records (first 5)
                samples = list(queryset.values('id', field.name)[:5])
                return {
                    'count': count,
                    'samples': samples,
                }

        except Exception as e:
            # Some fields might not be queryable (e.g., on abstract models)
            pass

        return None

    def run(self):
        """Run the full reference check"""
        print("\n" + "="*80)
        print("DATABASE REFERENCE CHECK FOR LEGACY MEDIA FILES")
        print("="*80 + "\n")

        # Get all file fields
        print("Scanning Django models for ImageField/FileField...")
        self.file_fields = self.get_all_file_fields()
        print(f"Found {len(self.file_fields)} file fields across all models\n")

        # Check each legacy file
        total_files = sum(len(files) for files in LEGACY_FILES.values())
        print(f"Checking {total_files} files from legacy containers...\n")

        referenced_count = 0
        orphaned_count = 0

        for container, files in LEGACY_FILES.items():
            print(f"\n{'-'*80}")
            print(f"Container: {container}")
            print(f"{'-'*80}\n")

            for file_path in files:
                self.total_checked += 1
                found_references = False

                # Check each file field
                for field_info in self.file_fields:
                    result = self.check_field_for_file(field_info, file_path)

                    if result:
                        found_references = True
                        referenced_count += 1

                        self.results[file_path].append({
                            'app': field_info['app'],
                            'model': field_info['model_name'],
                            'field': field_info['field_name'],
                            'count': result['count'],
                            'samples': result['samples'],
                        })

                        print(f"FOUND: {file_path}")
                        print(f"  -> {field_info['app']}.{field_info['model_name']}.{field_info['field_name']}")
                        print(f"  -> {result['count']} record(s)")

                        # Show sample values
                        for sample in result['samples'][:2]:
                            print(f"     ID {sample['id']}: {sample[field_info['field_name']]}")

                        print()

                if not found_references:
                    orphaned_count += 1
                    print(f"NOT REFERENCED: {file_path}")

        # Print summary
        self.print_summary(referenced_count, orphaned_count)

    def print_summary(self, referenced_count, orphaned_count):
        """Print summary of findings"""
        print("\n" + "="*80)
        print("SUMMARY")
        print("="*80 + "\n")

        print(f"Total files checked: {self.total_checked}")
        print(f"Files WITH database references: {referenced_count}")
        print(f"Files WITHOUT database references (orphaned): {orphaned_count}\n")

        if orphaned_count > 0:
            print(f"WARNING: {orphaned_count} files are orphaned and safe to delete immediately")

        if referenced_count > 0:
            print(f"CRITICAL: {referenced_count} files are actively referenced in database")
            print("These MUST be migrated before deleting legacy containers\n")

            # Group by platform
            platform_summary = defaultdict(list)
            for file_path in self.results.keys():
                if file_path.startswith('vinsdelux/'):
                    platform = 'VinsDelux'
                elif file_path.startswith('journey_gifts/'):
                    platform = 'Crush.lu (Journey Gifts)'
                elif file_path.startswith('crush-lu/'):
                    platform = 'Crush.lu (Branding)'
                elif file_path.startswith('powerup/'):
                    platform = 'PowerUP'
                elif file_path.startswith('shared/'):
                    platform = 'Shared'
                else:
                    platform = 'Unknown'

                platform_summary[platform].append(file_path)

            print("References by Platform:")
            for platform, files in sorted(platform_summary.items()):
                print(f"  {platform}: {len(files)} file(s)")

        print("\n" + "-"*80)
        print("NEXT STEPS")
        print("-"*80 + "\n")

        if referenced_count > 0:
            print("1. Run migration script to move referenced files to platform containers")
            print("2. Update database records to point to new container paths")
            print("3. Verify all references point to new locations")
            print("4. Delete orphaned files (if any)")
            print("5. Delete legacy containers")
        else:
            print("1. All files are orphaned - safe to delete!")
            print("2. Run: az storage container delete --name media --account-name ...")
            print("3. Run: az storage container delete --name media-staging --account-name ...")

        print()


def main():
    """Main entry point"""
    try:
        checker = DatabaseReferenceChecker()
        checker.run()
    except KeyboardInterrupt:
        print("\n\nCancelled by user")
        sys.exit(1)
    except Exception as e:
        print(f"\nERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
