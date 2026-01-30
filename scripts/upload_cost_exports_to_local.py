"""
Upload production cost export files to local Azurite blob storage
Usage: python scripts/upload_cost_exports_to_local.py
"""
import os
import sys
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


def upload_cost_exports():
    """Upload cost export files to local Azurite"""

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

    # Upload files
    project_root_path = Path(__file__).parent.parent
    files_to_upload = [
        "part_0_0001.csv.gz",
        "part_0_0001 (1).csv.gz",
        "part_0_0001 (2).csv.gz",
        "part_1_0001.csv.gz",
    ]

    # Create folder structure matching production pattern:
    # Pattern A (PartnerLed): partnerled/{subscription}/{date_range}/{guid}/part_*.csv.gz
    current_month = datetime.now().strftime("%Y%m")
    # Use same date range format as production (YYYYMMDD-YYYYMMDD for monthly exports)
    date_range = f"{current_month}01-{current_month}31"
    guid = "local-test-guid"
    blob_prefix = f"partnerled/PartnerLed-power_up/{date_range}/{guid}/"

    uploaded_count = 0
    for filename in files_to_upload:
        file_path = project_root_path / filename

        if not file_path.exists():
            print(f"[WARNING] File not found: {filename}")
            continue

        # Clean filename for blob (remove spaces/parentheses for consistency)
        clean_filename = filename.replace(" ", "_").replace("(", "").replace(")", "")
        blob_name = f"{blob_prefix}{clean_filename}"

        try:
            blob_client = blob_service_client.get_blob_client(
                container=container_name,
                blob=blob_name
            )

            with open(file_path, "rb") as data:
                blob_client.upload_blob(data, overwrite=True)

            file_size_kb = file_path.stat().st_size / 1024
            print(f"[OK] Uploaded: {blob_name} ({file_size_kb:.1f} KB)")
            uploaded_count += 1

        except Exception as e:
            print(f"[ERROR] Failed to upload {filename}: {e}")

    print(f"\n[OK] Successfully uploaded {uploaded_count}/{len(files_to_upload)} files")
    print(f"\nContainer: {container_name}")
    print(f"Path: {blob_prefix}")
    print(f"\nNext step: Run import command:")
    print(f"  python manage.py import_cost_data --force-reimport")


if __name__ == "__main__":
    upload_cost_exports()
