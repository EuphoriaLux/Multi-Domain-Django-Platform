import os
import logging
from django.core.management.base import BaseCommand
from django.conf import settings
from azure.storage.blob import BlobServiceClient
from pathlib import Path
import mimetypes
from vinsdelux.models import VdlProducer, VdlCoffret, VdlAdoptionPlan, VdlProductImage, HomepageContent
from django.core.files.storage import default_storage
from django.core.files import File

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Syncs local media files to Azure Blob Storage AND updates database references'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be uploaded and updated without actually doing it',
        )
        parser.add_argument(
            '--overwrite',
            action='store_true',
            help='Overwrite existing blobs in Azure Storage',
        )

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS('🔄 Starting media sync with database updates...'))
        
        # Get Azure storage configuration
        account_name = getattr(settings, 'AZURE_ACCOUNT_NAME', None)
        account_key = getattr(settings, 'AZURE_ACCOUNT_KEY', None)
        container_name = getattr(settings, 'AZURE_CONTAINER_NAME', None)
        
        if not all([account_name, account_key, container_name]):
            self.stderr.write(self.style.ERROR('❌ Azure storage credentials not properly configured'))
            return
        
        # Find local media directory
        possible_media_paths = [
            os.path.join(settings.BASE_DIR, 'media'),
            '/home/site/wwwroot/media',
            '/tmp/8ddd39c5c3de8b8/media',
        ]
        
        media_root = None
        for path in possible_media_paths:
            if path and os.path.exists(path):
                media_root = path
                break
        
        if not media_root:
            self.stderr.write(self.style.ERROR('❌ Could not find local media directory'))
            return
        
        self.stdout.write(self.style.SUCCESS(f'📁 Found media directory: {media_root}'))
        
        uploaded_count = 0
        updated_count = 0
        
        # Process producer logos and photos
        self.stdout.write('🏭 Processing producer images...')
        for producer in VdlProducer.objects.all():
            # Process logo
            if self._process_producer_image(producer, 'logo', media_root, options):
                updated_count += 1
                uploaded_count += 1
            
            # Process producer photo
            if self._process_producer_image(producer, 'producer_photo', media_root, options):
                updated_count += 1
                uploaded_count += 1
        
        # Process product images
        self.stdout.write('📦 Processing product images...')
        for image in VdlProductImage.objects.all():
            if self._process_product_image(image, media_root, options):
                updated_count += 1
                uploaded_count += 1
        
        # Process homepage images
        self.stdout.write('🏠 Processing homepage images...')
        for content in HomepageContent.objects.all():
            if self._process_homepage_image(content, media_root, options):
                updated_count += 1
                uploaded_count += 1
        
        # Summary
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('📊 Sync Summary:'))
        if options['dry_run']:
            self.stdout.write(f'   🔍 Dry run completed - no files actually uploaded or updated')
        else:
            self.stdout.write(f'   ✅ Files uploaded: {uploaded_count}')
            self.stdout.write(f'   🔄 Database records updated: {updated_count}')
        
        if uploaded_count > 0 and not options['dry_run']:
            self.stdout.write('')
            self.stdout.write(self.style.SUCCESS('🎉 Media sync with database updates completed!'))

    def _process_producer_image(self, producer, field_name, media_root, options):
        """Process producer logo or photo"""
        field = getattr(producer, field_name)
        if not field:
            return False
        
        # Check if field already has a proper Azure path
        if field.name.startswith('http') or '/' not in field.name:
            return False
        
        local_file_path = os.path.join(media_root, field.name)
        if not os.path.exists(local_file_path):
            # Try alternative paths
            alt_paths = [
                os.path.join(media_root, f'producers/logos/{os.path.basename(field.name)}'),
                os.path.join(media_root, f'producers/photos/{os.path.basename(field.name)}'),
                os.path.join(media_root, f'homepage/{os.path.basename(field.name)}'),
            ]
            
            for alt_path in alt_paths:
                if os.path.exists(alt_path):
                    local_file_path = alt_path
                    break
            else:
                self.stdout.write(self.style.WARNING(f'⚠️  File not found: {field.name}'))
                return False
        
        if options['dry_run']:
            self.stdout.write(f'🔍 Would upload and update: {producer.name} - {field_name}')
            return True
        
        try:
            # Upload file using Django's storage system
            with open(local_file_path, 'rb') as f:
                # Generate proper path
                if field_name == 'logo':
                    new_path = f'producers/logos/{os.path.basename(local_file_path)}'
                else:
                    new_path = f'producers/photos/{os.path.basename(local_file_path)}'
                
                # Save using Django's storage (automatically goes to Azure)
                django_file = File(f, name=os.path.basename(local_file_path))
                saved_path = default_storage.save(new_path, django_file)
                
                # Update the model field
                setattr(producer, field_name, saved_path)
                producer.save(update_fields=[field_name])
                
                self.stdout.write(self.style.SUCCESS(f'✅ Updated {producer.name} - {field_name}'))
                return True
                
        except Exception as e:
            self.stderr.write(self.style.ERROR(f'❌ Failed to process {producer.name} - {field_name}: {e}'))
            return False
    
    def _process_product_image(self, product_image, media_root, options):
        """Process product image"""
        if not product_image.image:
            return False
        
        # Check if already has proper Azure path
        if product_image.image.name.startswith('http'):
            return False
        
        local_file_path = os.path.join(media_root, product_image.image.name)
        if not os.path.exists(local_file_path):
            # Try in products/gallery/
            alt_path = os.path.join(media_root, f'products/gallery/{os.path.basename(product_image.image.name)}')
            if os.path.exists(alt_path):
                local_file_path = alt_path
            else:
                self.stdout.write(self.style.WARNING(f'⚠️  File not found: {product_image.image.name}'))
                return False
        
        if options['dry_run']:
            self.stdout.write(f'🔍 Would upload and update product image: {product_image.alt_text}')
            return True
        
        try:
            # Upload using Django's storage system
            with open(local_file_path, 'rb') as f:
                new_path = f'products/gallery/{os.path.basename(local_file_path)}'
                django_file = File(f, name=os.path.basename(local_file_path))
                saved_path = default_storage.save(new_path, django_file)
                
                # Update the model field
                product_image.image = saved_path
                product_image.save(update_fields=['image'])
                
                self.stdout.write(self.style.SUCCESS(f'✅ Updated product image: {product_image.alt_text}'))
                return True
                
        except Exception as e:
            self.stderr.write(self.style.ERROR(f'❌ Failed to process product image {product_image.alt_text}: {e}'))
            return False
    
    def _process_homepage_image(self, content, media_root, options):
        """Process homepage background image"""
        if not content.hero_background_image:
            return False
        
        # Check if already has proper Azure path
        if content.hero_background_image.name.startswith('http'):
            return False
        
        local_file_path = os.path.join(media_root, content.hero_background_image.name)
        if not os.path.exists(local_file_path):
            # Try in homepage/
            alt_path = os.path.join(media_root, f'homepage/{os.path.basename(content.hero_background_image.name)}')
            if os.path.exists(alt_path):
                local_file_path = alt_path
            else:
                self.stdout.write(self.style.WARNING(f'⚠️  File not found: {content.hero_background_image.name}'))
                return False
        
        if options['dry_run']:
            self.stdout.write(f'🔍 Would upload and update homepage background image')
            return True
        
        try:
            # Upload using Django's storage system
            with open(local_file_path, 'rb') as f:
                new_path = f'homepage/{os.path.basename(local_file_path)}'
                django_file = File(f, name=os.path.basename(local_file_path))
                saved_path = default_storage.save(new_path, django_file)
                
                # Update the model field
                content.hero_background_image = saved_path
                content.save(update_fields=['hero_background_image'])
                
                self.stdout.write(self.style.SUCCESS(f'✅ Updated homepage background image'))
                return True
                
        except Exception as e:
            self.stderr.write(self.style.ERROR(f'❌ Failed to process homepage image: {e}'))
            return False