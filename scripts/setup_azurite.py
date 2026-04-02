#!/usr/bin/env python
"""
Setup script for Azurite (Azure Blob Storage emulator).

Creates the required blob containers for local development:
- media: Public container for general media files
- crush-profiles-private: Private container for Crush.lu profile photos

Usage:
    python scripts/setup_azurite.py

Or via management command:
    python manage.py setup_local_dev
"""

from azure.storage.blob import BlobServiceClient, PublicAccess


# Azurite well-known development credentials
AZURITE_ACCOUNT_NAME = "devstoreaccount1"
AZURITE_ACCOUNT_KEY = (
    "Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq"
    "/K1SZFPTOtr/KBHBeksoGMGw=="
)
AZURITE_BLOB_URL = f"http://127.0.0.1:10000/{AZURITE_ACCOUNT_NAME}"

# Containers to create
CONTAINERS = [
    {"name": "media", "public_access": PublicAccess.BLOB},  # Public read for blobs
    {"name": "crush-profiles-private", "public_access": None},  # Private (SAS required)
]


def get_blob_service_client():
    """Create a BlobServiceClient for Azurite."""
    connection_string = (
        f"DefaultEndpointsProtocol=http;"
        f"AccountName={AZURITE_ACCOUNT_NAME};"
        f"AccountKey={AZURITE_ACCOUNT_KEY};"
        f"BlobEndpoint={AZURITE_BLOB_URL};"
    )
    return BlobServiceClient.from_connection_string(connection_string)


def setup_containers():
    """Create all required containers in Azurite."""
    print("Connecting to Azurite...")

    try:
        client = get_blob_service_client()

        # Test connection
        client.get_account_information()
        print("Connected to Azurite successfully!")

    except Exception as e:
        print(f"ERROR: Could not connect to Azurite at {AZURITE_BLOB_URL}")
        print(f"Make sure Azurite is running: docker-compose up -d azurite")
        print(f"Error details: {e}")
        return False

    success = True
    for container_config in CONTAINERS:
        container_name = container_config["name"]
        public_access = container_config["public_access"]

        try:
            container_client = client.get_container_client(container_name)

            if container_client.exists():
                print(f"Container '{container_name}' already exists")
            else:
                container_client.create_container(public_access=public_access)
                access_type = "public" if public_access else "private"
                print(f"Created container '{container_name}' ({access_type})")

        except Exception as e:
            print(f"ERROR creating container '{container_name}': {e}")
            success = False

    return success


def main():
    """Main entry point."""
    print("=" * 50)
    print("Azurite Container Setup")
    print("=" * 50)
    print()

    if setup_containers():
        print()
        print("Setup complete!")
        print()
        print("Azurite is ready. Container URLs:")
        print(f"  - Media:   {AZURITE_BLOB_URL}/media")
        print(f"  - Private: {AZURITE_BLOB_URL}/crush-profiles-private")
        return 0
    else:
        print()
        print("Setup failed. Check errors above.")
        return 1


if __name__ == "__main__":
    exit(main())
