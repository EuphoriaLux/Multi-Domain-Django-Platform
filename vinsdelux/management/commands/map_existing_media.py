import os
import logging
from django.core.management.base import BaseCommand
from django.conf import settings
from django.core.files.storage import default_storage
from django.core.files import File
from vinsdelux.models import VdlProducer, VdlCoffret, VdlAdoptionPlan, VdlProductImage, HomepageContent
from django.contrib.contenttypes.models import ContentType
import random

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Maps existing local media files to database records intelligently'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be mapped without actually doing it',
        )

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS('🎯 Starting intelligent media mapping...'))
        
        # Find local media directory
        possible_media_paths = [
            os.path.join(settings.BASE_DIR, 'media'),
            '/home/site/wwwroot/media',
            '/tmp/8ddd3a0284a7251/media',
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
        
        # Get available files
        producer_logos = self._get_files(media_root, 'producers/logos')
        producer_photos = self._get_files(media_root, 'producers/photos') 
        product_images = self._get_files(media_root, 'products/gallery')
        homepage_images = self._get_files(media_root, 'homepage')
        
        self.stdout.write(f'📊 Found {len(producer_logos)} producer logos')
        self.stdout.write(f'📊 Found {len(producer_photos)} producer photos')
        self.stdout.write(f'📊 Found {len(product_images)} product images')
        self.stdout.write(f'📊 Found {len(homepage_images)} homepage images')
        
        updated_count = 0
        
        # Map producer images
        self.stdout.write('\n🏭 Mapping producer images...')
        producers = list(VdlProducer.objects.all())
        
        for i, producer in enumerate(producers):
            # Map logo
            if producer_logos and (not producer.logo or not self._file_exists_in_azure(producer.logo.name)):
                logo_file = producer_logos.pop(0)  # Take first available
                if self._map_file_to_field(producer, 'logo', logo_file, f'producers/logos/producer_{i+1}_logo.jpg', options):
                    updated_count += 1
            
            # Map photo
            if producer_photos and (not producer.producer_photo or not self._file_exists_in_azure(producer.producer_photo.name)):
                photo_file = producer_photos.pop(0)  # Take first available
                if self._map_file_to_field(producer, 'producer_photo', photo_file, f'producers/photos/producer_{i+1}_photo.jpg', options):
                    updated_count += 1
        
        # Map product images
        self.stdout.write('\n📦 Mapping product images...')
        
        # Clear existing product images that don't have files
        orphaned_images = []
        for img in VdlProductImage.objects.all():
            if not self._file_exists_in_azure(img.image.name):
                orphaned_images.append(img)
        
        if orphaned_images and not options['dry_run']:
            self.stdout.write(f'🗑️  Removing {len(orphaned_images)} orphaned image records...')
            for img in orphaned_images:
                img.delete()
        
        # Create new product images for coffrets
        coffrets = list(VdlCoffret.objects.all())
        for i, coffret in enumerate(coffrets):
            if product_images and not coffret.images.exists():
                image_file = product_images.pop(0)
                if self._create_product_image(coffret, image_file, f'products/gallery/wine_{i+1}.jpg', options):
                    updated_count += 1
        
        # Create product images for adoption plans
        adoption_plans = list(VdlAdoptionPlan.objects.all())
        for i, plan in enumerate(adoption_plans):
            if product_images and not plan.images.exists():
                image_file = product_images.pop(0)
                if self._create_product_image(plan, image_file, f'products/gallery/plan_{i+1}.jpg', options):
                    updated_count += 1
        
        # Map homepage image
        self.stdout.write('\n🏠 Mapping homepage image...')
        homepage_content = HomepageContent.objects.first()
        if homepage_content and homepage_images:
            if not homepage_content.hero_background_image or not self._file_exists_in_azure(homepage_content.hero_background_image.name):
                bg_file = homepage_images[0]  # Take first/best image
                if self._map_file_to_field(homepage_content, 'hero_background_image', bg_file, 'homepage/hero-background.jpg', options):
                    updated_count += 1
        
        # Summary
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('📊 Mapping Summary:'))
        if options['dry_run']:
            self.stdout.write(f'   🔍 Dry run completed - no files actually mapped')
        else:
            self.stdout.write(f'   ✅ Database records updated: {updated_count}')
            self.stdout.write(f'   📸 Files uploaded to Azure Blob Storage')
        
        if updated_count > 0 and not options['dry_run']:
            self.stdout.write('')
            self.stdout.write(self.style.SUCCESS('🎉 Media mapping completed! Your images should now be visible on the website.'))
    
    def _get_files(self, media_root, subfolder):
        """Get list of files in a subfolder"""
        folder_path = os.path.join(media_root, subfolder)
        if not os.path.exists(folder_path):
            return []
        
        files = []
        for file in os.listdir(folder_path):
            if file.lower().endswith(('.png', '.jpg', '.jpeg')):
                files.append(os.path.join(folder_path, file))
        
        return files
    
    def _file_exists_in_azure(self, filename):
        """Check if file exists in Azure storage"""
        if not filename:
            return False
        try:
            return default_storage.exists(filename)
        except:
            return False
    
    def _map_file_to_field(self, instance, field_name, local_file_path, azure_path, options):
        """Map a local file to a model field"""
        if options['dry_run']:
            self.stdout.write(f'🔍 Would map: {os.path.basename(local_file_path)} → {instance} - {field_name}')
            return True
        
        try:
            with open(local_file_path, 'rb') as f:
                django_file = File(f, name=os.path.basename(azure_path))
                
                # Save to Azure using Django's storage
                saved_path = default_storage.save(azure_path, django_file)
                
                # Update the model field
                setattr(instance, field_name, saved_path)
                instance.save(update_fields=[field_name])
                
                self.stdout.write(self.style.SUCCESS(f'✅ Mapped: {os.path.basename(local_file_path)} → {instance} - {field_name}'))
                return True
                
        except Exception as e:
            self.stderr.write(self.style.ERROR(f'❌ Failed to map {local_file_path}: {e}'))
            return False
    
    def _create_product_image(self, product, local_file_path, azure_path, options):
        """Create a new product image record"""
        if options['dry_run']:
            self.stdout.write(f'🔍 Would create product image: {os.path.basename(local_file_path)} → {product.name}')
            return True
        
        try:
            with open(local_file_path, 'rb') as f:
                django_file = File(f, name=os.path.basename(azure_path))
                
                # Save to Azure
                saved_path = default_storage.save(azure_path, django_file)
                
                # Create product image record
                content_type = ContentType.objects.get_for_model(product.__class__)
                VdlProductImage.objects.create(
                    content_type=content_type,
                    object_id=product.id,
                    image=saved_path,
                    alt_text=product.name
                )
                
                self.stdout.write(self.style.SUCCESS(f'✅ Created product image: {os.path.basename(local_file_path)} → {product.name}'))
                return True
                
        except Exception as e:
            self.stderr.write(self.style.ERROR(f'❌ Failed to create product image {local_file_path}: {e}'))
            return False