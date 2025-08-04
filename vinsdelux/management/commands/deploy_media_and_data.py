import os
import logging
from django.core.management.base import BaseCommand
from django.conf import settings
from django.core.files.storage import default_storage
from django.core.files import File
from vinsdelux.models import VdlProducer, VdlCoffret, VdlAdoptionPlan, VdlProductImage, HomepageContent, VdlCategory
from django.contrib.contenttypes.models import ContentType
from pathlib import Path

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Complete deployment: uploads media, creates data, and maps everything together'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be done without actually doing it',
        )
        parser.add_argument(
            '--force-refresh',
            action='store_true',
            help='Clear existing data and start fresh',
        )

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS('🚀 Starting complete deployment with media and data...'))
        
        # Find local media directory
        possible_media_paths = [
            os.path.join(settings.BASE_DIR, 'media'),
            '/home/site/wwwroot/media',
            os.path.join(os.getcwd(), 'media'),
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
        
        # Analyze media structure
        media_inventory = self._analyze_media_structure(media_root)
        self._print_media_inventory(media_inventory)
        
        if options['dry_run']:
            self.stdout.write(self.style.SUCCESS('🔍 Dry run completed - showing what would be deployed'))
            return
        
        # Clear existing data if requested
        if options['force_refresh']:
            self._clear_existing_data()
        
        # Create/update data with media mapping
        created_count = self._create_data_with_media(media_inventory, media_root)
        
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('📊 Deployment Summary:'))
        self.stdout.write(f'   ✅ Data records created/updated: {created_count}')
        self.stdout.write(f'   📸 Media files uploaded to Azure Blob Storage')
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('🎉 Complete deployment finished! Your Vins de Lux app is ready.'))

    def _analyze_media_structure(self, media_root):
        """Analyze the media directory structure"""
        inventory = {
            'homepage': [],
            'producer_photos': [],
            'producer_logos': [],
            'wine_bottles': [],
            'product_gallery': [],
            'product_main': []
        }
        
        # Homepage images
        homepage_path = os.path.join(media_root, 'homepage')
        if os.path.exists(homepage_path):
            for file in os.listdir(homepage_path):
                if file.lower().endswith(('.png', '.jpg', '.jpeg')):
                    inventory['homepage'].append(os.path.join(homepage_path, file))
        
        # Producer photos
        producer_photos_path = os.path.join(media_root, 'producers', 'photos')
        if os.path.exists(producer_photos_path):
            for file in sorted(os.listdir(producer_photos_path)):
                if file.lower().endswith(('.png', '.jpg', '.jpeg')):
                    inventory['producer_photos'].append(os.path.join(producer_photos_path, file))
        
        # Producer logos
        producer_logos_path = os.path.join(media_root, 'producers', 'logos')
        if os.path.exists(producer_logos_path):
            for file in sorted(os.listdir(producer_logos_path)):
                if file.lower().endswith(('.png', '.jpg', '.jpeg')):
                    inventory['producer_logos'].append(os.path.join(producer_logos_path, file))
        
        # Wine bottles
        winebottles_path = os.path.join(media_root, 'products', 'winebottles')
        if os.path.exists(winebottles_path):
            for file in sorted(os.listdir(winebottles_path)):
                if file.lower().endswith(('.png', '.jpg', '.jpeg')):
                    inventory['wine_bottles'].append(os.path.join(winebottles_path, file))
        
        # Product gallery
        gallery_path = os.path.join(media_root, 'products', 'gallery')
        if os.path.exists(gallery_path):
            for file in sorted(os.listdir(gallery_path)):
                if file.lower().endswith(('.png', '.jpg', '.jpeg')):
                    inventory['product_gallery'].append(os.path.join(gallery_path, file))
        
        # Product main
        main_path = os.path.join(media_root, 'products', 'main')
        if os.path.exists(main_path):
            for file in sorted(os.listdir(main_path)):
                if file.lower().endswith(('.png', '.jpg', '.jpeg')):
                    inventory['product_main'].append(os.path.join(main_path, file))
        
        return inventory

    def _print_media_inventory(self, inventory):
        """Print what media files were found"""
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('📊 Media Inventory:'))
        self.stdout.write(f'   🏠 Homepage images: {len(inventory["homepage"])}')
        self.stdout.write(f'   👤 Producer photos: {len(inventory["producer_photos"])}')
        self.stdout.write(f'   🏢 Producer logos: {len(inventory["producer_logos"])}')
        self.stdout.write(f'   🍷 Wine bottles: {len(inventory["wine_bottles"])}')
        self.stdout.write(f'   📸 Product gallery: {len(inventory["product_gallery"])}')
        self.stdout.write(f'   🖼️  Product main: {len(inventory["product_main"])}')

    def _clear_existing_data(self):
        """Clear existing sample data"""
        self.stdout.write('🗑️  Clearing existing data...')
        VdlProductImage.objects.all().delete()
        VdlAdoptionPlan.objects.all().delete()
        VdlCoffret.objects.all().delete()
        VdlProducer.objects.all().delete()
        HomepageContent.objects.all().delete()

    def _create_data_with_media(self, media_inventory, media_root):
        """Create all data with proper media mapping"""
        created_count = 0
        
        # Create homepage content
        if not HomepageContent.objects.exists() and media_inventory['homepage']:
            homepage_content = HomepageContent.objects.create(
                hero_title="Discover Premium Wine Experiences",
                hero_subtitle="Adopt a vine, support artisan producers, and enjoy exclusive wine collections delivered to your door."
            )
            
            # Add hero background image
            hero_image_path = media_inventory['homepage'][0]  # Use first available
            if self._upload_and_set_image(hero_image_path, homepage_content, 'hero_background_image', 'homepage/hero-background.jpg'):
                created_count += 1
                self.stdout.write(self.style.SUCCESS('✅ Created homepage content with background image'))

        # Create producers with images
        producer_data = [
            {'name': 'Château Margaux', 'region': 'Bordeaux, France', 'desc': 'One of the most prestigious wine estates in Bordeaux.', 'featured': True},
            {'name': 'Domaine de la Romanée-Conti', 'region': 'Burgundy, France', 'desc': 'The most famous wine producer in Burgundy.', 'featured': True},
            {'name': 'Penfolds', 'region': 'South Australia', 'desc': 'An iconic Australian winery with rich history.', 'featured': True},
            {'name': 'Antinori', 'region': 'Tuscany, Italy', 'desc': 'A renowned Italian wine producer with centuries of tradition.', 'featured': True},
            {'name': 'Catena Zapata', 'region': 'Mendoza, Argentina', 'desc': 'A leading Argentine winery known for high-altitude vineyards.', 'featured': False},
        ]

        producers = []
        for i, prod_data in enumerate(producer_data):
            slug = prod_data['name'].lower().replace(' ', '-').replace('é', 'e')
            
            producer, created = VdlProducer.objects.get_or_create(
                slug=slug,
                defaults={
                    'name': prod_data['name'],
                    'description': prod_data['desc'],
                    'region': prod_data['region'],
                    'is_featured_on_homepage': prod_data['featured'],
                }
            )
            
            if created:
                # Add logo if available
                if i < len(media_inventory['producer_logos']):
                    logo_path = media_inventory['producer_logos'][i]
                    azure_path = f'producers/logos/producer_{i+1}_logo{Path(logo_path).suffix}'
                    if self._upload_and_set_image(logo_path, producer, 'logo', azure_path):
                        pass
                
                # Add photo if available
                if i < len(media_inventory['producer_photos']):
                    photo_path = media_inventory['producer_photos'][i]
                    azure_path = f'producers/photos/producer_{i+1}_photo{Path(photo_path).suffix}'
                    if self._upload_and_set_image(photo_path, producer, 'producer_photo', azure_path):
                        pass
                
                created_count += 1
                self.stdout.write(self.style.SUCCESS(f'✅ Created producer: {producer.name}'))
            
            producers.append(producer)

        # Create coffrets with images
        coffret_data = [
            {'name': 'The Bordeaux Collection', 'price': 250.00, 'desc': 'A curated selection of top Bordeaux wines.'},
            {'name': 'Burgundy Discovery Set', 'price': 350.00, 'desc': 'Discover the elegance and complexity of Burgundy.'},
            {'name': 'Australian Shiraz Showcase', 'price': 200.00, 'desc': 'Experience the bold flavors of Australian Shiraz.'},
            {'name': 'Tuscan Classics', 'price': 300.00, 'desc': 'A journey through the heart of Tuscan winemaking.'},
            {'name': 'Argentine Malbec Selection', 'price': 220.00, 'desc': 'Explore the rich and fruity Malbecs of Argentina.'},
        ]

        coffrets = []
        available_wine_images = media_inventory['wine_bottles'].copy()
        
        for i, coffret_info in enumerate(coffret_data):
            slug = coffret_info['name'].lower().replace(' ', '-')
            
            coffret, created = VdlCoffret.objects.get_or_create(
                slug=slug,
                defaults={
                    'name': coffret_info['name'],
                    'price': coffret_info['price'],
                    'short_description': coffret_info['desc'],
                    'full_description': f"{coffret_info['desc']} Each bottle is carefully selected to represent the best of the region.",
                    'producer': producers[i] if i < len(producers) else producers[0],
                    'stock_quantity': 10,
                }
            )
            
            if created and available_wine_images:
                # Add product image
                wine_image_path = available_wine_images.pop(0)
                azure_path = f'products/gallery/coffret_{i+1}{Path(wine_image_path).suffix}'
                
                # Create product image record
                content_type = ContentType.objects.get_for_model(VdlCoffret)
                if self._create_product_image_record(wine_image_path, content_type, coffret.id, azure_path, coffret.name):
                    pass
                
                created_count += 1
                self.stdout.write(self.style.SUCCESS(f'✅ Created coffret: {coffret.name}'))
            
            coffrets.append(coffret)

        # Create adoption plans with images
        plan_data = [
            {'name': 'Bordeaux Adoption Plan', 'price': 500.00, 'desc': 'Adopt a vine in Bordeaux and receive exclusive benefits.'},
            {'name': 'Burgundy Adoption Plan', 'price': 700.00, 'desc': 'Become a part of the Burgundy winemaking tradition.'},
            {'name': 'Shiraz Adoption Plan', 'price': 400.00, 'desc': 'Experience the best of Australian Shiraz.'},
            {'name': 'Tuscan Adoption Plan', 'price': 600.00, 'desc': 'Support a Tuscan vineyard and enjoy exquisite wines.'},
            {'name': 'Malbec Adoption Plan', 'price': 440.00, 'desc': 'Embrace the Argentine Malbec culture.'},
        ]

        for i, plan_info in enumerate(plan_data):
            slug = plan_info['name'].lower().replace(' ', '-')
            
            plan, created = VdlAdoptionPlan.objects.get_or_create(
                slug=slug,
                defaults={
                    'name': plan_info['name'],
                    'price': plan_info['price'],
                    'short_description': plan_info['desc'],
                    'full_description': f"{plan_info['desc']} Includes seasonal coffrets, vineyard visits, and exclusive member benefits.",
                    'associated_coffret': coffrets[i] if i < len(coffrets) else coffrets[0],
                    'producer': producers[i] if i < len(producers) else producers[0],
                    'duration_months': 12,
                    'coffrets_per_year': 2,
                    'includes_visit': True,
                    'visit_details': 'A guided tour of the vineyard and a tasting session.',
                    'includes_medallion': True,
                    'includes_club_membership': True,
                }
            )
            
            if created and available_wine_images:
                # Add adoption plan image
                wine_image_path = available_wine_images.pop(0)
                azure_path = f'products/gallery/plan_{i+1}{Path(wine_image_path).suffix}'
                
                # Create product image record
                content_type = ContentType.objects.get_for_model(VdlAdoptionPlan)
                if self._create_product_image_record(wine_image_path, content_type, plan.id, azure_path, plan.name):
                    pass
                
                created_count += 1
                self.stdout.write(self.style.SUCCESS(f'✅ Created adoption plan: {plan.name}'))

        return created_count

    def _upload_and_set_image(self, local_path, instance, field_name, azure_path):
        """Upload image to Azure and set model field"""
        try:
            with open(local_path, 'rb') as f:
                django_file = File(f, name=os.path.basename(azure_path))
                saved_path = default_storage.save(azure_path, django_file)
                setattr(instance, field_name, saved_path)
                instance.save(update_fields=[field_name])
                return True
        except Exception as e:
            self.stderr.write(self.style.ERROR(f'❌ Failed to upload {local_path}: {e}'))
            return False

    def _create_product_image_record(self, local_path, content_type, object_id, azure_path, alt_text):
        """Create product image record with Azure upload"""
        try:
            with open(local_path, 'rb') as f:
                django_file = File(f, name=os.path.basename(azure_path))
                saved_path = default_storage.save(azure_path, django_file)
                
                VdlProductImage.objects.create(
                    content_type=content_type,
                    object_id=object_id,
                    image=saved_path,
                    alt_text=alt_text
                )
                return True
        except Exception as e:
            self.stderr.write(self.style.ERROR(f'❌ Failed to create product image {local_path}: {e}'))
            return False