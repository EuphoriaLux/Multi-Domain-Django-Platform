import os
import logging
from django.core.management.base import BaseCommand
from django.conf import settings
from azure.storage.blob import BlobServiceClient, BlobClient, ContainerClient
from io import BytesIO

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Tests direct upload to Azure Blob Storage using configured credentials'

    def handle(self, *args, **options):
        self.stdout.write("Attempting to test Azure Blob Storage upload...")

        account_name = getattr(settings, 'AZURE_ACCOUNT_NAME', None)
        account_key = getattr(settings, 'AZURE_ACCOUNT_KEY', None)
        container_name = getattr(settings, 'AZURE_CONTAINER_NAME', None)
        connection_string = getattr(settings, 'AZURE_STORAGE_CONNECTION_STRING', None) # Check for connection string too

        if not container_name:
            self.stderr.write(self.style.ERROR("AZURE_CONTAINER_NAME is not set in settings."))
            return

        blob_service_client = None
        using_connection_string = False

        if connection_string:
            self.stdout.write(f"Attempting to connect using AZURE_STORAGE_CONNECTION_STRING...")
            try:
                blob_service_client = BlobServiceClient.from_connection_string(connection_string)
                using_connection_string = True
                self.stdout.write(self.style.SUCCESS("Successfully created BlobServiceClient from connection string."))
            except Exception as e:
                self.stderr.write(self.style.ERROR(f"Failed to create BlobServiceClient from connection string: {e}"))
                # Fallback to account key if connection string fails but key is present
                if not (account_name and account_key):
                    return
                else:
                    self.stdout.write("Falling back to account name and key...")
                    blob_service_client = None # Reset

        if not blob_service_client and account_name and account_key:
            self.stdout.write(f"Attempting to connect using AZURE_ACCOUNT_NAME ('{account_name}') and AZURE_ACCOUNT_KEY...")
            connect_str_template = "DefaultEndpointsProtocol=https;AccountName={};AccountKey={};EndpointSuffix=core.windows.net"
            sdk_connection_string = connect_str_template.format(account_name, account_key)
            try:
                blob_service_client = BlobServiceClient.from_connection_string(sdk_connection_string)
                self.stdout.write(self.style.SUCCESS("Successfully created BlobServiceClient from account name and key."))
            except Exception as e:
                self.stderr.write(self.style.ERROR(f"Failed to create BlobServiceClient from account name and key: {e}"))
                return
        elif not blob_service_client:
            self.stderr.write(self.style.ERROR("Azure storage credentials (connection string or account name/key) are not configured correctly."))
            return

        # Create a dummy file in memory
        dummy_file_content = b"Hello Azure Blob Storage from Django management command!"
        dummy_file_name = "test_upload_from_django_command.txt"
        
        self.stdout.write(f"Attempting to upload '{dummy_file_name}' to container '{container_name}'...")

        try:
            blob_client = blob_service_client.get_blob_client(container=container_name, blob=dummy_file_name)
            
            with BytesIO(dummy_file_content) as stream:
                blob_client.upload_blob(stream, overwrite=True)
            
            self.stdout.write(self.style.SUCCESS(f"Successfully uploaded '{dummy_file_name}' to container '{container_name}'."))
            self.stdout.write(f"Blob URL: {blob_client.url}")

        except Exception as e:
            self.stderr.write(self.style.ERROR(f"Error during blob upload: {e}"))
            import traceback
            traceback.print_exc()

        self.stdout.write("Azure Blob Storage upload test finished.")
