"""
Analyze legacy media files to determine their purpose and migration target

Based on file paths and model definitions, determines:
1. Which model/field should reference each file
2. Target platform-specific container
3. Whether the file is likely still needed

Does NOT require database connection - uses static analysis only.
"""

import os
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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


def analyze_file(file_path, container):
    """Analyze a single file to determine its purpose and migration target"""

    analysis = {
        'file': file_path,
        'container': container,
        'platform': None,
        'target_container': None,
        'likely_model': None,
        'likely_field': None,
        'status': None,
        'action': None,
        'notes': None,
    }

    # Journey Gift QR Codes
    if file_path.startswith('journey_gifts/qr/'):
        analysis['platform'] = 'Crush.lu'
        analysis['target_container'] = 'crush-lu-media'
        analysis['likely_model'] = 'JourneyGift'
        analysis['likely_field'] = 'qr_code_image'
        analysis['status'] = 'ACTIVE - Likely referenced in database'
        analysis['action'] = 'MIGRATE to crush-lu-media (keep same path)'
        analysis['notes'] = 'Created before storage fix. New QR codes go to crush-lu-media automatically.'

    # Crush.lu Branding
    elif file_path.startswith('crush-lu/branding/'):
        analysis['platform'] = 'Crush.lu'
        analysis['target_container'] = 'crush-lu-media'
        analysis['likely_model'] = 'Hardcoded in templates'
        analysis['likely_field'] = 'N/A'
        analysis['status'] = 'ACTIVE - Used in meta tags'
        analysis['action'] = 'MIGRATE to crush-lu-media (keep same path)'
        analysis['notes'] = 'Social media preview image for OG tags'

    # VinsDelux Producer Files
    elif file_path.startswith('vinsdelux/producers/'):
        analysis['platform'] = 'VinsDelux'
        analysis['target_container'] = 'vinsdelux-media'
        analysis['likely_model'] = 'VdlProducer'
        if 'logo' in file_path:
            analysis['likely_field'] = 'logo'
        else:
            analysis['likely_field'] = 'photo'
        analysis['status'] = 'ACTIVE - Likely referenced in database'
        analysis['action'] = 'MIGRATE to vinsdelux-media (keep same path)'
        analysis['notes'] = 'Sample producer data from setup commands'

    # VinsDelux Products
    elif file_path.startswith('vinsdelux/products/'):
        analysis['platform'] = 'VinsDelux'
        analysis['target_container'] = 'vinsdelux-media'
        if 'coffret' in file_path:
            analysis['likely_model'] = 'VdlCoffret'
            analysis['likely_field'] = 'image'
        elif 'plan' in file_path:
            analysis['likely_model'] = 'VdlAdoptionPlan'
            analysis['likely_field'] = 'image'
        elif 'wine' in file_path:
            analysis['likely_model'] = 'VdlCoffret (wine images)'
            analysis['likely_field'] = 'wine_image_*'
        analysis['status'] = 'ACTIVE - Likely referenced in database'
        analysis['action'] = 'MIGRATE to vinsdelux-media (keep same path)'
        analysis['notes'] = 'Sample product data from setup commands'

    # VinsDelux Vineyard Defaults
    elif file_path.startswith('vinsdelux/vineyard-defaults/'):
        analysis['platform'] = 'VinsDelux'
        analysis['target_container'] = 'vinsdelux-media'
        analysis['likely_model'] = 'VdlPlot (default images)'
        analysis['likely_field'] = 'adoption_image'
        analysis['status'] = 'ACTIVE - Default images for plots without custom images'
        analysis['action'] = 'MIGRATE to vinsdelux-media (keep same path)'
        analysis['notes'] = 'Default vineyard images assigned to plots'

    # VinsDelux Journey Steps
    elif file_path.startswith('vinsdelux/journey/'):
        analysis['platform'] = 'VinsDelux'
        analysis['target_container'] = 'vinsdelux-media'
        analysis['likely_model'] = 'Hardcoded in templates'
        analysis['likely_field'] = 'N/A'
        analysis['status'] = 'ACTIVE - Used in adoption journey UI'
        analysis['action'] = 'MIGRATE to vinsdelux-media (keep same path)'
        analysis['notes'] = 'Step-by-step adoption journey illustrations'

    # PowerUP Default Profile
    elif file_path.startswith('powerup/defaults/'):
        analysis['platform'] = 'PowerUP'
        analysis['target_container'] = 'powerup-media'
        analysis['likely_model'] = 'Delegation/Company (default image)'
        analysis['likely_field'] = 'logo'
        analysis['status'] = 'ACTIVE - Default profile image'
        analysis['action'] = 'MIGRATE to powerup-media (keep same path)'
        analysis['notes'] = 'Default logo for companies without custom logo'

    # Shared Homepage
    elif file_path.startswith('shared/homepage/'):
        analysis['platform'] = 'Shared'
        analysis['target_container'] = 'shared-media'
        analysis['likely_model'] = 'Hardcoded in templates'
        analysis['likely_field'] = 'N/A'
        analysis['status'] = 'ACTIVE - Homepage background'
        analysis['action'] = 'MIGRATE to shared-media (keep same path)'
        analysis['notes'] = 'Hero background for landing pages'

    else:
        analysis['platform'] = 'Unknown'
        analysis['target_container'] = 'NEEDS_MANUAL_REVIEW'
        analysis['status'] = 'UNKNOWN'
        analysis['action'] = 'MANUAL REVIEW REQUIRED'

    return analysis


def main():
    """Main analysis"""
    print("\n" + "="*80)
    print("LEGACY MEDIA FILES - STATIC ANALYSIS")
    print("="*80 + "\n")

    all_files = []
    for container, files in LEGACY_FILES.items():
        for file_path in files:
            analysis = analyze_file(file_path, container)
            all_files.append(analysis)

    # Group by platform
    by_platform = {}
    for item in all_files:
        platform = item['platform']
        if platform not in by_platform:
            by_platform[platform] = []
        by_platform[platform].append(item)

    # Print analysis by platform
    for platform in sorted(by_platform.keys()):
        files = by_platform[platform]
        print(f"\n{'-'*80}")
        print(f"PLATFORM: {platform}")
        print(f"{'-'*80}\n")
        print(f"Files: {len(files)}")
        print(f"Target Container: {files[0]['target_container']}\n")

        # Show first few examples
        for item in files[:5]:
            print(f"  {item['file']}")
            print(f"    Model: {item['likely_model']}")
            print(f"    Field: {item['likely_field']}")
            print(f"    Status: {item['status']}")
            print(f"    Action: {item['action']}")
            if item['notes']:
                print(f"    Notes: {item['notes']}")
            print()

        if len(files) > 5:
            print(f"  ... and {len(files) - 5} more files\n")

    # Summary
    print("\n" + "="*80)
    print("MIGRATION SUMMARY")
    print("="*80 + "\n")

    print("All 55 files should be migrated to platform-specific containers:\n")

    migration_plan = {}
    for item in all_files:
        target = item['target_container']
        if target not in migration_plan:
            migration_plan[target] = []
        migration_plan[target].append(item['file'])

    for target, files in sorted(migration_plan.items()):
        print(f"  {target}: {len(files)} files")

    print("\n" + "-"*80)
    print("DATABASE IMPACT ASSESSMENT")
    print("-"*80 + "\n")

    print("Files likely REFERENCED in database (need path updates):")
    print("  - Journey Gift QR codes: 16 files -> JourneyGift.qr_code_image")
    print("  - VinsDelux producers: 11 files -> VdlProducer.logo/photo")
    print("  - VinsDelux products: 15 files -> VdlCoffret/VdlAdoptionPlan images")
    print("  - VinsDelux vineyard defaults: 5 files -> VdlPlot.adoption_image")
    print("  Total: ~47 database records to update\n")

    print("Files HARDCODED in templates (need template updates):")
    print("  - Crush.lu social preview: 1 file")
    print("  - VinsDelux journey steps: 5 files")
    print("  - PowerUP default profile: 1 file")
    print("  - Shared homepage hero: 1 file")
    print("  Total: ~8 template references to update\n")

    print("-"*80)
    print("CRITICAL NOTE")
    print("-"*80 + "\n")

    print("The AZURE_CONTAINER_NAME environment variable currently points to 'media'.")
    print("This means file URLs are constructed as:")
    print("  https://storage.blob.core.windows.net/media/{file_path}\n")

    print("After migration, URLs will change to:")
    print("  https://storage.blob.core.windows.net/crush-lu-media/{file_path}")
    print("  https://storage.blob.core.windows.net/vinsdelux-media/{file_path}")
    print("  etc.\n")

    print("Database records storing just the path (e.g., 'journey_gifts/qr/WOY-*.png')")
    print("will work fine because Django's storage backend handles the container name.")
    print("But hardcoded URLs in templates MUST be updated.\n")

    print("-"*80)
    print("RECOMMENDED MIGRATION STRATEGY")
    print("-"*80 + "\n")

    print("1. Create migration script to:")
    print("   - Copy files from 'media' -> platform-specific containers")
    print("   - Keep same file paths within containers")
    print("   - Verify copy succeeded")
    print("")
    print("2. Database records should work automatically IF they only store paths")
    print("   (Django storage backend will use new container)")
    print("")
    print("3. Update template references manually:")
    print("   - Search for 'social-preview.jpg'")
    print("   - Search for 'journey/step_'")
    print("   - Search for 'defaults/profile.png'")
    print("   - Search for 'homepage/hero-background.jpg'")
    print("")
    print("4. Update infra/resources.bicep:")
    print("   - Remove or update AZURE_CONTAINER_NAME")
    print("   - Point to shared-media or remove entirely")
    print("")
    print("5. Test on staging first")
    print("")
    print("6. Delete legacy containers only after verification")
    print("")


if __name__ == '__main__':
    main()
