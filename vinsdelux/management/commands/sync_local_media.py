"""
Management command to sync local media files with database records for development.
This updates database image paths to use local media files instead of Azure URLs.
"""

import os
from django.core.management.base import BaseCommand, CommandError
from django.conf import settings
from django.core.files import File
from django.core.files.storage import default_storage
from vinsdelux.models import VdlProducer, VdlProductImage, HomepageContent
import glob


class Command(BaseCommand):
    help = 'Sync local media files with database records for development'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be updated without making changes',
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Force update even if files already have local paths',
        )

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError('This command should only be run in development (DEBUG=True)')

        dry_run = options['dry_run']
        force = options['force']
        
        self.stdout.write(self.style.SUCCESS('Starting local media sync...'))
        self.stdout.write(f'Media root: {settings.MEDIA_ROOT}')
        self.stdout.write(f'Media URL: {settings.MEDIA_URL}')
        
        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN MODE - No changes will be made'))
        
        updated_count = 0
        
        # Sync producer images
        updated_count += self.sync_producer_images(dry_run, force)
        
        # Sync product images
        updated_count += self.sync_product_images(dry_run, force)
        
        # Sync homepage content
        updated_count += self.sync_homepage_content(dry_run, force)
        
        if dry_run:
            self.stdout.write(
                self.style.SUCCESS(f'Would update {updated_count} image references')
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(f'Successfully updated {updated_count} image references')
            )

    def sync_producer_images(self, dry_run=False, force=False):
        """Sync producer logo and photo images"""
        updated = 0
        
        # Map local media files
        logo_files = {}
        photo_files = {}
        producer_logos_path = os.path.join(settings.MEDIA_ROOT, 'producers', 'logos')
        producer_photos_path = os.path.join(settings.MEDIA_ROOT, 'producers', 'photos')
        
        if os.path.exists(producer_logos_path):
            for file_path in glob.glob(os.path.join(producer_logos_path, '*')):
                if os.path.isfile(file_path):
                    filename = os.path.basename(file_path)
                    # Remove extension and normalize for matching
                    base_name = os.path.splitext(filename)[0].lower()
                    logo_files[base_name] = f'producers/logos/{filename}'
        
        if os.path.exists(producer_photos_path):
            for file_path in glob.glob(os.path.join(producer_photos_path, '*')):
                if os.path.isfile(file_path):
                    filename = os.path.basename(file_path)
                    # Remove extension and normalize for matching
                    base_name = os.path.splitext(filename)[0].lower()
                    photo_files[base_name] = f'producers/photos/{filename}'
        
        self.stdout.write(f'Found {len(logo_files)} logo files and {len(photo_files)} photo files')
        
        for producer in VdlProducer.objects.all():
            # Update logo - check if current path has Django suffix and find matching local file
            if producer.logo and (force or not os.path.exists(os.path.join(settings.MEDIA_ROOT, producer.logo.name))):
                current_filename = os.path.basename(producer.logo.name)
                # Remove Django's automatic suffix (like _aCiWcDB) and extension
                base_name = os.path.splitext(current_filename)[0]
                # Remove Django suffix pattern (underscore followed by random chars)
                import re
                clean_name = re.sub(r'_[a-zA-Z0-9]{7}$', '', base_name).lower()
                
                # First try to find in logo files
                if clean_name in logo_files:
                    if not dry_run:
                        producer.logo.name = logo_files[clean_name]
                        producer.save(update_fields=['logo'])
                    self.stdout.write(f'  Updated {producer.name} logo: {logo_files[clean_name]}')
                    updated += 1
                # If not found in logos, try photos directory (common mistake)
                elif clean_name in photo_files:
                    if not dry_run:
                        producer.logo.name = photo_files[clean_name]
                        producer.save(update_fields=['logo'])
                    self.stdout.write(f'  Updated {producer.name} logo (from photos): {photo_files[clean_name]}')
                    updated += 1
                else:
                    self.stdout.write(f'  No matching logo found for: {current_filename} (looking for: {clean_name})')
            
            # Update producer photo - same logic
            if producer.producer_photo and (force or not os.path.exists(os.path.join(settings.MEDIA_ROOT, producer.producer_photo.name))):
                current_filename = os.path.basename(producer.producer_photo.name)
                base_name = os.path.splitext(current_filename)[0]
                import re
                clean_name = re.sub(r'_[a-zA-Z0-9]{7}$', '', base_name).lower()
                
                if clean_name in photo_files:
                    if not dry_run:
                        producer.producer_photo.name = photo_files[clean_name]
                        producer.save(update_fields=['producer_photo'])
                    self.stdout.write(f'  Updated {producer.name} photo: {photo_files[clean_name]}')
                    updated += 1
                # If not found in photos, try logos directory
                elif clean_name in logo_files:
                    if not dry_run:
                        producer.producer_photo.name = logo_files[clean_name]
                        producer.save(update_fields=['producer_photo'])
                    self.stdout.write(f'  Updated {producer.name} photo (from logos): {logo_files[clean_name]}')
                    updated += 1
                else:
                    self.stdout.write(f'  No matching photo found for: {current_filename} (looking for: {clean_name})')
        
        return updated

    def sync_product_images(self, dry_run=False, force=False):
        """Sync product gallery images"""
        updated = 0
        
        # Map local product images
        media_files = {}
        products_path = os.path.join(settings.MEDIA_ROOT, 'products')
        
        for root, dirs, files in os.walk(products_path):
            for file in files:
                if file.lower().endswith(('.jpg', '.jpeg', '.png', '.gif')):
                    rel_path = os.path.relpath(os.path.join(root, file), settings.MEDIA_ROOT)
                    # Normalize path for Windows
                    rel_path = rel_path.replace('\\', '/')
                    # Use base filename without extension as key
                    base_name = os.path.splitext(file)[0].lower()
                    media_files[base_name] = rel_path
        
        self.stdout.write(f'Found {len(media_files)} product media files')
        
        for img in VdlProductImage.objects.all():
            if img.image and (force or not os.path.exists(os.path.join(settings.MEDIA_ROOT, img.image.name))):
                current_filename = os.path.basename(img.image.name)
                # Remove Django's automatic suffix (like _bFhlGon) and extension
                base_name = os.path.splitext(current_filename)[0]
                import re
                clean_name = re.sub(r'_[a-zA-Z0-9]{7}$', '', base_name).lower()
                
                if clean_name in media_files:
                    if not dry_run:
                        img.image.name = media_files[clean_name]
                        img.save(update_fields=['image'])
                    self.stdout.write(f'  Updated product image: {media_files[clean_name]}')
                    updated += 1
                else:
                    self.stdout.write(f'  No matching product image found for: {current_filename} (looking for: {clean_name})')
        
        return updated

    def sync_homepage_content(self, dry_run=False, force=False):
        """Sync homepage hero background image"""
        updated = 0
        
        # Map homepage images
        homepage_path = os.path.join(settings.MEDIA_ROOT, 'homepage')
        if os.path.exists(homepage_path):
            for file_path in glob.glob(os.path.join(homepage_path, '*')):
                if os.path.isfile(file_path):
                    filename = os.path.basename(file_path)
                    
                    # Update homepage content
                    try:
                        homepage_content = HomepageContent.objects.first()
                        if homepage_content and homepage_content.hero_background_image:
                            if force or 'blob.core.windows.net' in homepage_content.hero_background_image.url:
                                if not dry_run:
                                    homepage_content.hero_background_image.name = f'homepage/{filename}'
                                    homepage_content.save(update_fields=['hero_background_image'])
                                self.stdout.write(f'  Updated homepage background: homepage/{filename}')
                                updated += 1
                    except HomepageContent.DoesNotExist:
                        pass
        
        return updated