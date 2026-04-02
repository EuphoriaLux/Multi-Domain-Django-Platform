"""
Upload production cost export files to local Azurite blob storage
Correctly organizes files by their actual billing month
Usage: python scripts/upload_cost_exports_to_local.py
"""
import os
import sys
import gzip
import csv
import io
from datetime import datetime
from pathlib import Path

# Add project root to Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'azureproject.settings')
import django
django.setup()

from azure.storage.blob import BlobServiceClient
from django.conf import settings


def get_billing_period_from_file(file_path):
    """Extract billing period from CSV file with correct month-end dates"""
    import calendar
    try:
        with gzip.open(file_path, 'rt', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            first_row = next(reader, None)
            if first_row and 'BillingPeriodStart' in first_row:
                # BillingPeriodStart format: 2025-10-01T00:00:00Z
                billing_start = first_row['BillingPeriodStart'][:10]  # Get YYYY-MM-DD
                year = int(billing_start[:4])
                month = int(billing_start[5:7])

                # Get the last day of the month
                last_day = calendar.monthrange(year, month)[1]

                # Format: YYYYMMDD01-YYYYMMDDLAST
                return f"{year:04d}{month:02d}01-{year:04d}{month:02d}{last_day:02d}"
    except Exception as e:
        print(f"[WARNING] Could not extract billing period from {file_path}: {e}")
    return None


def upload_cost_exports():
    """Upload cost export files to local Azurite with correct month structure"""

    # Local Azurite connection string
    connection_string = "DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;BlobEndpoint=http://127.0.0.1:10000/devstoreaccount1;"

    # Container name for FinOps
    container_name = "powerup-finops"

    # Create blob service client
    blob_service_client = BlobServiceClient.from_connection_string(connection_string)

    # Create container if it doesn't exist
    try:
        container_client = blob_service_client.get_container_client(container_name)
        container_client.create_container()
        print(f"[OK] Created container: {container_name}")
    except Exception as e:
        if "ContainerAlreadyExists" in str(e):
            print(f"[OK] Container already exists: {container_name}")
        else:
            print(f"[ERROR] Error creating container: {e}")
            return

    # Upload files from msexports folder
    project_root_path = Path(__file__).parent.parent
    msexports_path = project_root_path / "msexports"

    files_to_upload = [
        ("oktober.gz", "part_0_0001.csv.gz"),
        ("november.gz", "part_0_0001.csv.gz"),
        ("dezember.gz", "part_0_0001.csv.gz"),
        ("january.gz", "part_1_0001.csv.gz"),
    ]

    uploaded_count = 0
    for source_filename, target_filename in files_to_upload:
        file_path = msexports_path / source_filename

        if not file_path.exists():
            print(f"[WARNING] File not found: {source_filename}")
            continue

        # Extract billing period from file content
        date_range = get_billing_period_from_file(file_path)
        if not date_range:
            print(f"[ERROR] Could not determine billing period for {source_filename}, skipping")
            continue

        # Create folder structure matching production pattern:
        # Pattern A (PartnerLed): partnerled/{subscription}/{date_range}/{guid}/part_*.csv.gz
        guid = "local-test-guid"
        blob_name = f"partnerled/PartnerLed-power_up/{date_range}/{guid}/{target_filename}"

        try:
            blob_client = blob_service_client.get_blob_client(
                container=container_name,
                blob=blob_name
            )

            with open(file_path, "rb") as data:
                blob_client.upload_blob(data, overwrite=True)

            file_size_kb = file_path.stat().st_size / 1024
            print(f"[OK] Uploaded: {blob_name} ({file_size_kb:.1f} KB)")
            print(f"     Period: {date_range}")
            uploaded_count += 1

        except Exception as e:
            print(f"[ERROR] Failed to upload {source_filename}: {e}")

    print(f"\n[OK] Successfully uploaded {uploaded_count}/{len(files_to_upload)} files")
    print(f"\nContainer: {container_name}")
    print(f"\nFiles organized by billing period:")
    print(f"  October 2025: partnerled/PartnerLed-power_up/20251001-20251031/...")
    print(f"  November 2025: partnerled/PartnerLed-power_up/20251101-20251130/...")
    print(f"  December 2025: partnerled/PartnerLed-power_up/20251201-20251231/...")
    print(f"  January 2026: partnerled/PartnerLed-power_up/20260101-20260131/...")
    print(f"\nNext step: Run import command:")
    print(f"  python manage.py import_cost_data --force")


if __name__ == "__main__":
    upload_cost_exports()
