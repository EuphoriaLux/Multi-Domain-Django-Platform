import os
import logging
from django.core.management.base import BaseCommand
from django.conf import settings
from azure.storage.blob import BlobServiceClient
from pathlib import Path
import mimetypes

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Syncs local media files to Azure Blob Storage'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be uploaded without actually uploading',
        )
        parser.add_argument(
            '--overwrite',
            action='store_true',
            help='Overwrite existing blobs in Azure Storage',
        )
        parser.add_argument(
            '--folder',
            type=str,
            help='Specific folder to sync (e.g., "producers" or "products")',
        )

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS('🔄 Starting media sync to Azure Blob Storage...'))
        
        # Get Azure storage configuration
        account_name = getattr(settings, 'AZURE_ACCOUNT_NAME', None)
        account_key = getattr(settings, 'AZURE_ACCOUNT_KEY', None)
        container_name = getattr(settings, 'AZURE_CONTAINER_NAME', None)
        
        if not all([account_name, account_key, container_name]):
            self.stderr.write(self.style.ERROR('❌ Azure storage credentials not properly configured'))
            return
        
        # Create blob service client
        connection_string = f"DefaultEndpointsProtocol=https;AccountName={account_name};AccountKey={account_key};EndpointSuffix=core.windows.net"
        blob_service_client = BlobServiceClient.from_connection_string(connection_string)
        
        # Find local media directory
        # Check multiple possible locations
        possible_media_paths = [
            os.path.join(settings.BASE_DIR, 'media'),
            '/home/site/wwwroot/media',
            '/tmp/8ddd39c5c3de8b8/media',  # Azure App Service path
            settings.MEDIA_ROOT if hasattr(settings, 'MEDIA_ROOT') else None,
        ]
        
        media_root = None
        for path in possible_media_paths:
            if path and os.path.exists(path):
                media_root = path
                break
        
        if not media_root:
            self.stderr.write(self.style.ERROR('❌ Could not find local media directory'))
            self.stdout.write('Checked paths:')
            for path in possible_media_paths:
                if path:
                    self.stdout.write(f'  - {path} ({"exists" if os.path.exists(path) else "not found"})')
            return
        
        self.stdout.write(self.style.SUCCESS(f'📁 Found media directory: {media_root}'))
        
        # Get list of existing blobs if not overwriting
        existing_blobs = set()
        if not options['overwrite']:
            try:
                blob_list = blob_service_client.get_container_client(container_name).list_blobs()
                existing_blobs = {blob.name for blob in blob_list}
                self.stdout.write(f'📊 Found {len(existing_blobs)} existing blobs in Azure Storage')
            except Exception as e:
                self.stdout.write(self.style.WARNING(f'⚠️  Could not list existing blobs: {e}'))
        
        # Walk through media directory
        uploaded_count = 0
        skipped_count = 0
        error_count = 0
        
        for root, dirs, files in os.walk(media_root):
            for file in files:
                # Skip non-image files and system files
                if file.startswith('.') or file.lower().endswith(('.txt', '.log', '.tmp')):
                    continue
                
                local_file_path = os.path.join(root, file)
                
                # Calculate relative path from media root
                relative_path = os.path.relpath(local_file_path, media_root)
                blob_name = relative_path.replace('\\', '/')  # Ensure forward slashes for blob names
                
                # Check if we should filter by folder
                if options['folder']:
                    if not blob_name.startswith(options['folder']):
                        continue
                
                # Check if blob already exists
                if not options['overwrite'] and blob_name in existing_blobs:
                    self.stdout.write(f'⏭️  Skipping {blob_name} (already exists)')
                    skipped_count += 1
                    continue
                
                # Determine content type
                content_type, _ = mimetypes.guess_type(local_file_path)
                if not content_type:
                    content_type = 'application/octet-stream'
                
                if options['dry_run']:
                    self.stdout.write(f'🔍 Would upload: {blob_name} ({content_type})')
                    continue
                
                # Upload file
                try:
                    blob_client = blob_service_client.get_blob_client(
                        container=container_name, 
                        blob=blob_name
                    )
                    
                    with open(local_file_path, 'rb') as data:
                        blob_client.upload_blob(
                            data, 
                            overwrite=options['overwrite'],
                            content_settings={'content_type': content_type}
                        )
                    
                    blob_url = f"https://{account_name}.blob.core.windows.net/{container_name}/{blob_name}"
                    self.stdout.write(self.style.SUCCESS(f'✅ Uploaded: {blob_name}'))
                    self.stdout.write(f'   URL: {blob_url}')
                    uploaded_count += 1
                    
                except Exception as e:
                    self.stderr.write(self.style.ERROR(f'❌ Failed to upload {blob_name}: {e}'))
                    error_count += 1
        
        # Summary
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('📊 Sync Summary:'))
        if options['dry_run']:
            self.stdout.write(f'   🔍 Dry run completed - no files actually uploaded')
        else:
            self.stdout.write(f'   ✅ Uploaded: {uploaded_count} files')
            self.stdout.write(f'   ⏭️  Skipped: {skipped_count} files')
            self.stdout.write(f'   ❌ Errors: {error_count} files')
        
        if uploaded_count > 0:
            self.stdout.write('')
            self.stdout.write(self.style.SUCCESS('🎉 Media sync completed successfully!'))
            self.stdout.write(f'Files are now available at: https://{account_name}.blob.core.windows.net/{container_name}/')
        elif options['dry_run']:
            self.stdout.write('')
            self.stdout.write(self.style.SUCCESS('🔍 Dry run completed. Use without --dry-run to actually upload files.'))
        else:
            self.stdout.write('')
            self.stdout.write(self.style.WARNING('⚠️  No files were uploaded. Check your media directory and filters.'))